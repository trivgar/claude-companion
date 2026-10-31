"""Save live Claude Code sessions and start them again after an interruption.

The snapshot is deliberately small: it contains only the tmux session name,
working directory, Claude Code session id, and model.  It is local state, not
a transcript, and is written with mode 600 into the configured app data
directory.

    python3 -m claude_companion.restore snapshot
    python3 -m claude_companion.restore restore
    python3 -m claude_companion.restore install-timer
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import config
from .reader import SessionReader
from .registry import live_sessions

SNAPSHOT_NAME = "restore-snapshot.json"
CRASH_SNAPSHOT_NAME = "restore-crash-snapshot.json"
SERVICE_NAME = "claude-companion-restore-snapshot.service"
TIMER_NAME = "claude-companion-restore-snapshot.timer"
CRASH_SESSION_COUNT = 2
CRASH_GRACE_S = 4 * 60 * 60
COMMAND_TIMEOUT_S = 5

_restore_lock = threading.Lock()


def claude_home() -> Path:
    """The Claude Code home, from the same setting as the sessions view."""
    return Path(str(config.get("claude_home") or Path.home() / ".claude")).expanduser()


def data_dir() -> Path:
    """Where local companion state, including restore snapshots, lives."""
    return Path(str(config.get("data_dir")
                    or Path.home() / ".local" / "share" / "claude-companion")).expanduser()


def snapshot_path(directory: Path | None = None) -> Path:
    return Path(directory or data_dir()) / SNAPSHOT_NAME


def crash_snapshot_path(directory: Path | None = None) -> Path:
    return Path(directory or data_dir()) / CRASH_SNAPSHOT_NAME


def _tmux_session(value: object) -> str:
    """The session portion of a registry target such as ``work:@1.%2``."""
    value = str(value or "").strip()
    name = value.split(":", 1)[0]
    if not name or len(name) > 200 or any(c in name for c in "\x00\r\n:"):
        return ""
    return name


def _safe_text(value: object, limit: int = 4096) -> str:
    value = str(value or "")
    if len(value) > limit or "\x00" in value or "\r" in value or "\n" in value:
        return ""
    return value


def _stored_session(value: object) -> dict | None:
    """Accept only snapshot fields that are safe to pass to fixed commands."""
    if not isinstance(value, dict):
        return None
    tmux = _tmux_session(value.get("tmux"))
    cwd = _safe_text(value.get("cwd"))
    session_id = _safe_text(value.get("session_id"), 512)
    if not tmux or not cwd or not Path(cwd).is_absolute() or not session_id:
        return None
    return {"tmux": tmux, "cwd": cwd, "session_id": session_id,
            "model": _safe_text(value.get("model"), 512)}


def _read(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or not isinstance(value.get("sessions"), list):
        return None
    return value


def _atomic_json(path: Path, value: dict) -> None:
    """Replace a private JSON file without exposing a partial snapshot."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, sort_keys=True, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _model_rows(home: Path) -> dict[tuple[int, str], dict]:
    """The registry has no model; the incremental reader does when it can."""
    try:
        rows = SessionReader(home).snapshot()
    except Exception:
        return {}
    return {(int(row["pid"]), str(row.get("session_id") or "")): row
            for row in rows if isinstance(row, dict) and isinstance(row.get("pid"), int)}


def live_snapshot_sessions(home: Path | None = None) -> list[dict]:
    """The restoreable, top-level live sessions in stable tmux-name order."""
    home = Path(home or claude_home())
    details = _model_rows(home)
    out: dict[str, dict] = {}
    for row in live_sessions(home / "sessions"):
        key = (int(row["pid"]), str(row.get("session_id") or ""))
        detail = details.get(key, {})
        # A child normally shares its parent's tmux session. Resuming it on
        # its own would duplicate work, so only the session that owns a pane
        # is saved.
        if detail.get("child"):
            continue
        tmux = _tmux_session(row.get("tmux"))
        saved = _stored_session({"tmux": tmux, "cwd": row.get("cwd"),
                                 "session_id": row.get("session_id"),
                                 "model": detail.get("model") or ""})
        if saved is not None:
            out.setdefault(tmux, saved)
    return [out[name] for name in sorted(out)]


def snapshot(home: Path | None = None, directory: Path | None = None,
             now: float | None = None) -> dict:
    """Atomically save current sessions and preserve a sharp all-session loss.

    A regular empty snapshot is still useful, but if it immediately follows a
    snapshot of several sessions we also retain that larger list for a short
    crash-recovery window.  The current snapshot always wins duplicate tmux
    names when status is later assembled.
    """
    now = time.time() if now is None else now
    path = snapshot_path(directory)
    previous = _read(path)
    sessions = live_snapshot_sessions(home)
    saved = {"version": 1, "taken_at": now, "sessions": sessions}
    crash_saved = False
    if not sessions and previous is not None:
        prior_rows = [row for row in previous.get("sessions", []) if _stored_session(row)]
        prior_taken = previous.get("taken_at")
        try:
            recent = 0 <= now - float(prior_taken) <= CRASH_GRACE_S
        except (TypeError, ValueError):
            recent = False
        if len(prior_rows) >= CRASH_SESSION_COUNT and recent:
            _atomic_json(crash_snapshot_path(directory), previous)
            crash_saved = True
    _atomic_json(path, saved)
    return {"ok": True, "path": str(path), "sessions": len(sessions),
            "taken_at": now, "crash_saved": crash_saved}


