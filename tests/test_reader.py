import json
import os
from pathlib import Path

from claude_companion.liveness import proc_start
from claude_companion.reader import SessionReader


def _home(tmp_path: Path, project="proj", with_tts=False, pane="t:@1.%1",
          tsv_pane=None, tsv_key=None) -> Path:
    home = tmp_path / ".claude"
    (home / "sessions").mkdir(parents=True)
    cwd = f"/tmp/{project}"
    slug = cwd.replace("/", "-")
    (home / "projects" / slug).mkdir(parents=True)
    pid = os.getpid()
    started = proc_start(pid)
    (home / "sessions" / f"{pid}.json").write_text(json.dumps({
        "pid": pid, "sessionId": "sid1", "cwd": cwd,
        "procStart": started, "status": "busy", "name": "auto-name",
        "kind": "interactive", "tmux": pane, "startedAt": 1,
    }))
    (home / "projects" / slug / "sid1.jsonl").write_text(
        json.dumps({"type": "ai-title", "aiTitle": "Doing a thing"}) + "\n"
        + json.dumps({"type": "assistant", "message": {
            "model": "claude-opus-5",
            "usage": {"input_tokens": 1, "output_tokens": 2,
                      "cache_read_input_tokens": 500_000,
                      "cache_creation_input_tokens": 0}}}) + "\n"
    )
    if with_tts:
        (home / "tts").mkdir()
        key = tsv_key if tsv_key is not None else f"p{pid}.{started}"
        (home / "tts" / "agents.tsv").write_text(
            f"{key}\tGLaDOS\t{project}\tmain\t1\t1\t0\t"
            f"{tsv_pane if tsv_pane is not None else pane}\n")
    return home


def test_snapshot_shape(tmp_path):
    rows = SessionReader(_home(tmp_path)).snapshot()
    assert len(rows) == 1
    row = rows[0]
    assert row["title"] == "Doing a thing"
    assert row["status"] == "busy"
    assert row["project"] == "proj"
    assert row["context_used"] == 500_001
    assert row["context_limit"] == 1_000_000
    assert 49 < row["context_pct"] < 51
    assert row["cost_usd"] > 0
    assert row["pid"] == os.getpid()
    assert row["session_id"] == "sid1"
    assert row["cache"] is None


def test_app_url_is_built_from_the_bridge_id(tmp_path):
    home = _home(tmp_path)
    p = home / "sessions" / f"{os.getpid()}.json"
    rec = json.loads(p.read_text())
    rec["bridgeSessionId"] = "session_01XYZ"
    p.write_text(json.dumps(rec))
    assert SessionReader(home).snapshot()[0]["app_url"] == "https://claude.ai/code/session_01XYZ"


def test_app_url_is_empty_without_remote_control(tmp_path):
    assert SessionReader(_home(tmp_path)).snapshot()[0]["app_url"] == ""


def test_speaker_overlay_absent_without_tts(tmp_path):
    assert SessionReader(_home(tmp_path)).snapshot()[0]["speaker"] == ""


def test_speaker_overlay_matches_on_session_key(tmp_path):
    """pid.procStart is the reliable join -- panes move, sessions don't."""
    home = _home(tmp_path, with_tts=True, tsv_pane="some:other.%9")
    assert SessionReader(home).snapshot()[0]["speaker"] == "GLaDOS"


def test_speaker_overlay_falls_back_to_pane(tmp_path):
    """An older row keyed differently still matches by tmux pane."""
    home = _home(tmp_path, with_tts=True, tsv_key="p1.1")
    assert SessionReader(home).snapshot()[0]["speaker"] == "GLaDOS"


def test_speaker_overlay_does_not_match_a_stranger(tmp_path):
    home = _home(tmp_path, with_tts=True, tsv_key="p1.1", tsv_pane="other:@9.%9")
    assert SessionReader(home).snapshot()[0]["speaker"] == ""


def test_two_sessions_in_one_pane_do_not_share_a_name(tmp_path):
    """Observed live: a second session in the same pane must not borrow the
    first's speaker name."""
    home = _home(tmp_path, with_tts=True)
    pid = os.getpid()
    # A second live session, same pane, not present in agents.tsv. The parent
    # process is a real, live, different pid.
    other = os.getppid()
    rec = json.loads((home / "sessions" / f"{pid}.json").read_text())
    rec.update(pid=other, procStart=proc_start(other), sessionId="sid2")
    (home / "sessions" / f"{other}.json").write_text(json.dumps(rec))

    rows = SessionReader(home).snapshot()
    assert len(rows) == 2
    assert sorted(r["speaker"] for r in rows) == ["", "GLaDOS"]


