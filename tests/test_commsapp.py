"""Android comms registration does not require real Firebase credentials."""
import json

from agent_companion import commsapp


def _clip(root, sequence):
    (root / ".seq").write_text(f"{sequence}\n")
    (root / f"{sequence}.json").write_text(json.dumps({"seq": sequence, "at": 100, "name": "Speaker"}))
    (root / f"{sequence}.opus").write_bytes(b"audio")


def test_registration_is_private_and_a_new_token_starts_clean(tmp_path, monkeypatch):
    tts = tmp_path / "tts"
    tts.mkdir()
    monkeypatch.setenv("AGENT_COMPANION_TTS_APP_STATE", str(tmp_path / "state.json"))
    first = commsapp.register(tts, "one", now=100, notify=True)
    assert first["registered"] and oct((tmp_path / "state.json").stat().st_mode & 0o777) == "0o600"
    commsapp.took(tts, now=101)
    second = commsapp.register(tts, "two", now=102)
    assert second["last_take"] is None


def test_ready_excludes_finished_and_pushed_clips(tmp_path):
    _clip(tmp_path, 1)
    _clip(tmp_path, 2)
    (tmp_path / "1.done").write_text("{}")
    (tmp_path / "2.pushed").write_text("{}")
    assert commsapp.ready(tmp_path, now=101) == []


def test_a_clip_is_claimed_before_its_mobile_push(tmp_path, monkeypatch):
    tts = tmp_path / "tts"
    tts.mkdir()
    monkeypatch.setenv("AGENT_COMPANION_TTS_APP_STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(commsapp, "configured", lambda: True)
    _clip(tmp_path, 1)
    commsapp.register(tts, "token", now=100)

    def send(_token, sequence):
        assert sequence == 1 and (tmp_path / "1.pushed").is_file()
        return "ok"

    assert commsapp.push_pending(tts, tmp_path, send=send, now=101)
    assert commsapp.status(tts, now=101)["last_push"] == 101