def _fresh_crash(value: dict | None, now: float) -> bool:
    if value is None:
        return False
    try:
        return 0 <= now - float(value.get("taken_at")) <= CRASH_GRACE_S
    except (TypeError, ValueError):
        return False


def _snapshot_rows(directory: Path | None = None, now: float | None = None) -> tuple[dict | None, list[dict]]:
    """Current rows plus a fresh sharp-crash backup, one per tmux name."""
    now = time.time() if now is None else now
    current = _read(snapshot_path(directory))
    crash = _read(crash_snapshot_path(directory))
    rows: dict[str, dict] = {}
    # The current snapshot takes precedence. A crash backup fills in sessions
    # that vanished together, but cannot replace an active current record.
    for source in (current, crash if _fresh_crash(crash, now) else None):
        for raw in (source or {}).get("sessions", []):
            row = _stored_session(raw)
            if row is not None:
                rows.setdefault(row["tmux"], row)
    return current or (crash if _fresh_crash(crash, now) else None), [rows[k] for k in sorted(rows)]


def running_tmux_sessions(run=subprocess.run) -> set[str] | None:
    """Tmux session names, or ``None`` if tmux could not answer safely."""
    try:
        result = run(["tmux", "list-sessions", "-F", "#{session_name}"],
                     capture_output=True, text=True, timeout=COMMAND_TIMEOUT_S,
                     stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode == 0:
        return {_tmux_session(line) for line in result.stdout.splitlines()
                if _tmux_session(line)}
    # tmux reports a missing server with a nonzero exit code. That is the
    # normal post-reboot state; other errors are not a safe basis for restore.
    error = str(getattr(result, "stderr", "") or "").lower()
    if "no server running" in error or "error connecting" in error:
        return set()
    return None


def _rows_with_state(rows: list[dict], running: set[str]) -> list[dict]:
    out = []
    for row in rows:
        try:
            gone = not Path(row["cwd"]).is_dir()
        except OSError:
            gone = True
        out.append({**row, "running": row["tmux"] in running, "gone": gone})
    return out


def status(directory: Path | None = None, now: float | None = None,
           run=subprocess.run) -> dict:
    """What a dashboard can offer to restore, without starting anything."""
    head, rows = _snapshot_rows(directory, now)
    if head is None:
        return {"ok": None, "detail": "No session snapshot yet.", "missing": 0, "sessions": []}
    running = running_tmux_sessions(run)
    if running is None:
        return {"ok": False, "detail": "tmux did not answer.", "missing": 0, "sessions": []}
    sessions = _rows_with_state(rows, running)
    missing = sum(not row["running"] and not row["gone"] for row in sessions)
    return {"ok": True, "taken_at": head.get("taken_at"), "missing": missing,
            "sessions": sessions}


def _start(row: dict, run, systemd_run: str | None, unit: str) -> tuple[bool, str]:
    command = ["tmux", "new-session", "-d", "-s", row["tmux"], "-c", row["cwd"],
               shlex.join(["claude", "--resume", row["session_id"]])]
    scoped = ([systemd_run, "--user", "--scope", "--quiet", "--collect", f"--unit={unit}", *command]
              if systemd_run else command)
    try:
        result = run(scoped, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_S,
                     stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False, "could not start tmux"
    if result.returncode == 0:
        return True, "started"
    # A binary can exist even when this login does not have a user systemd
    # manager. In that case direct tmux is the safe equivalent.
    if systemd_run:
        try:
            result = run(command, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_S,
                         stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            return False, "could not start tmux"
        if result.returncode == 0:
            return True, "started"
    return False, "tmux refused to start the session"


def restore(directory: Path | None = None, now: float | None = None,
            run=subprocess.run, which=None) -> dict:
    """Restore missing snapshots one by one and report every session's result."""
    if not _restore_lock.acquire(blocking=False):
        return {"ok": False, "detail": "A restore is already under way.", "sessions": []}
    try:
        head, rows = _snapshot_rows(directory, now)
        if head is None:
            return {"ok": False, "detail": "No session snapshot yet.", "sessions": []}
        running = running_tmux_sessions(run)
        if running is None:
            return {"ok": False, "detail": "tmux did not answer.", "sessions": []}
        systemd_run = (which or shutil.which)("systemd-run")
        reports = []
        restored = 0
        stamp = int(time.time() if now is None else now)
        for index, item in enumerate(_rows_with_state(rows, running), 1):
            report = {"tmux": item["tmux"]}
            if item["running"]:
                report.update(ok=True, skipped=True, detail="already running")
            elif item["gone"]:
                report.update(ok=False, skipped=True, detail="working directory is gone")
            else:
                ok, detail = _start(item, run, systemd_run,
                                    f"claude-companion-restore-{stamp}-{index}")
                report.update(ok=ok, skipped=False, detail=detail)
                if ok:
                    restored += 1
                    running.add(item["tmux"])
            reports.append(report)
        return {"ok": True, "restored": restored, "sessions": reports}
    finally:
        _restore_lock.release()


def systemd_user_dir() -> Path:
    configured = config.get("systemd_user_dir")
    if configured:
        return Path(str(configured)).expanduser()
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"


def snapshot_interval_min(value: object | None = None) -> int:
    """A conservative, documented timer cadence even for a bad config value."""
    value = config.get("restore_snapshot_interval_min", 5) if value is None else value
    try:
        value = int(value)
    except (TypeError, ValueError):
        return 5
    return value if 1 <= value <= 60 else 5


def _service_text(python: str) -> str:
    return "\n".join([
        "[Unit]",
        "Description=claude-companion: save session restore snapshots",
        "",
        "[Service]",
        "Type=oneshot",
        f"ExecStart={shlex.quote(python)} -m claude_companion.restore snapshot",
        "",
    ])


def _timer_text(interval_min: int) -> str:
    return "\n".join([
        "[Unit]",
        "Description=Save claude-companion session snapshots every few minutes",
        "",
        "[Timer]",
        f"OnUnitActiveSec={interval_min}min",
        "Persistent=true",
        "",
        "[Install]",
        "WantedBy=timers.target",
        "",
    ])


def install_timer(directory: Path | None = None, python: str | None = None,
                  interval_min: object | None = None, run=subprocess.run) -> dict:
    """Install and enable the user timer without changing any Claude settings."""
    directory = Path(directory or systemd_user_dir())
    python = str(python or config.get("restore_python") or sys.executable)
    interval = snapshot_interval_min(interval_min)
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        (directory / SERVICE_NAME).write_text(_service_text(python))
        (directory / TIMER_NAME).write_text(_timer_text(interval))
    except OSError:
        return {"ok": False, "detail": "Could not write the restore timer files."}
    try:
        reload = run(["systemctl", "--user", "daemon-reload"], capture_output=True, text=True,
                     timeout=COMMAND_TIMEOUT_S, stdin=subprocess.DEVNULL)
        enable = run(["systemctl", "--user", "enable", "--now", TIMER_NAME], capture_output=True,
                     text=True, timeout=COMMAND_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return {"ok": False, "detail": "Timer files were written, but systemctl did not answer."}
    if reload.returncode or enable.returncode:
        return {"ok": False, "detail": "Timer files were written, but systemctl refused the timer."}
    return {"ok": True, "directory": str(directory), "interval_min": interval}


def uninstall_timer(directory: Path | None = None, run=subprocess.run) -> dict:
    """Disable and remove only this feature's two user-unit files."""
    directory = Path(directory or systemd_user_dir())
    try:
        disabled = run(["systemctl", "--user", "disable", "--now", TIMER_NAME], capture_output=True,
                       text=True, timeout=COMMAND_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        disabled = None
    removed = []
    for name in (SERVICE_NAME, TIMER_NAME):
        try:
            (directory / name).unlink()
            removed.append(name)
        except FileNotFoundError:
            pass
        except OSError:
            return {"ok": False, "detail": "Could not remove the restore timer files."}
    try:
        reloaded = run(["systemctl", "--user", "daemon-reload"], capture_output=True, text=True,
                       timeout=COMMAND_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        reloaded = None
    if (disabled is not None and disabled.returncode not in (0, 1)) or (reloaded is not None and reloaded.returncode):
        return {"ok": False, "detail": "Timer files were removed, but systemctl reported an error."}
    return {"ok": True, "removed": removed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("snapshot", help="write the current live-session snapshot")
    commands.add_parser("restore", help="start missing sessions from the latest snapshot")
    install = commands.add_parser("install-timer", help="install the systemd user snapshot timer")
    install.add_argument("--systemd-user-dir", type=Path)
    install.add_argument("--interval-min", type=int)
    uninstall = commands.add_parser("uninstall-timer", help="remove the systemd user snapshot timer")
    uninstall.add_argument("--systemd-user-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "snapshot":
            result = snapshot()
        elif args.command == "restore":
            result = restore()
        elif args.command == "install-timer":
            result = install_timer(args.systemd_user_dir, interval_min=args.interval_min)
        else:
            result = uninstall_timer(args.systemd_user_dir)
    except OSError as exc:
        result = {"ok": False, "detail": f"Could not update the snapshot ({type(exc).__name__})."}
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
