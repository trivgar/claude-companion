"""Periodically terminate abandoned Claude Code snapshot-shell jobs.

The worker considers only snapshot shells whose Claude owner has disappeared.
It checks that condition again immediately before signalling, so an active
Claude process is never deliberately interrupted.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time

from . import bgjobs, config

DEFAULT_EVERY_MIN = 10
DEFAULT_AFTER_MIN = 60
LOG_NAME = "job-reaper.jsonl"


def _minutes(key: str, default: int) -> int:
    try:
        value = int(config.get(key, default))
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def every_s() -> int:
    """The interval between reaper passes.  Zero disables the worker."""
    return _minutes("job_reaper_every_min", DEFAULT_EVERY_MIN) * 60


def after_s() -> int:
    """The minimum age of an orphan snapshot shell before it is terminated."""
    return _minutes("job_reaper_after_min", DEFAULT_AFTER_MIN) * 60


def data_dir() -> Path:
    raw = config.get("data_dir") or Path.home() / ".local" / "share" / "claude-companion"
    return Path(str(raw)).expanduser()


def log_path(directory: Path | None = None) -> Path:
    return Path(directory or data_dir()) / LOG_NAME


def is_claude(proc: str, pid: int) -> bool:
    """Whether a process is a live Claude Code launcher."""
    try:
        with open(f"{proc}/{pid}/comm") as fh:
            if fh.read().strip().lower() == "claude":
                return True
    except OSError:
        pass
    command = bgjobs.argv(proc, pid)
    if not command:
        return False
    name = os.path.basename(command[0]).lower()
    if name == "claude":
        return True
    return name in {"node", "nodejs"} and any("claude" in part.lower() for part in command[1:])


def has_claude_ancestor(proc: str, pid: int) -> bool:
    """Whether *pid* or any current ancestor is a live Claude process."""
    seen: set[int] = set()
    current = int(pid)
    for _ in range(64):
        if current <= 1 or current in seen:
            return False
        seen.add(current)
        if is_claude(proc, current):
            return True
        details = bgjobs.stat(proc, current)
        if not details:
            return False
        current = details[0]
    return True                 # unknown ancestry is not safe to reap


def _snapshot(proc: str, pid: int) -> tuple[int, int, list[str]] | None:
    details = bgjobs.stat(proc, pid)
    if not details:
        return None
    parent, started = details
    command = bgjobs.argv(proc, pid)
    if not bgjobs.is_snapshot_shell(command):
        return None
    return parent, started, command


def stuck(proc: str = "/proc", now: float | None = None,
          minimum_age_s: int | None = None) -> list[dict]:
    """Return aged snapshot shells whose Claude owner no longer exists."""
    try:
        moment = time.time() if now is None else float(now)
        minimum = after_s() if minimum_age_s is None else max(0, int(minimum_age_s))
        boot = bgjobs.boot_time(proc)
        ticks = bgjobs._clock_ticks()
        if boot is None:
            return []
        rows = []
        for name in os.listdir(proc):
            if not name.isdigit():
                continue
            pid = int(name)
            candidate = _snapshot(proc, pid)
            if not candidate:
                continue
            parent, started, command = candidate
            # A nested shell belongs to the outer snapshot-shell job.
            if bgjobs.is_snapshot_shell(bgjobs.argv(proc, parent)):
                continue
            # Do not signal a shell while its direct or indirect Claude owner
            # is alive.  The ancestor walk is deliberately conservative.
            if has_claude_ancestor(proc, parent):
                continue
            age = bgjobs._age(started, boot, moment, ticks)
            if age is None or age < minimum:
                continue
            rows.append({"pid": pid, "ppid": parent, "start_ticks": started,
                         "age_s": age, "cmd": bgjobs.command_of(command, 160)})
        return sorted(rows, key=lambda row: (-row["age_s"], row["pid"]))
    except (OSError, ValueError):
        return []


def _same_process(proc: str, pid: int, start_ticks: int) -> bool:
    details = bgjobs.stat(proc, pid)
    return bool(details and details[1] == start_ticks)


def _tree(proc: str, root: int) -> list[tuple[int, int]]:
    """The current process subtree in children-first order, with identities."""
    try:
        children: dict[int, list[int]] = {}
        starts: dict[int, int] = {}
        for name in os.listdir(proc):
            if not name.isdigit():
                continue
            pid = int(name)
            details = bgjobs.stat(proc, pid)
            if details:
                children.setdefault(details[0], []).append(pid)
                starts[pid] = details[1]
    except OSError:
        return []
    for group in children.values():
        group.sort()
    found: list[tuple[int, int]] = []
    todo: list[tuple[int, bool]] = [(root, False)]
    seen: set[int] = set()
    while todo:
        pid, visited = todo.pop()
        if pid in seen and not visited:
            continue
        if visited:
            if pid in starts:
                found.append((pid, starts[pid]))
            continue
        seen.add(pid)
        # A command can itself launch another Claude process.  Its descendants
        # are a separate live session, never part of an abandoned shell tree.
        if pid != root and is_claude(proc, pid):
            continue
        todo.append((pid, True))
        todo.extend((child, False) for child in reversed(children.get(pid, [])))
    return found


def kill(row: dict, proc: str = "/proc", *, signal_fn=None,
         sleep_fn=None, grace_s: float = 1.0, own_pid: int | None = None) -> list[int]:
    """Terminate an orphan snapshot-shell tree and return signalled pids.

    Identity checks avoid signalling a recycled pid.  The root is checked for
    a live Claude ancestor before each signal pass.
    """
    try:
        root = int(row["pid"])
        start = int(row["start_ticks"])
    except (KeyError, TypeError, ValueError):
        return []
    if not _same_process(proc, root, start) or has_claude_ancestor(proc, root):
        return []
    signal_fn = os.kill if signal_fn is None else signal_fn
    sleep_fn = time.sleep if sleep_fn is None else sleep_fn
    mine = os.getpid() if own_pid is None else own_pid
    targets = [(pid, ticks) for pid, ticks in _tree(proc, root)
               if pid not in {1, mine} and not is_claude(proc, pid)]
    sent: list[int] = []
    for pid, ticks in targets:
        if not _same_process(proc, root, start) or has_claude_ancestor(proc, root):
            return sent
        if not _same_process(proc, pid, ticks):
            continue
        try:
            signal_fn(pid, signal.SIGTERM)
            sent.append(pid)
        except OSError:
            pass
    if not sent:
        return []
    try:
        sleep_fn(max(0.0, float(grace_s)))
    except (TypeError, ValueError):
        pass
    if not _same_process(proc, root, start) or has_claude_ancestor(proc, root):
        return sent
    for pid, ticks in targets:
        if not _same_process(proc, pid, ticks):
            continue
        try:
            signal_fn(pid, signal.SIGKILL)
        except OSError:
            pass
    return sent


def log_kill(row: dict, directory: Path | None = None, now: float | None = None) -> None:
    """Append one JSONL audit record.  Logging errors never stop reaping."""
    try:
        when = time.time() if now is None else float(now)
        record = {"at": datetime.fromtimestamp(when, timezone.utc).isoformat(timespec="seconds"),
                  "pid": int(row["pid"]), "ppid": int(row["ppid"]),
                  "age_s": int(row["age_s"]), "cmd": str(row["cmd"])[:160]}
        path = log_path(directory)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, (json.dumps(record, sort_keys=True) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
    except (KeyError, OSError, TypeError, ValueError):
        pass


def reap(proc: str = "/proc", directory: Path | None = None, now: float | None = None,
         minimum_age_s: int | None = None, *, signal_fn=None, sleep_fn=None) -> list[dict]:
    """Kill and audit every currently eligible orphan snapshot-shell job."""
    reaped = []
    for row in stuck(proc, now, minimum_age_s):
        sent = kill(row, proc, signal_fn=signal_fn, sleep_fn=sleep_fn)
        if sent:
            log_kill(row, directory, now)
            reaped.append(row)
    return reaped


class Watcher:
    """One daemon worker that reaps immediately and then at the configured rate."""
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def tick(self) -> list[dict]:
        return reap(directory=self.directory)

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            self.tick()
            interval = every_s()
            if interval <= 0:
                return
            self.stop_event.wait(interval)

    def start(self) -> None:
        if "pytest" in sys.modules or every_s() <= 0:
            return
        self.thread = threading.Thread(target=self._loop, name="job-reaper", daemon=True)
        self.thread.start()


def start(directory: Path | None = None) -> Watcher | None:
    """Start the configured worker outside tests, returning it when enabled."""
    if "pytest" in sys.modules or every_s() <= 0:
        return None
    watcher = Watcher(directory)
    watcher.start()
    return watcher
