"""Read Claude Code background shell jobs from the Linux process tree.

Claude Code starts Bash tool calls through a shell snapshot.  A snapshot shell
that is a direct child of a live Claude process represents one running command,
including any descendants the command may have started.
"""
from __future__ import annotations

import os
import re
import shlex
import time

CMD_LIMIT = 96
_SNAPSHOT = re.compile(r"(?:^|/)shell-snapshots/snapshot-[^\s/]+\.sh")
_EVAL = re.compile(r"\beval\s+('(?:[^']|'\"'\"')*')", re.S)
# Anything after a credential-looking assignment, flag or header is dropped.
# "_" counts as a separator, so GH_TOKEN=... and db_password=... match too.
_SECRET = re.compile(
    r"(?is)((?<![A-Za-z0-9])[A-Za-z0-9_]*(?:token|api[_-]?key|key|password|passwd|secret|auth|credential)"
    r"[A-Za-z0-9_]*\s*(?:=|:)\s*|"
    r"(?:^|\s)--?(?:[A-Za-z0-9-]*(?:token|password|passwd|secret|key|auth|user))(?:\s+|=)|"
    r"(?:^|\s)(?:-H|--header|-u)(?:\s+|=)|"
    r"\b(?:bearer|basic)\s+).*$"
)
_USERINFO = re.compile(r"(://)[^/@\s]+@")


def stat(proc: str, pid: int) -> tuple[int, int] | None:
    """Return a process's parent pid and start ticks, or ``None``.

    ``comm`` is allowed to contain spaces and closing parentheses, so fields
    begin after its final closing parenthesis rather than after a simple split.
    """
    try:
        with open(f"{proc}/{pid}/stat") as fh:
            fields = fh.read().rsplit(")", 1)[1].split()
        return int(fields[1]), int(fields[19])
    except (OSError, ValueError, IndexError):
        return None


def argv(proc: str, pid: int) -> list[str]:
    """Read a process argv without allowing a vanished process to escape."""
    try:
        with open(f"{proc}/{pid}/cmdline", "rb") as fh:
            raw = fh.read()
    except OSError:
        return []
    return [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]


def is_snapshot_shell(command: list[str]) -> bool:
    """Whether *command* is the Bash shell Claude Code created for a tool."""
    return (len(command) >= 3 and os.path.basename(command[0]) in {"bash", "sh"}
            and command[1] == "-c" and bool(_SNAPSHOT.search(command[2])))


def raw_command(command: list[str]) -> str:
    """Return the command passed to the snapshot shell, before display cleanup."""
    script = command[2] if len(command) > 2 else ""
    match = _EVAL.search(script)
    if not match:
        return ""
    try:
        words = shlex.split("eval " + match.group(1))
    except ValueError:
        return ""
    return words[1] if len(words) > 1 else ""


def redact(value: str) -> str:
    """Avoid returning command-line credentials through the dashboard API."""
    value = _USERINFO.sub(r"\1[redacted]@", value)
    return _SECRET.sub(lambda match: match.group(1) + "[redacted]", value)


def command_of(command: list[str], limit: int = CMD_LIMIT) -> str:
    """A one-line, redacted and bounded label for a running shell job."""
    text = redact(" ".join(raw_command(command).split()))
    if not text:
        return "shell job"
    try:
        limit = max(1, int(limit))
    except (TypeError, ValueError):
        limit = CMD_LIMIT
    return text if len(text) <= limit else text[:max(0, limit - 1)] + "…"


def boot_time(proc: str) -> float | None:
    """The system boot epoch supplied by procfs, if available."""
    try:
        with open(f"{proc}/stat") as fh:
            for line in fh:
                if line.startswith("btime "):
                    return float(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


def _clock_ticks() -> int:
    try:
        return int(os.sysconf("SC_CLK_TCK"))
    except (AttributeError, OSError, ValueError):
        return 100


def _age(start_ticks: int, boot: float | None, now: float, ticks: int) -> int | None:
    if boot is None or ticks <= 0:
        return None
    return max(0, int(now - (boot + start_ticks / ticks)))


def scan(pids: set[int], proc: str = "/proc", now: float | None = None) -> dict[int, list[dict]]:
    """Find snapshot-shell children for the supplied live Claude process ids.

    Process listing is read once for every card refresh, even when several
    session cards are visible.  A disappearing or unreadable procfs returns
    empty lists rather than making the Sessions API fail.
    """
    out = {pid: [] for pid in pids}
    if not out:
        return out
    try:
        moment = time.time() if now is None else float(now)
        boot = boot_time(proc)
        ticks = _clock_ticks()
        for name in os.listdir(proc):
            if not name.isdigit():
                continue
            pid = int(name)
            details = stat(proc, pid)
            if not details:
                continue
            parent, started = details
            if parent not in out:
                continue
            command = argv(proc, pid)
            if not is_snapshot_shell(command):
                continue
            out[parent].append({
                "cmd": command_of(command),
                "age_s": _age(started, boot, moment, ticks),
            })
        for jobs in out.values():
            jobs.sort(key=lambda job: job["age_s"] if job["age_s"] is not None else -1,
                      reverse=True)
    except (OSError, ValueError):
        return {pid: [] for pid in pids}
    return out


def jobs_for(pid: int | str | None, proc: str = "/proc", now: float | None = None) -> list[dict]:
    """The current snapshot-shell jobs directly owned by one Claude process."""
    try:
        value = int(pid)  # bools are harmless here and proc has no pid 0 job.
    except (TypeError, ValueError):
        return []
    return scan({value}, proc, now).get(value, [])


def annotate(rows: list[dict], proc: str = "/proc", now: float | None = None) -> None:
    """Add ``bg_jobs`` and ``bg_job_list`` to each public session row."""
    pids: set[int] = set()
    for row in rows:
        try:
            if not row.get("private"):
                pids.add(int(row.get("pid")))
        except (TypeError, ValueError):
            continue
    by_pid = scan(pids, proc, now)
    for row in rows:
        try:
            jobs = [] if row.get("private") else by_pid.get(int(row.get("pid")), [])
        except (TypeError, ValueError):
            jobs = []
        row["bg_jobs"] = len(jobs)
        row["bg_job_list"] = jobs
