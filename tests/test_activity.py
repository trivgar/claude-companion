import json

from claude_companion import activity as ac


def _line(kind, at, blocks):
    return json.dumps({"type": kind, "timestamp": at, "message": {"content": blocks}})


def _write(path, lines):
    path.write_text("\n".join(lines) + "\n")
    return path


def test_calls_durations_failures_and_thinking(tmp_path):
    f = _write(tmp_path / "t.jsonl", [
        _line("user", "2026-10-05T10:00:00Z", [{"type": "text", "text": "go"}]),
        _line("assistant", "2026-10-05T10:00:10Z", [{"type": "tool_use", "id": "a", "name": "Bash",
                                                    "input": {"command": "pytest -q\nmore"}}]),
        _line("user", "2026-10-05T10:00:40Z", [{"type": "tool_result", "tool_use_id": "a", "is_error": True}]),
        _line("assistant", "2026-10-05T10:00:45Z", [{"type": "tool_use", "id": "b", "name": "Edit",
                                                    "input": {"file_path": "/x/y/app.py", "old_string": "s"}}]),
        _line("user", "2026-10-05T10:00:47Z", [{"type": "tool_result", "tool_use_id": "b"}]),
        _line("assistant", "2026-10-05T10:00:50Z", [{"type": "tool_use", "id": "c", "name": "WebFetch",
                                                    "input": {"url": "https://example.com/a?b"}}]),
    ])
    got = ac.activity(f)
    calls = got["calls"]
    assert [c["tool"] for c in calls] == ["WebFetch", "Edit", "Bash"], "newest first"
    assert calls[2]["target"] == "pytest -q" and calls[2]["seconds"] == 30 and calls[2]["failed"]
    assert calls[1]["target"] == "app.py" and calls[1]["seconds"] == 2
    assert calls[0]["running"] and calls[0]["target"] == "example.com"
    assert got["time"] == {"Thinking": 18.0, "Bash": 30.0, "Edit": 2.0}
    assert got["since"] is not None


def test_a_long_pause_is_not_thinking(tmp_path):
    f = _write(tmp_path / "t.jsonl", [
        _line("user", "2026-10-05T10:00:00Z", [{"type": "text", "text": "go"}]),
        _line("assistant", "2026-10-05T12:00:00Z", [{"type": "text", "text": "back"}]),
    ])
    assert ac.activity(f)["time"] == {}


def test_only_the_tail_is_read_and_a_cut_line_is_dropped(tmp_path):
    lines = [_line("assistant", f"2026-10-05T10:{i // 60:02d}:{i % 60:02d}Z",
                   [{"type": "tool_use", "id": f"id{i}", "name": "Read", "input": {"file_path": f"/f{i}"}}])
             for i in range(400)]
    f = _write(tmp_path / "t.jsonl", lines)
    got = ac.activity(f, tail_bytes=5000)
    assert 0 < len(got["calls"]) < 60
    assert got["calls"][0]["target"] == "f399"


def test_a_missing_transcript_is_empty(tmp_path):
    assert ac.activity(tmp_path / "none.jsonl") == {"since": None, "calls": [], "time": {}}
