"""A session's workflows and helpers as cards (claude_companion.tasks)."""
from __future__ import annotations

import json
import os

from claude_companion import tasks


def _touch(p, at):
    os.utime(p, (at, at))


def _session(tmp_path, now):
    s = tmp_path / "sess"
    run = s / "subagents" / "workflows" / "wf_1"
    run.mkdir(parents=True)
    (run / "journal.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"type": "launched"},
        {"type": "started", "agentId": "a1", "label": "review:x", "phase": "Review"},
        {"type": "started", "agentId": "a2", "label": "review:y", "phase": "Review"},
        {"type": "result", "agentId": "a1", "result": {}},
        {"type": "started", "agentId": "a3", "label": "fix:1", "phase": "Fix"},
    ]) + "\n")
    (s / "workflows" / "scripts").mkdir(parents=True)
    (s / "workflows" / "scripts" / "my-flow-wf_1.js").write_text("export const meta = {}")
    _touch(s / "workflows" / "scripts" / "my-flow-wf_1.js", now - 600)
    _touch(run / "journal.jsonl", now - 10)
    (s / "subagents" / "agent-h1.meta.json").write_text(json.dumps({"agentType": "Explore", "description": "Find the thing"}))
    (s / "subagents" / "agent-h1.jsonl").write_text("{}\n")
    _touch(s / "subagents" / "agent-h1.jsonl", now - 5)
    return s, run


def test_a_running_workflow_and_a_helper(tmp_path):
    now = 2_000_000_000.0
    s, _ = _session(tmp_path, now)
    got = tasks.tasks(s, now)
    wf = next(t for t in got if t["kind"] == "workflow")
    assert wf["name"] == "my-flow" and wf["status"] == "running" and wf["phase"] == "Fix"
    assert [a["state"] for a in wf["agents"]] == ["done", "running", "running"]
    assert wf["start"] == now - 600 and wf["end"] is None
    h = next(t for t in got if t["kind"] == "agent")
    assert h["name"] == "Find the thing" and h["type"] == "Explore" and h["status"] == "running"


def test_a_workflow_nobody_wrote_to_lately_is_stopped(tmp_path):
    now = 2_000_000_000.0
    s, run = _session(tmp_path, now)
    _touch(run / "journal.jsonl", now - 600)
    wf = next(t for t in tasks.tasks(s, now) if t["kind"] == "workflow")
    assert wf["status"] == "stopped" and {a["state"] for a in wf["agents"]} == {"done", "stopped"}


def test_the_runs_own_record_decides_once_it_ends(tmp_path):
    now = 2_000_000_000.0
    s, _ = _session(tmp_path, now)
    (s / "workflows" / "wf_1.json").write_text(json.dumps(
        {"workflowName": "named", "status": "completed", "startTime": (now - 300) * 1000, "durationMs": 120000}))
    wf = next(t for t in tasks.tasks(s, now) if t["kind"] == "workflow")
    assert wf["name"] == "named" and wf["status"] == "done" and wf["end"] == now - 180


def test_old_work_drops_off_and_running_comes_first(tmp_path):
    now = 2_000_000_000.0
    s, run = _session(tmp_path, now)
    _touch(s / "subagents" / "agent-h1.jsonl", now - tasks.SHOW_DONE_S - 5)
    got = tasks.tasks(s, now)
    assert [t["kind"] for t in got] == ["workflow"]
    assert tasks.tasks(tmp_path / "nothing", now) == []


def test_a_journal_is_parsed_again_only_when_it_grows(tmp_path):
    now = 2_000_000_000.0
    s, run = _session(tmp_path, now)
    tasks.tasks(s, now)
    with open(run / "journal.jsonl", "a") as fh:
        fh.write(json.dumps({"type": "result", "agentId": "a2", "result": {}}) + "\n")
    _touch(run / "journal.jsonl", now - 10)
    wf = next(t for t in tasks.tasks(s, now) if t["kind"] == "workflow")
    assert [a["state"] for a in wf["agents"]] == ["done", "done", "running"]
