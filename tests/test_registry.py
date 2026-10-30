import json
import os
from pathlib import Path

from claude_companion.liveness import proc_start
from claude_companion.registry import live_sessions


def _write(d: Path, pid: int, **over):
    rec = {
        "pid": pid, "sessionId": f"sid-{pid}", "cwd": "/home/t/proj",
        "startedAt": 1789329667883, "procStart": proc_start(os.getpid()) or "1",
        "kind": "interactive", "tmux": "t:@1.%1", "name": f"n{pid}",
        "status": "idle",
    }
    rec.update(over)
    (d / f"{pid}.json").write_text(json.dumps(rec))


def test_keeps_only_live_sessions(tmp_path):
    _write(tmp_path, os.getpid())
    _write(tmp_path, 2_147_483_600, procStart="5")
    assert [s["pid"] for s in live_sessions(tmp_path)] == [os.getpid()]


def test_row_carries_proc_start_for_identity(tmp_path):
    """pid + proc_start identifies the process; agents.tsv keys on it."""
    _write(tmp_path, os.getpid())
    row = live_sessions(tmp_path)[0]
    assert row["proc_start"] == proc_start(os.getpid())


def test_row_carries_the_remote_control_bridge_id(tmp_path):
    _write(tmp_path, os.getpid(), bridgeSessionId="session_01ABC")
    assert live_sessions(tmp_path)[0]["bridge_session_id"] == "session_01ABC"


def test_bridge_id_absent_when_not_remote_controlled(tmp_path):
    _write(tmp_path, os.getpid())
    assert live_sessions(tmp_path)[0]["bridge_session_id"] == ""


def test_project_is_basename_of_cwd(tmp_path):
    _write(tmp_path, os.getpid(), cwd="/home/user/my-project")
    assert live_sessions(tmp_path)[0]["project"] == "my-project"


def test_ignores_unparseable_file(tmp_path):
    (tmp_path / "bad.json").write_text("{not json")
    _write(tmp_path, os.getpid())
    assert len(live_sessions(tmp_path)) == 1


def test_missing_fields_do_not_raise(tmp_path):
    (tmp_path / "9.json").write_text(json.dumps({"pid": os.getpid()}))
    out = live_sessions(tmp_path)
    assert out == [] or out[0]["session_id"] == ""


def test_missing_dir_returns_empty(tmp_path):
    assert live_sessions(tmp_path / "nope") == []
