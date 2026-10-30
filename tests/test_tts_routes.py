"""Coordinator HTTP routes. These use TestClient and are left to the route-test run."""
import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from claude_companion import server  # noqa: E402
from claude_companion.server import auth  # noqa: E402

TOKEN = "t" * 32
OWNER = "o" * 64
KEY = {auth.OWNER_HEADER: OWNER}
SAME = {"origin": "http://testserver", "host": "testserver"}


def _coordinator(tmp_path):
    tts = tmp_path / "tts"
    tts.mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "audio-effects.cfg").write_text("Speaker|gain 8||0.7\n")
    return tts


def test_missing_coordinator_is_a_plain_setup_error(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_COMPANION_TTS_DIR", str(tmp_path / "missing"))
    monkeypatch.setattr(server, "FEATURES", ["comms"])
    client = TestClient(server.create_app(token=TOKEN, owner_key=OWNER))
    response = client.get("/api/tts", headers=KEY)
    assert response.status_code == 503 and "not set up" in response.json()["detail"]


def test_outbox_routes_take_and_ack_a_clip(tmp_path, monkeypatch):
    tts = _coordinator(tmp_path)
    outbox = tts / "outbox"
    outbox.mkdir()
    (outbox / ".seq").write_text("7\n")
    (outbox / "7.json").write_text(json.dumps({"seq": 7, "name": "Speaker", "at": 1, "duration": 1}))
    (outbox / "7.opus").write_bytes(b"OggS")
    monkeypatch.setenv("CLAUDE_COMPANION_TTS_DIR", str(tts))
    monkeypatch.setattr(server, "FEATURES", ["comms"])
    client = TestClient(server.create_app(token=TOKEN, owner_key=OWNER))
    assert client.get("/api/tts/outbox", headers=KEY).json()["clips"][0]["seq"] == 7
    assert client.get("/api/tts/clip/7?who=web", headers=KEY).status_code == 200
    assert client.post("/api/tts/played", json={"seq": 7, "result": "played"}, headers={**SAME, **KEY}).json() == {"ok": True}


def test_android_gets_refuse_requests_without_an_owner_key(tmp_path, monkeypatch):
    tts = _coordinator(tmp_path)
    outbox = tts / "outbox"
    outbox.mkdir()
    (outbox / "1.json").write_text(json.dumps({"seq": 1, "name": "Speaker", "at": 1, "duration": 1}))
    (outbox / "1.opus").write_bytes(b"OggS")
    monkeypatch.setenv("CLAUDE_COMPANION_TTS_DIR", str(tts))
    monkeypatch.setattr(server, "FEATURES", ["comms"])
    client = TestClient(server.create_app(token=TOKEN, owner_key=OWNER))
    assert client.get("/api/tts/outbox").status_code == 401
    assert client.get("/api/tts/clip/1?who=app").status_code == 401


def test_write_routes_and_mobile_registration_carry_owner_and_setup_guards(tmp_path, monkeypatch):
    tts = _coordinator(tmp_path)
    monkeypatch.setenv("CLAUDE_COMPANION_TTS_DIR", str(tts))
    monkeypatch.setattr(server, "FEATURES", ["comms"])
    client = TestClient(server.create_app(token=TOKEN, owner_key=OWNER))
    assert client.post("/api/tts/hold", json={"minutes": 5}, headers=SAME).status_code == 401
    assert client.post("/api/tts/hold", json={"minutes": 5}, headers={**SAME, **KEY}).status_code == 200
    response = client.post("/api/tts/app", json={"token": "token"}, headers={**SAME, **KEY})
    assert response.status_code == 503 and "fcm_service_account_file" in response.json()["detail"]


def test_android_build_route_uses_settings_not_a_fixed_path(tmp_path, monkeypatch):
    tts = _coordinator(tmp_path)
    apk = tmp_path / "build.apk"
    monkeypatch.setenv("CLAUDE_COMPANION_TTS_DIR", str(tts))
    monkeypatch.setenv("CLAUDE_COMPANION_APP_NAME", "Example App")
    monkeypatch.setenv("CLAUDE_COMPANION_ANDROID_APK_FILE", str(apk))
    monkeypatch.setattr(server, "FEATURES", ["comms"])
    client = TestClient(server.create_app(token=TOKEN, owner_key=OWNER))
    assert client.get("/app/exampleapp.apk", headers=KEY).status_code == 404
    apk.write_bytes(b"PK")
    assert client.get("/app/exampleapp.apk", headers=KEY).content == b"PK"


def test_a_phone_that_can_ring_is_recorded_as_the_live_calls_ringer(tmp_path, monkeypatch):
    # One registration route for the phone: comms owns it, the ring reads it.
    from claude_companion import fcm
    from claude_companion.server import comms as comms_routes
    tts = _coordinator(tmp_path)
    monkeypatch.setenv("CLAUDE_COMPANION_TTS_DIR", str(tts))
    monkeypatch.setattr(server, "FEATURES", ["comms"])
    monkeypatch.setattr(comms_routes.commsapp, "configured", lambda: True)
    monkeypatch.setattr(comms_routes.commsapp, "register", lambda tts, token, notify, ring: {"ok": True})
    saved = []
    monkeypatch.setattr(fcm, "save_device", lambda token, ring=False: saved.append((token, ring)) or True)
    client = TestClient(server.create_app(token=TOKEN, owner_key=OWNER))
    assert client.post("/api/tts/app", json={"token": "abc", "ring": True}, headers=SAME).status_code == 401
    assert saved == []
    assert client.post("/api/tts/app", json={"token": "abc", "ring": True}, headers={**SAME, **KEY}).json() == {"ok": True}
    assert saved == [("abc", True)]
    assert client.post("/api/tts/app", json={"token": "def"}, headers={**SAME, **KEY}).json() == {"ok": True}
    assert saved == [("abc", True)], "a phone that cannot ring is not a ringer"
