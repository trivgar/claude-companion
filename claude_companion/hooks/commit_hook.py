"""Record commits made by a Claude Code session.

Installed as a PostToolUse(Bash) hook. It is deliberately quiet and fails open:
a logging problem must not affect the command that just completed.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from .. import config

SEPARATORS = {";", "&&", "||", "|", "("}


def _tokens(command: str) -> list[str]:
    """Shell words, or no words when an incomplete command cannot be read."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
        lexer.whitespace_split = True
        lexer.commenters = "#"
        return list(lexer)
    except ValueError:
        return []


def _commit_at(tokens: list[str]) -> int | None:
    """The ``git`` word that starts a real commit command, if there is one."""
    for i, word in enumerate(tokens):
        if word != "git" or (i and tokens[i - 1] not in SEPARATORS):
            continue
        j = i + 1
        while j < len(tokens) and tokens[j].startswith("-"):
            if tokens[j] in {"-C", "-c"}:
                j += 1
            j += 1
        if j < len(tokens) and tokens[j] == "commit":
            return i
    return None


def is_commit(command: str) -> bool:
    """Whether a Bash command runs ``git commit``, excluding dry runs."""
    tokens = _tokens(command)
    at = _commit_at(tokens)
    if at is None:
        return False
    for word in tokens[at + 1:]:
        if word in SEPARATORS:
            break
        if word == "--dry-run":
            return False
    return True


def commit_cwd(command: str, cwd: str) -> str:
    """The directory used by a ``git -C DIR commit`` command, or its cwd."""
    tokens = _tokens(command)
    at = _commit_at(tokens)
    if at is None:
        return cwd
    base = Path(cwd)
    j = at + 1
    while j + 1 < len(tokens) and tokens[j].startswith("-"):
        if tokens[j] == "-C":
            target = Path(tokens[j + 1]).expanduser()
            base = target if target.is_absolute() else base / target
            j += 2
        elif tokens[j] == "-c":
            j += 2
        else:
            j += 1
    return str(base)


def log_path() -> Path:
    """Configured commit log location, or the app data directory by default."""
    configured = config.get("commit_log_file")
    if configured:
        return Path(str(configured)).expanduser()
    data_dir = config.get("data_dir") or Path.home() / ".local" / "share" / "claude-companion"
    return Path(str(data_dir)).expanduser() / "commits.jsonl"


def details(cwd: str, run=subprocess.run) -> tuple[str, str] | None:
    """The current commit hash and subject, without asking a shell to parse it."""
    try:
        result = run(["git", "-C", cwd, "log", "-1", "--format=%H%x00%s"],
                     capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or "\0" not in result.stdout:
        return None
    sha, subject = result.stdout.rstrip("\n").split("\0", 1)
    if len(sha) < 7 or any(char not in "0123456789abcdef" for char in sha.lower()):
        return None
    return sha, subject


def append(row: dict, path: Path | None = None) -> None:
    """Append one compact JSON line, safely shared by simultaneous hooks."""
    output = path or log_path()
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        data = (json.dumps(row, separators=(",", ":")) + "\n").encode()
        fd = os.open(output, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
    except OSError:
        pass


def main(stdin=sys.stdin, now: float | None = None, run=subprocess.run) -> int:
    """Read Claude Code hook input, recording only a completed Git commit."""
    try:
        event = json.load(stdin)
        if not isinstance(event, dict) or event.get("tool_name") != "Bash":
            return 0
        tool_input = event.get("tool_input")
        command = tool_input.get("command") if isinstance(tool_input, dict) else None
        if not isinstance(command, str) or not is_commit(command):
            return 0
        cwd = commit_cwd(command, str(event.get("cwd") or os.getcwd()))
        found = details(cwd, run)
        if found is None:
            return 0
        sha, subject = found
        append({"at": time.time() if now is None else now,
                "session_id": str(event.get("session_id") or ""), "cwd": cwd,
                "sha": sha, "subject": subject})
    except (OSError, TypeError, ValueError):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
