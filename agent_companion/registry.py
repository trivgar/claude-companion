"""Read Claude Code's live session registry.

~/.claude/sessions/ accumulates one JSON file per session process and does NOT
clean them up: 78 files against 5 live sessions was the measured ratio here.
Liveness must be filtered, never assumed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from agent_companion.liveness import is_alive


def live_sessions(sessions_dir: Path) -> list[dict]:
    sessions_dir = Path(sessions_dir)
    if not sessions_dir.is_dir():
        return []
    out: list[dict] = []
    for path in sorted(sessions_dir.glob("*.json")):
        try:
            rec = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict):
            continue
        pid = rec.get("pid")
        if not isinstance(pid, int):
            continue
        if not is_alive(pid, rec.get("procStart")):
            continue
        cwd = rec.get("cwd") or ""
        out.append({
            "pid": pid,
            # is_alive has already confirmed this matches /proc, so pid plus
            # proc_start is a stable identity for this exact process.
            "proc_start": str(rec.get("procStart") or ""),
            "session_id": rec.get("sessionId") or "",
            "cwd": cwd,
            "project": os.path.basename(cwd.rstrip("/")) if cwd else "",
            "name": rec.get("name") or "",
            "status": rec.get("status") or "unknown",
            "kind": rec.get("kind") or "",
            "tmux": rec.get("tmux") or "",
            # Remote Control's bridge id, when the session is connected. Maps
            # to https://claude.ai/code/<id>, which opens it in the Claude app.
            "bridge_session_id": rec.get("bridgeSessionId") or "",
            # Who chose the name. "user" means you did; anything else means
            # Claude Code generated one, `my-project-8f`, project plus a
            # short hex. A spawned child gets one, but so does any session
            # started without --name: an SDK host's session is `agent-a6`,
            # "derived", and nobody's child. So it is only reader.py's
            # fallback now. Recorded raw here and interpreted there, because a
            # value this module has never seen should not be guessed at twice.
            "name_source": rec.get("nameSource") or "",
            "entrypoint": rec.get("entrypoint") or "",
            "started_at": rec.get("startedAt") or 0,
        })
    return out
