"""What an agent has been doing, from the tail of its transcript: each tool
call with what it touched and how long it took, and where the time went.

Only the tail (transcripts reach several MB; nothing here re-reads a whole
one), so it describes a window, and says where the window starts.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

TAIL_BYTES = 2_000_000
MAX_CALLS = 150
GAP_CAP = 600          # a pause longer than this is the user away, not the agent thinking

KINDS = {"Bash": "Bash", "Edit": "Edit", "MultiEdit": "Edit", "Write": "Write",
         "NotebookEdit": "Edit", "Read": "Read", "Grep": "Read", "Glob": "Read",
         "WebFetch": "Web", "WebSearch": "Web", "Agent": "Agents", "Task": "Agents",
         "Workflow": "Agents"}


def _ts(v) -> float | None:
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def kind(name: str) -> str:
    return KINDS.get(name, "Other")


def target(name: str, inp: dict) -> str:
    """What a call touched, in a few words: never its whole input."""
    inp = inp if isinstance(inp, dict) else {}
    for key in ("file_path", "notebook_path", "path"):
        if inp.get(key):
            return Path(str(inp[key])).name[:80]
    if inp.get("command"):
        return str(inp["command"]).strip().splitlines()[0][:80] if str(inp["command"]).strip() else ""
    if inp.get("url"):
        return urlsplit(str(inp["url"])).netloc[:80]
    for key in ("pattern", "query", "description", "skill"):
        if inp.get(key):
            return str(inp[key])[:80]
    return ""


def activity(transcript: Path, tail_bytes: int = TAIL_BYTES) -> dict:
    """{since, calls (newest first), time: {kind: seconds}} for the window."""
    try:
        with open(transcript, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - tail_bytes))
            tail = fh.read().decode("utf-8", "replace")
    except OSError:
        return {"since": None, "calls": [], "time": {}}
    lines = tail.splitlines()
    if size > tail_bytes and lines:
        lines = lines[1:]                     # cut mid-line
    calls: dict[str, dict] = {}
    order: list[str] = []
    time: dict[str, float] = {}
    since = None
    last_input = None                         # a prompt or a result: the agent's turn starts
    for line in lines:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        at = _ts(d.get("timestamp"))
        if at is None:
            continue
        since = at if since is None else since
        msg = d.get("message") if isinstance(d.get("message"), dict) else {}
        content = msg.get("content")
        blocks = content if isinstance(content, list) else []
        if d.get("type") == "assistant":
            if last_input is not None:
                gap = at - last_input
                if 0 < gap <= GAP_CAP:
                    time["Thinking"] = time.get("Thinking", 0.0) + gap
                last_input = None
            for b in blocks:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id"):
                    name = str(b.get("name") or "?")
                    calls[b["id"]] = {"tool": name, "kind": kind(name), "target": target(name, b.get("input")),
                                      "at": at, "seconds": None, "failed": False, "running": True}
                    order.append(b["id"])
        elif d.get("type") == "user":
            results = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
            for b in results:
                c = calls.get(b.get("tool_use_id"))
                if c and c["running"]:
                    c["seconds"] = max(0.0, at - c["at"])
                    c["running"] = False
                    c["failed"] = bool(b.get("is_error"))
                    time[c["kind"]] = time.get(c["kind"], 0.0) + min(c["seconds"], GAP_CAP)
            last_input = at
    newest = [calls[i] for i in reversed(order)][:MAX_CALLS]
    return {"since": since, "calls": newest, "time": {k: round(v, 1) for k, v in time.items()}}
