"""Speech coordinator controls use a temporary coordinator only."""
from agent_companion import addons, agentvibes, commsbusy


def _tts(tmp_path):
    tts = tmp_path / "tts"
    tts.mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "audio-effects.cfg").write_text("Speaker|gain 8|track.mp3|0.70\n")
    tracks = tmp_path / "audio" / "tracks"
    tracks.mkdir(parents=True)
    (tracks / "track.mp3").write_bytes(b"x")
    return tts


def test_controls_are_unavailable_until_the_coordinator_layout_exists(tmp_path):
    assert not agentvibes.available(tmp_path / "tts")
    assert agentvibes.available(_tts(tmp_path))


def test_speaker_input_is_whitelisted_and_written_as_data(tmp_path):
    tts = _tts(tmp_path)
    row = agentvibes.update_speaker(tts, "Speaker", preset="warm", track="track.mp3", volume=0.4)
    assert row["preset"] == "warm" and row["volume"] == 0.4
    assert "reverb 30" in (tts / "effects.override.tsv").read_text()
    try:
        agentvibes.update_speaker(tts, "Speaker", preset="not-a-preset")
    except ValueError as exc:
        assert "preset" in str(exc)
    else:
        assert False, "invalid effect preset was accepted"


def test_manual_hold_and_the_expiring_mute_are_plain_state(tmp_path):
    tts = _tts(tmp_path)
    assert agentvibes.set_hold(tts, None)["held"]
    assert not agentvibes.set_hold(tts, 0)["held"]
    assert agentvibes.set_mute(tts, "Speaker", 3)["speakers"]


def test_no_addon_busy_reason_is_a_safe_default(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENT_COMPANION_ADDONS", raising=False)
    addons.load.cache_clear()
    assert commsbusy.reason() is None