def test_missing_transcript_does_not_raise(tmp_path):
    home = _home(tmp_path)
    next((home / "projects").glob("*/sid1.jsonl")).unlink()
    row = SessionReader(home).snapshot()[0]
    assert row["title"] == ""
    assert row["cost_usd"] == 0.0


def test_snapshot_is_stable_across_polls(tmp_path):
    r = SessionReader(_home(tmp_path))
    assert r.snapshot()[0]["tokens"] == r.snapshot()[0]["tokens"]


def _reply_with_usage(home, timestamp):
    transcript = next((home / "projects").glob("*/sid1.jsonl"))
    with transcript.open("a") as fh:
        fh.write(json.dumps({
            "type": "assistant", "timestamp": timestamp,
            "message": {
                "model": "claude-opus-5", "content": [{"type": "text", "text": "Done."}],
                "usage": {"input_tokens": 1, "output_tokens": 2,
                          "cache_read_input_tokens": 500_000,
                          "cache_creation_input_tokens": 0},
            },
        }) + "\n")


def test_fresh_session_cache_is_not_cold(tmp_path, monkeypatch):
    now = 1_800_000_000.0
    monkeypatch.setattr("claude_companion.reader.time.time", lambda: now)
    home = _home(tmp_path)
    _reply_with_usage(home, "2027-01-15T08:00:00Z")
    cache = SessionReader(home).snapshot()[0]["cache"]
    assert cache["cold"] is False
    assert cache["expires_in_s"] == 3600.0
    assert {"rewarm_usd", "warm_usd"} <= cache.keys()


def test_session_two_hours_after_its_reply_has_a_cold_cache(tmp_path, monkeypatch):
    now = 1_800_000_000.0
    monkeypatch.setattr("claude_companion.reader.time.time", lambda: now)
    home = _home(tmp_path)
    _reply_with_usage(home, "2027-01-15T06:00:00Z")
    cache = SessionReader(home).snapshot()[0]["cache"]
    assert cache["cold"] is True
    assert cache["expires_in_s"] == -3600.0


def test_tool_use_call_refreshes_the_cache_without_moving_last_reply(tmp_path, monkeypatch):
    now = 1_800_000_000.0
    monkeypatch.setattr("claude_companion.reader.time.time", lambda: now)
    home = _home(tmp_path)
    _reply_with_usage(home, "2027-01-15T07:50:00Z")
    transcript = next((home / "projects").glob("*/sid1.jsonl"))
    with transcript.open("a") as fh:
        fh.write(json.dumps({
            "type": "assistant", "timestamp": "2027-01-15T08:00:00Z",
            "message": {
                "model": "claude-opus-5",
                "content": [{"type": "tool_use", "name": "Bash"}],
                "usage": {"input_tokens": 1, "output_tokens": 2,
                          "cache_read_input_tokens": 500_000,
                          "cache_creation_input_tokens": 0},
            },
        }) + "\n")
    row = SessionReader(home).snapshot()[0]
    assert row["last_reply_at"] == 1_799_999_400.0
    assert row["cache"]["expires_in_s"] == 3600.0


def test_missing_home_returns_empty(tmp_path):
    assert SessionReader(tmp_path / "nope").snapshot() == []


# --- blocked-on-you marker -------------------------------------------------

def _touch_marker(home, pid, when):
    d = home / "session-companion-waiting"
    d.mkdir(parents=True, exist_ok=True)
    p = d / str(pid)
    p.touch()
    os.utime(p, (when, when))


def test_not_waiting_without_a_marker(tmp_path):
    assert SessionReader(_home(tmp_path)).snapshot()[0]["waiting"] is False


def test_waiting_when_the_marker_is_newer_than_the_transcript(tmp_path):
    home = _home(tmp_path)
    t = next((home / "projects").glob("*/sid1.jsonl"))
    _touch_marker(home, os.getpid(), t.stat().st_mtime + 60)
    assert SessionReader(home).snapshot()[0]["waiting"] is True


def test_marker_older_than_the_transcript_clears_itself(tmp_path):
    """The session answered and carried on; nothing to clean up."""
    home = _home(tmp_path)
    t = next((home / "projects").glob("*/sid1.jsonl"))
    _touch_marker(home, os.getpid(), t.stat().st_mtime - 60)
    assert SessionReader(home).snapshot()[0]["waiting"] is False


# --- subagents cost money and belong to their own context ------------------

