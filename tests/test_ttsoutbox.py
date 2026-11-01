"""Speech outbox coordination stays pure and uses only fixtures."""
import json

from agent_companion import ttsoutbox


def _clip(root, sequence, **meta):
    meta = {"seq": sequence, "name": "Speaker", "duration": 1, "at": 100, **meta}
    (root / f"{sequence}.json").write_text(json.dumps(meta))
    (root / f"{sequence}.opus").write_bytes(b"audio")


def test_pending_orders_urgent_and_another_player_cannot_take(tmp_path):
    _clip(tmp_path, 1)
    _clip(tmp_path, 2, urgent=True)
    assert [clip["seq"] for clip in ttsoutbox.pending(0, tmp_path, now=101)] == [2, 1]
    assert ttsoutbox.take(2, "web", tmp_path, now=101)
    assert not ttsoutbox.take(2, "phone", tmp_path, now=102)


def test_app_lead_defers_other_players_but_not_the_app(tmp_path):
    _clip(tmp_path, 1)
    ttsoutbox.app_listening(tmp_path, now=100)
    assert ttsoutbox.pending(0, tmp_path, who="phone", now=101) == []
    assert [clip["seq"] for clip in ttsoutbox.pending(0, tmp_path, who="app", now=101)] == [1]


def test_mark_and_replay_use_a_new_sequence(tmp_path):
    _clip(tmp_path, 1, text="A sufficiently long fixture line")
    (tmp_path / ".seq").write_text("1\n")
    assert ttsoutbox.mark(1, "played", "", tmp_path)
    assert ttsoutbox.replay(1, tmp_path, now=200) == 2
    assert (tmp_path / "2.opus").read_bytes() == b"audio"


def test_an_answered_clip_is_anyones_to_replay(tmp_path):
    _clip(tmp_path, 3)
    assert ttsoutbox.take(3, "phone", tmp_path, now=101)
    assert not ttsoutbox.take(3, "web", tmp_path, now=102), "held while it plays"
    assert ttsoutbox.mark(3, "played", "", tmp_path)
    assert ttsoutbox.take(3, "web", tmp_path, now=103), "answered: the chat tab may replay it"
