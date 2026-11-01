"""Give Claude Code sessions the deferred handoffs that concern their project.

This hook is safe to run for both SessionStart and UserPromptSubmit.  A small
per-session marker records the backlog file's mtime and size, so prompt hooks
do no parsing or output until the shared file has changed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

from .. import deferred


def _session_name(value: object, cwd: str) -> str:
    raw = re.sub(r"[^A-Za-z0-9_-]", "", str(value or ""))[:80]
    if raw:
        return raw
    return hashlib.sha256(cwd.encode()).hexdigest()[:32]


def _stamp(path: Path) -> str | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def _marker(path: Path, session: str) -> Path:
    return path / session


def _unchanged(path: Path, session: str, stamp: str | None) -> bool:
    if stamp is None:
        return True
    try:
        return _marker(path, session).read_text(encoding="utf-8") == stamp
    except OSError:
        return False


def _mark(path: Path, session: str, stamp: str | None) -> None:
    if stamp is None:
        return
    try:
        path.mkdir(parents=True, exist_ok=True)
        marker = _marker(path, session)
        tmp = marker.with_name(marker.name + ".tmp")
        tmp.write_text(stamp, encoding="utf-8")
        os.replace(tmp, marker)
    except OSError:
        pass


def brief(entries: list[dict], project: str) -> str:
    """The context relevant to a project's current session, or nothing."""
    open_for_me = [entry for entry in entries
                   if entry.get("for") == project and entry.get("state", "open") == "open"]
    answered_for_me = [entry for entry in entries
                       if entry.get("project") == project and entry.get("for")
                       and entry.get("state") == "answered"]
    if not open_for_me and not answered_for_me:
        return ""
    lines = ["Deferred handoffs:"]
    if open_for_me:
        lines.append(f"Open requests for {project}:")
        for entry in open_for_me:
            source = entry.get("by") or entry.get("project") or "another session"
            text = entry.get("ask") or entry.get("header") or ""
            when = f" Pick up when: {entry['when']}." if entry.get("when") else ""
            lines.append(f"- {entry.get('header', '')} (from {source}): {text}.{when}")
        lines.append('After completing one, record the result with `python3 -m agent_companion.deferred --answer "<header>" "<answer>"`.')
    if answered_for_me:
        lines.append(f"Answered requests from {project}:")
        for entry in answered_for_me:
            lines.append(f"- {entry.get('header', '')} (for {entry.get('for', '')}): {entry.get('answer', '')}")
        lines.append('After using an answer, remove it with `python3 -m agent_companion.deferred --done "<header>"`.')
    return "\n".join(lines)


def run(payload: dict | None = None) -> str:
    """Return JSON hook output, or an empty string when there is no update."""
    payload = payload if isinstance(payload, dict) else {}
    cwd = str(payload.get("cwd") or os.getcwd())
    source = deferred.file_path()
    state_dir = deferred.hook_state_dir()
    session = _session_name(payload.get("session_id"), cwd)
    stamp = _stamp(source)
    event = str(payload.get("hook_event_name") or "")
    # A session that starts, resumes or was cleared has lost what it was told,
    # so only prompt hooks skip an unchanged file.
    if event != "SessionStart" and _unchanged(state_dir, session, stamp):
        return ""
    entries = deferred.read(source)
    project = deferred.project_of(cwd)
    context = brief(entries, project)
    _mark(state_dir, session, stamp)
    if not context:
        return ""
    return json.dumps({"hookSpecificOutput": {"hookEventName": event,
                                               "additionalContext": context}})


def main() -> int:
    try:
        try:
            payload = json.load(sys.stdin)
        except (ValueError, OSError):
            payload = {}
        output = run(payload)
        if output:
            print(output)
    except Exception:
        # Claude Code hooks must never make a prompt fail because their local
        # shared backlog cannot be read.
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
