"""Which pids are actually running, and what /proc says about them.

/proc/<pid>/stat is "pid (comm) state ppid ...". `comm` can contain spaces and
parens, so positional splitting of the raw line silently reads the wrong field.
Strip through the LAST ')' first; start time is then field 20 of what remains
(field 22 of the line, counting from 1).
"""
from __future__ import annotations


def STAT_PATH(pid: int) -> str:
    return f"/proc/{pid}/stat"


def proc_start(pid: int) -> str | None:
    """Process start time in clock ticks since boot, or None if pid is gone."""
    try:
        with open(STAT_PATH(pid), "r") as fh:
            line = fh.read()
    except (OSError, ValueError):
        return None
    try:
        after = line[line.rindex(")") + 1:].split()
        return after[19]
    except (ValueError, IndexError):
        return None


def proc_ppid(pid: int) -> int | None:
    """Parent pid, read with the same strip-through-last-paren rule."""
    try:
        with open(STAT_PATH(pid), "r") as fh:
            line = fh.read()
    except (OSError, ValueError):
        return None
    try:
        after = line[line.rindex(")") + 1:].split()
        return int(after[1])
    except (ValueError, IndexError):
        return None


def ENVIRON_PATH(pid: int) -> str:
    return f"/proc/{pid}/environ"


def proc_environ(pid: int, keys) -> dict[str, str]:
    """The named variables from a process's environment, and nothing else.

    /proc/<pid>/environ is the environment the process was exec'd with, as
    NUL-separated NAME=value entries. It also holds every token and password
    the process was started with, so only the names asked for come back:
    nothing else is decoded, returned, logged or kept. A variable that is
    absent is absent from the answer. A process that is gone, or one this
    user may not read, answers {} rather than raising.
    """
    wanted = {str(k).encode("utf-8", "surrogateescape") for k in keys}
    if not wanted:
        return {}
    try:
        with open(ENVIRON_PATH(pid), "rb") as fh:
            raw = fh.read()
    except (OSError, ValueError):
        return {}
    out: dict[str, str] = {}
    for entry in raw.split(b"\0"):
        name, sep, value = entry.partition(b"=")
        if not sep or name not in wanted:
            continue
        key = name.decode("utf-8", "replace")
        if key not in out:            # the first of a repeated name, as getenv() reads it
            out[key] = value.decode("utf-8", "replace")
    return out


def is_alive(pid: int, proc_start_expected: str | None) -> bool:
    """True only if pid exists AND started when we expect.

    The start-time check is what stops a recycled pid from impersonating a
    session that has already exited.
    """
    if not pid or not proc_start_expected:
        return False
    actual = proc_start(int(pid))
    return actual is not None and str(actual) == str(proc_start_expected)
