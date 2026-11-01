"""A session's background work as cards: its workflows and its helper agents.

What Claude Code leaves on disk, under <project>/<session-id>/:

  subagents/agent-<id>.meta.json        a helper: {agentType, description, ...}
  subagents/agent-<id>.jsonl            its transcript (mtime = last write)
  subagents/workflows/<run>/journal.jsonl
                                        a workflow run: one line per agent
                                        "started" {agentId, label, phase}
                                        and "result" {agentId}
  workflows/scripts/<name>-<run>.js     the script, written at launch
  workflows/<run>.json                  the run's record, once it ended:
                                        {workflowName, status, startTime,
                                         durationMs, agentCount, summary}

A workflow's end is recorded, so its state is exact. A helper's is not (a
finished helper's last record looks like one mid-run, see reader.py), so a
helper is "running" while it wrote lately and "done" after: the same rule
as the session cards' helper count.

Read only, and cheap: a journal is parsed again only when its size changes.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

RECENT_S = 120            # a helper or a workflow agent that wrote this lately is working
SHOW_DONE_S = 30 * 60     # finished work stays on the list this long
MAX_ITEMS = 8

_journals: dict[str, tuple[int, list[dict]]] = {}


def _json(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(errors="replace"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _journal(path: Path) -> list[dict]:
    """The journal's agents in start order: {id, label, phase, done}."""
    try:
        size = path.stat().st_size
    except OSError:
        return []
    hit = _journals.get(str(path))
    if hit and hit[0] == size:
        return hit[1]
    agents: dict[str, dict] = {}
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                aid = rec.get("agentId")
                if not aid:
                    continue
                if rec.get("type") == "started":
                    a = agents.setdefault(aid, {"id": aid, "done": False})
                    a["label"] = str(rec.get("label") or "")[:80]
                    a["phase"] = str(rec.get("phase") or "")[:40]
                elif rec.get("type") == "result":
                    agents.setdefault(aid, {"id": aid, "label": "", "phase": ""})["done"] = True
    except OSError:
        return []
    out = list(agents.values())
    _journals[str(path)] = (size, out)
    return out


def _workflow(session_dir: Path, run_dir: Path, now: float) -> dict | None:
    run = run_dir.name
    journal = run_dir / "journal.jsonl"
    if not journal.is_file():
        return None
    agents = _journal(journal)
    record = _json(session_dir / "workflows" / f"{run}.json")
    scripts = list((session_dir / "workflows" / "scripts").glob(f"*-{run}.js"))
    name = (record.get("workflowName")
            or (re.sub(rf"-{re.escape(run)}\.js$", "", scripts[0].name) if scripts else "") or run)
    start = (float(record["startTime"]) / 1000 if record.get("startTime")
             else _mtime(scripts[0]) if scripts else _mtime(run_dir))
    last = _mtime(journal)
    if record.get("status"):
        status = "done" if record["status"] == "completed" else str(record["status"])
        end = start + float(record.get("durationMs") or 0) / 1000 if record.get("durationMs") else last
    else:
        # No record yet: running while it or one of its unfinished agents wrote lately.
        busy = now - last < RECENT_S or any(
            now - _mtime(run_dir / f"agent-{a['id']}.jsonl") < RECENT_S for a in agents if not a["done"])
        status, end = ("running", None) if busy else ("stopped", last)
    for a in agents:
        a["state"] = "done" if a["done"] else ("running" if status == "running" else "stopped")
    phase = next((a["phase"] for a in reversed(agents) if not a["done"] and a["phase"]), "") \
        or (agents[-1]["phase"] if agents else "")
    return {"kind": "workflow", "id": run, "name": name, "status": status, "start": start, "end": end,
            "phase": phase if status == "running" else "", "summary": str(record.get("summary") or "")[:200],
            "agents": [{"label": a["label"], "phase": a["phase"], "state": a["state"]} for a in agents]}


def _helper(meta: Path, now: float) -> dict:
    m = _json(meta)
    log = meta.with_name(meta.name[: -len(".meta.json")] + ".jsonl")
    last = _mtime(log) or _mtime(meta)
    running = now - last < RECENT_S
    return {"kind": "agent", "id": meta.name[: -len(".meta.json")], "name": str(m.get("description") or "helper")[:80],
            "type": str(m.get("agentType") or ""), "status": "running" if running else "done",
            "start": _mtime(meta), "end": None if running else last, "phase": "", "summary": "",
            "agents": [{"label": str(m.get("agentType") or "agent"), "phase": "", "state": "running" if running else "done"}]}


def tasks(session_dir: Path, now: float | None = None) -> list[dict]:
    """The session's running work, then what finished in the last SHOW_DONE_S,
    newest first, at most MAX_ITEMS."""
    now = time.time() if now is None else now
    session_dir = Path(session_dir)
    sub = session_dir / "subagents"
    out: list[dict] = []
    try:
        metas = list(sub.glob("agent-*.meta.json"))
    except OSError:
        metas = []
    for meta in metas:
        if now - (_mtime(meta.with_name(meta.name[: -len(".meta.json")] + ".jsonl")) or _mtime(meta)) < SHOW_DONE_S:
            out.append(_helper(meta, now))
    try:
        runs = [d for d in (sub / "workflows").iterdir() if d.is_dir()]
    except OSError:
        runs = []
    for d in runs:
        if now - _mtime(d / "journal.jsonl") > SHOW_DONE_S and (session_dir / "workflows" / f"{d.name}.json").exists():
            continue                       # long finished: not read at all
        w = _workflow(session_dir, d, now)
        if w and (w["status"] == "running" or now - (w["end"] or now) < SHOW_DONE_S):
            out.append(w)
    out.sort(key=lambda t: (t["status"] != "running", -(t["start"] or 0)))
    return out[:MAX_ITEMS]
