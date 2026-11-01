import json
import time
from datetime import datetime, timezone

from agent_companion.messages import MessageIndex


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _send(ts, to, body, tid="t1"):
    return json.dumps({
        "type": "assistant", "timestamp": _iso(ts),
        "cwd": "/home/user/scratch", "gitBranch": "master",
        "message": {"content": [
            {"type": "tool_use", "id": tid, "name": "SendMessage",
             "input": {"to": to, "message": body}},
        ]},
    }) + "\n"


def _result(tid="t1", ok=True, subject="Did the thing", to="scratch"):
    payload = (json.dumps({"success": True,
                           "message": f"“{subject}” → {to} (another Claude session)"})
               if ok else json.dumps({"success": False, "error": "no such agent"}))
    return json.dumps({
        "type": "user",
        "message": {"content": [
            {"type": "tool_result", "tool_use_id": tid, "content": payload},
        ]},
    }) + "\n"


def _home(tmp_path, lines, proj="-home-user-scratch", name="s1.jsonl"):
    home = tmp_path / ".claude"
    d = home / "projects" / proj
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text("".join(lines))
    return home


def test_reads_a_send_with_its_subject(tmp_path):
    now = time.time()
    home = _home(tmp_path, [_send(now - 10, "home-assistant", "the long body"),
                            _result(subject="Short subject")])
    idx = MessageIndex(home, tmp_path / "c.json")
    idx.refresh(now)
    m = idx.recent()[0]
    assert m["to"] == "home-assistant"
    assert m["body"] == "the long body"
    assert m["subject"] == "Short subject"
    assert m["from_project"] == "scratch"
    assert m["ok"] is True


def test_a_failed_send_is_kept_and_marked(tmp_path):
    now = time.time()
    home = _home(tmp_path, [_send(now - 5, "ghost", "hello"), _result(ok=False)])
    idx = MessageIndex(home, tmp_path / "c.json")
    idx.refresh(now)
    m = idx.recent()[0]
    assert m["ok"] is False
    # A message that never arrived is the one worth seeing, so it is not dropped.
    assert m["to"] == "ghost"


def test_other_tools_are_ignored(tmp_path):
    now = time.time()
    line = json.dumps({"type": "assistant", "timestamp": _iso(now),
                       "message": {"content": [
                           {"type": "tool_use", "id": "x", "name": "Bash",
                            "input": {"command": "ls"}}]}}) + "\n"
    home = _home(tmp_path, [line])
    idx = MessageIndex(home, tmp_path / "c.json")
    idx.refresh(now)
    assert idx.recent() == []


def test_a_malformed_line_does_not_lose_the_file(tmp_path):
    now = time.time()
    home = _home(tmp_path, ["{not json at all\n", _send(now, "scratch", "still read")])
    idx = MessageIndex(home, tmp_path / "c.json")
    idx.refresh(now)
    assert [m["body"] for m in idx.recent()] == ["still read"]


def test_refresh_is_incremental_and_does_not_duplicate(tmp_path):
    now = time.time()
    home = _home(tmp_path, [_send(now - 20, "a", "first", tid="t1")])
    cache = tmp_path / "c.json"
    idx = MessageIndex(home, cache)
    idx.refresh(now)
    f = home / "projects" / "-home-user-scratch" / "s1.jsonl"
    first_offset = idx._files[str(f)]["offset"]

    with f.open("a") as fh:
        fh.write(_send(now - 10, "b", "second", tid="t2"))
    idx.refresh(now)
    bodies = [m["body"] for m in idx.recent()]
    assert bodies == ["second", "first"]          # newest first, no duplicate
    assert idx._files[str(f)]["offset"] > first_offset


def test_a_truncated_file_is_reread_without_duplicating(tmp_path):
    now = time.time()
    # Long enough that the replacement below is genuinely SHORTER -- that is the
    # only signal available when the inode has not changed, and an earlier
    # version of this test rewrote the file with a longer body and proved
    # nothing.
    home = _home(tmp_path, [_send(now - 20, "peer-one", "a" * 400, tid="t1")])
    idx = MessageIndex(home, tmp_path / "c.json")
    idx.refresh(now)
    f = home / "projects" / "-home-user-scratch" / "s1.jsonl"
    before = f.stat().st_size
    f.write_text(_send(now - 1, "b", "fresh", tid="t9"))
    assert f.stat().st_size < before, "the fixture must actually shrink"
    idx.refresh(now)
    assert [m["body"] for m in idx.recent()] == ["fresh"]


def test_recent_is_bounded_and_newest_first(tmp_path):
    now = time.time()
    lines = [_send(now - i, f"peer{i}", f"body{i}", tid=f"t{i}") for i in range(60, 0, -1)]
    home = _home(tmp_path, lines)
    idx = MessageIndex(home, tmp_path / "c.json")
    idx.refresh(now)
    got = idx.recent(limit=5)
    assert len(got) == 5
    assert got[0]["at"] >= got[-1]["at"]


def test_survives_a_restart_through_the_cache(tmp_path):
    now = time.time()
    cache = tmp_path / "c.json"
    home = _home(tmp_path, [_send(now - 30, "a", "persisted", tid="t1")])
    MessageIndex(home, cache).refresh(now)
    again = MessageIndex(home, cache)
    assert [m["body"] for m in again.recent()] == ["persisted"]


def test_project_falls_back_to_the_directory_when_cwd_is_absent(tmp_path):
    """Old transcripts predate the cwd field; the encoded directory is lossy
    (slashes became dashes) but its tail is still the best guess available."""
    now = time.time()
    line = json.dumps({"type": "assistant", "timestamp": _iso(now),
                       "message": {"content": [
                           {"type": "tool_use", "id": "t1", "name": "SendMessage",
                            "input": {"to": "x", "message": "b"}}]}}) + "\n"
    home = _home(tmp_path, [line])
    idx = MessageIndex(home, tmp_path / "c.json")
    idx.refresh(now)
    assert idx.recent()[0]["from_project"] == "scratch"


def test_branch_comes_along_when_the_transcript_has_it(tmp_path):
    now = time.time()
    home = _home(tmp_path, [_send(now, "a", "b")])
    idx = MessageIndex(home, tmp_path / "c.json")
    idx.refresh(now)
    assert idx.recent()[0]["from_branch"] == "master"


def test_a_private_workspace_is_never_scanned(tmp_path):
    now = time.time()
    home = _home(tmp_path, [_send(now - 5, "scratch", "for their eyes"), _result()], proj="-home-user-agent-alex")
    idx = MessageIndex(home, hide=lambda: ["/home/user/agent-alex"])
    idx.refresh(now)
    assert idx.recent() == []
    shown = MessageIndex(home)
    shown.refresh(now)
    assert shown.recent(), "the same file is read when nothing hides it"


def test_a_private_folder_hides_its_subfolders_and_dotted_paths(tmp_path):
    from agent_companion.messages import _folder
    assert _folder("/home/t/.sdkhost/agents/s/") == "-home-t--sdkhost-agents-s"
    assert _folder("/home/t/agent_s") == "-home-t-agent-s"
