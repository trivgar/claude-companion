"""Map pending asks onto their live owning session."""
import json

from claude_companion.server import asks, sessions


def _row(pid, speaker="", **over):
    row = {"pid": pid, "session_id": f"s{pid}", "speaker": speaker,
           "waiting": False, "child": False, "job": "", "root_pid": None}
    row.update(over)
    return row


def _helper(pid, root_pid, label="reviewing task"):
    return _row(pid, child=True, root_pid=root_pid, label=label)


def _ask(sid, speaker="", at=100.0, aid="a1", **over):
    ask = {"id": aid, "at": at, "speaker": speaker, "project": "site", "session_id": sid,
           "tool": "Bash", "what": "ls", "status": "pending"}
    ask.update(over)
    return ask


def test_an_ask_finds_its_row_by_session_id_without_a_speaker():
    rows = [_row(1, "Juno"), _row(2)]
    asks.attach(rows, [_ask("s2")])
    assert rows[1]["ask"] == {"id": "a1", "tool": "Bash", "what": "ls", "at": 100.0}
    assert "ask" not in rows[0]


def test_a_session_id_beats_a_speaker_name_that_moved():
    rows = [_row(1, "Juno"), _row(2, "Vance")]
    asks.attach(rows, [_ask("s2", "Juno")])
    assert rows[1]["ask"]["id"] == "a1" and "ask" not in rows[0]


def test_an_ask_with_no_live_session_falls_back_to_the_speaker():
    rows = [_row(1, "Juno")]
    asks.attach(rows, [_ask("gone", "Juno")])
    assert rows[0]["ask"]["id"] == "a1"


def test_a_helpers_ask_is_lifted_onto_its_agent():
    rows = [_row(1, "Juno"), _helper(2, 1)]
    asks.attach(rows, [_ask("s2", aid="a2")])
    assert rows[1]["ask"]["id"] == "a2", "the helper keeps its own"
    assert rows[0]["ask"]["id"] == "a2" and rows[0]["ask"]["from"] == "reviewing task"
    assert rows[0]["waiting"] is True


def test_a_lifted_ask_keeps_the_agents_own_waiting():
    rows = [_row(1, "Juno"), _helper(2, 1)]
    asks.attach(rows, [_ask("s2")])
    assert rows[0]["waiting"] is True and rows[0]["own_waiting"] is False
    rows = [_row(1, "Juno", waiting=True), _helper(2, 1)]
    asks.attach(rows, [_ask("s2")])
    assert rows[0]["waiting"] is True and rows[0]["own_waiting"] is True
    rows = [_row(1, "Juno", waiting=True), _helper(2, 1)]
    asks.attach(rows, [_ask("s1", "Juno")])
    assert "own_waiting" not in rows[0]


def test_an_unlabelled_helper_is_from_nobody_in_particular():
    for label in ("", "subagent"):
        rows = [_row(1, "Juno"), _helper(2, 1, label=label)]
        asks.attach(rows, [_ask("s2")])
        assert rows[0]["ask"]["from"] == "", label


def test_the_agents_own_ask_wins_and_the_older_helper_goes_first():
    rows = [_row(1, "Juno"), _helper(2, 1), _helper(3, 1, label="drafting")]
    asks.attach(rows, [_ask("s2", at=200.0, aid="h2"), _ask("s1", "Juno", at=300.0, aid="own")])
    assert rows[0]["ask"]["id"] == "own" and "from" not in rows[0]["ask"]
    rows = [_row(1, "Juno"), _helper(2, 1), _helper(3, 1, label="drafting")]
    asks.attach(rows, [_ask("s3", at=100.0, aid="h3"), _ask("s2", at=200.0, aid="h2")])
    assert rows[0]["ask"]["id"] == "h3" and rows[0]["ask"]["from"] == "drafting"


def test_a_jobs_ask_is_never_lifted():
    rows = [_row(1, "Juno"), _row(2, child=True, job="worker", root_pid=None)]
    asks.attach(rows, [_ask("s2")])
    assert "ask" not in rows[0] and rows[0]["waiting"] is False


def test_the_chat_window_lists_its_helpers_asks():
    rows = [_row(1, "Juno"), _helper(2, 1), _row(3, "Wren")]
    got = asks.for_agent([_ask("s1", "Juno", aid="own"), _ask("s2", aid="h"),
                          _ask("s3", "Wren", aid="other")], rows, 1)
    assert [(ask["id"], ask.get("from")) for ask in got] == [("own", None), ("h", "reviewing task")]


def test_resolve_names_the_helper_and_its_root():
    rows = [_row(1, "Juno"), _helper(2, 1, label="reviewing site")]
    assert asks.resolve(_ask("s2"), rows) == {"from": "reviewing site", "root": "Juno"}
    assert asks.resolve(_ask("s1", "Juno"), rows) == {}


def test_asks_list_merges_helper_mapping_from_the_live_snapshot(monkeypatch):
    pending = [_ask("s2")]
    rows = [_row(1, "Juno"), _helper(2, 1, label="reviewing site")]

    class Reader:
        def snapshot(self):
            return rows

    monkeypatch.setattr(asks, "pending", lambda: pending)
    monkeypatch.setattr(sessions, "_parts", lambda: {"reader": Reader()})
    monkeypatch.setattr(sessions, "order", lambda got: got)
    assert json.loads(asks.asks_list().body) == {
        "asks": [dict(pending[0], **{"from": "reviewing site", "root": "Juno"})]
    }


def test_asks_list_keeps_pending_asks_when_the_live_snapshot_fails(monkeypatch):
    pending = [_ask("gone", "Vance")]

    class Reader:
        def snapshot(self):
            raise OSError("unavailable")

    monkeypatch.setattr(asks, "pending", lambda: pending)
    monkeypatch.setattr(sessions, "_parts", lambda: {"reader": Reader()})
    assert json.loads(asks.asks_list().body) == {"asks": pending}


def test_a_shortened_ask_carries_show_all_onto_its_card():
    rows = [_row(1, "Juno")]
    asks.attach(rows, [_ask("s1", shortened=True, input_full='{"command": "ls -la"}')])
    assert rows[0]["ask"]["shortened"] is True
    assert rows[0]["ask"]["input_full"] == '{"command": "ls -la"}'