def _add_subagent(home, session_id, project="proj", output=1_000_000,
                  model="claude-opus-5", cache_read=0):
    d = home / "projects" / f"-tmp-{project}" / session_id / "subagents"
    d.mkdir(parents=True, exist_ok=True)
    (d / "agent-abc123.jsonl").write_text(json.dumps({
        "type": "assistant", "message": {"model": model, "usage": {
            "input_tokens": 0, "output_tokens": output,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": 0}}}) + "\n")


def test_subagent_tokens_are_billed_to_the_session(tmp_path):
    """The Agent tool_result records an agentId and an outputFile but NO usage,
    so the parent transcript cannot tell you what a subagent cost."""
    home = _home(tmp_path)
    before = SessionReader(home).snapshot()[0]["cost_usd"]
    _add_subagent(home, "sid1")
    row = SessionReader(home).snapshot()[0]
    assert row["cost_usd"] > before
    assert row["subagent_tokens"] == 1_000_000
    # 1M opus output at $25/MTok
    assert round(row["subagent_cost_usd"], 2) == 25.00


def test_subagents_do_not_move_the_context_bar(tmp_path):
    """They have their own window; the bar tracks the main thread."""
    home = _home(tmp_path)
    before = SessionReader(home).snapshot()[0]["context_used"]
    _add_subagent(home, "sid1", cache_read=900_000)
    assert SessionReader(home).snapshot()[0]["context_used"] == before


def test_no_subagents_costs_nothing(tmp_path):
    row = SessionReader(_home(tmp_path)).snapshot()[0]
    assert row["subagent_tokens"] == 0 and row["subagent_cost_usd"] == 0.0


# --- subagent labels ----------------------------------------------------------
# A subagent never registers as a process and never speaks, so the room cannot
# give it a speaker. It gets a label instead. The only self-contained source is its own opening prompt.

def test_subagent_label_drops_the_prompt_lead_in():
    from claude_companion.reader import subagent_label
    assert subagent_label("You are implementing Task 1: Migration path") \
        == "implementing Task 1"


def test_subagent_label_truncates_on_a_word_boundary():
    """A chip reading "making one approved b" is worse than a shorter one."""
    from claude_companion.reader import subagent_label
    got = subagent_label("You are making one approved behavior change to the categoriser")
    assert got == "making one approved…"
    assert not got.rstrip("…").endswith(" ")


def test_subagent_label_does_not_end_on_a_dangling_word():
    from claude_companion.reader import subagent_label
    got = subagent_label("You are fixing findings from a final whole-branch review")
    assert got == "fixing findings…", got


def test_subagent_label_stops_at_a_path():
    from claude_companion.reader import subagent_label
    assert subagent_label("You are auditing in `/home/user/x`") == "auditing"


def test_subagent_label_falls_back_to_the_plain_word():
    """An unlabelled chip still says a subagent is there, which is the point."""
    from claude_companion.reader import subagent_label
    assert subagent_label("") == "subagent"
    assert subagent_label("   ") == "subagent"
    assert subagent_label("`/mnt/c/x`") == "subagent"


def test_the_most_recent_message_first():
    """Most recent message at the top, not alphabetical. Its last reply or prompt, busy or
    not; the transcript's time only when it has neither."""
    from claude_companion.reader import order
    rows = [{"project": "api-main", "status": "idle", "last_reply_at": 1000, "last_activity": 5},
            {"project": "scratch", "status": "busy", "last_reply_at": 900, "last_activity": 1},
            {"project": "money", "status": "idle", "last_reply_at": 500, "last_prompt_at": 2000},
            {"project": "docs-site", "status": "idle", "last_activity": None},
            {"project": "home", "status": "idle", "last_reply_at": 1000}]
    assert [r["project"] for r in order(rows)] == ["money", "api-main", "home", "scratch", "docs-site"]


def test_one_snapshot_at_a_time(tmp_path):
    """Endpoints share one reader across a thread pool; a transcript chunk
    applied twice would count its tokens twice."""
    from claude_companion.reader import SessionReader
    r = SessionReader(tmp_path)
    seen = []
    r._snapshot = lambda: seen.append(r._snap_lock.locked()) or []
    assert r.snapshot() == [] and seen == [True]
    assert not r._snap_lock.locked()


def test_a_session_in_someone_elses_private_workspace_is_never_listed_or_read(tmp_path):
    """Another person's agent can run through your Claude Code login (an SDK
    host's claude-cli runtime), so its sessions land in your registry. A
    folder marked private is dropped before anything reads it: no title, no last prompt, no transcript opened."""
    home = _home(tmp_path)
    p = home / "sessions" / f"{os.getpid()}.json"
    rec = json.loads(p.read_text())
    rec["cwd"] = "/home/user/agent-alex"
    p.write_text(json.dumps(rec))
    reader = SessionReader(home, hide=lambda: ["/home/user/agent-alex"])
    opened = []
    real = reader._transcripts.read
    reader._transcripts.read = lambda path: (opened.append(str(path)), real(path))[1]
    assert reader.snapshot() == [] and opened == []
    rec["cwd"] = "/home/user/agent-alex-not"          # a prefix of a name is not inside it
    p.write_text(json.dumps(rec))
    assert len(reader.snapshot()) == 1


def test_a_private_session_is_presence_only_whose_and_whether_it_is_working(tmp_path):
    """Private cards still show whose agent it is and whether it is working,
    and nothing more."""
    home = _home(tmp_path)
    p = home / "sessions" / f"{os.getpid()}.json"
    rec = json.loads(p.read_text())
    rec["cwd"] = "/home/user/agent-alex"
    p.write_text(json.dumps(rec))
    reader = SessionReader(home, hide=lambda: {"/home/user/agent-alex": "Alex's agent"})
    opened = []
    real = reader._transcripts.read
    reader._transcripts.read = lambda path: (opened.append(str(path)), real(path))[1]
    assert reader.snapshot() == []
    assert reader.private_presence() == [
        {"private": True, "label": "Alex's agent", "status": "busy", "pid": os.getpid()}]
    assert opened == [], "presence reads the registry only, never their transcript"


# --- what the page needs to nest helpers (helpers nested under their agent) ---

def test_a_row_says_when_its_session_started(tmp_path):
    home = _home(tmp_path)
    p = home / "sessions" / f"{os.getpid()}.json"
    rec = json.loads(p.read_text())
    rec["startedAt"] = 1_791_000_000_500
    p.write_text(json.dumps(rec))
    assert SessionReader(home).snapshot()[0]["started_at"] == 1_791_000_000.5


def test_a_row_without_a_start_says_none(tmp_path):
    home = _home(tmp_path)
    p = home / "sessions" / f"{os.getpid()}.json"
    rec = json.loads(p.read_text())
    rec.pop("startedAt")
    p.write_text(json.dumps(rec))
    assert SessionReader(home).snapshot()[0]["started_at"] is None


def _subagent_file(home, name, started_iso, output, mtime):
    d = home / "projects" / "-tmp-proj" / "sid1" / "subagents"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{name}.jsonl"
    first = {"type": "user", "message": {"content": f"You are {name}: the job"}}
    if started_iso:
        first["timestamp"] = started_iso
    f.write_text(json.dumps(first) + "\n"
                 + json.dumps({"type": "assistant", "message": {"model": "claude-opus-5", "usage": {
                     "input_tokens": 0, "output_tokens": output,
                     "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}}) + "\n")
    os.utime(f, (mtime, mtime))
    return f


def test_each_subagent_says_its_state_its_cost_and_its_start(tmp_path):
    """A listed subagent reads busy while it wrote in the last 30 s, carries
    its own share of subagent_cost_usd, and when it started (its first
    record's timestamp), read with its label."""
    import datetime
    import time
    from claude_companion.reader import SUBAGENT_BUSY_SECS
    assert SUBAGENT_BUSY_SECS == 30.0
    home = _home(tmp_path)
    now = time.time()
    _subagent_file(home, "writing", "2026-10-11T19:39:19.000Z", 1_000_000, now - 10)
    _subagent_file(home, "reading", "2026-10-11T19:38:00Z", 400_000, now - 60)
    row = SessionReader(home).snapshot()[0]
    by = {s["label"]: s for s in row["subagents"]}
    assert by["writing"]["status"] == "busy" and by["reading"]["status"] == "idle"
    # 1M and 0.4M opus output at $25/MTok
    assert round(by["writing"]["cost_usd"], 2) == 25.00 and round(by["reading"]["cost_usd"], 2) == 10.00
    assert abs(sum(s["cost_usd"] for s in row["subagents"]) - row["subagent_cost_usd"]) < 1e-3
    assert by["writing"]["started_at"] == datetime.datetime(
        2026, 10, 11, 19, 39, 19, tzinfo=datetime.timezone.utc).timestamp()
    assert by["reading"]["started_at"] == datetime.datetime(
        2026, 10, 11, 19, 38, 0, tzinfo=datetime.timezone.utc).timestamp()


def test_a_subagent_with_no_timestamp_has_no_start(tmp_path):
    import time
    home = _home(tmp_path)
    _subagent_file(home, "plain", "", 10, time.time() - 5)
    sub = SessionReader(home).snapshot()[0]["subagents"][0]
    assert sub["started_at"] is None and sub["label"] == "plain"
