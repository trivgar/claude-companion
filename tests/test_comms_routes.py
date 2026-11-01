import json
import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent_companion import server  # noqa: E402

TOKEN = "t" * 32
OWNER = "o" * 64
KEY = {"x-agent-companion-key": OWNER}
SAME = {"origin": "http://testserver", "host": "testserver"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("COMMS_CLIPS_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("COMMS_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "clips").mkdir()
    (tmp_path / "state").mkdir()
    monkeypatch.setattr(server, "FEATURES", ["comms"])
    c = TestClient(server.create_app(token=TOKEN, owner_key=OWNER))
    c.clips = tmp_path / "clips"
    c.state = tmp_path / "state"
    return c


def add(c, cid, speaker="Juno", text="hi", at=None):
    import time
    (c.clips / f"{cid}.wav").write_bytes(b"RIFFdata")
    (c.clips / f"{cid}.json").write_text(json.dumps({"speaker": speaker, "text": text, "at": at or int(time.time())}))


def test_the_feature_is_listed(client):
    assert client.get("/api/features").json() == [{"id": "comms", "title": "Comms"}]
    assert client.get("/static/comms.js").status_code == 200


def test_reads_need_the_token(client):
    assert client.get("/api/comms/next").status_code == 401
    assert client.get("/api/comms/clip/1-Juno").status_code == 401


def test_next_is_the_oldest_unplayed_and_played_advances(client):
    add(client, "100-Juno", text="one")
    add(client, "200-Wren", speaker="Wren", text="two")
    got = client.get("/api/comms/next", headers=KEY).json()
    assert got["clip"]["id"] == "100-Juno" and got["clip"]["text"] == "one" and got["held"] is False
    r = client.post("/api/comms/played", json={"id": "100-Juno"}, headers={**SAME, **KEY})
    assert r.status_code == 200
    assert client.get("/api/comms/next", headers=KEY).json()["clip"]["id"] == "200-Wren"
    client.post("/api/comms/played", json={"id": "200-Wren"}, headers={**SAME, **KEY})
    assert client.get("/api/comms/next", headers=KEY).json() == {"clip": None, "held": False}


def test_a_hold_offers_nothing(client):
    add(client, "100-Juno")
    (client.state / "hold").write_text("0\n")
    assert client.get("/api/comms/next", headers=KEY).json() == {"clip": None, "held": True}


def test_the_clip_is_the_wav_and_ids_are_checked(client):
    add(client, "100-Juno")
    r = client.get("/api/comms/clip/100-Juno", headers=KEY)
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.content == b"RIFFdata"
    assert client.get("/api/comms/clip/999-Nobody", headers=KEY).status_code == 404
    assert client.get("/api/comms/clip/..%2Fstate", headers=KEY).status_code in (400, 404)
    assert client.get("/api/comms/clip/a.b", headers=KEY).status_code == 400


def test_played_is_a_guarded_write(client):
    add(client, "100-Juno")
    body = {"id": "100-Juno"}
    assert client.post("/api/comms/played", json=body, headers=SAME).status_code == 401
    assert client.post("/api/comms/played", json=body, headers={**KEY, "origin": "http://evil.example", "host": "testserver"}).status_code == 403
    assert client.post("/api/comms/played", content="id=1", headers={**SAME, **KEY, "content-type": "text/plain"}).status_code == 415
    assert client.post("/api/comms/played", json={"id": "../x"}, headers={**SAME, **KEY}).status_code == 400
    assert client.post("/api/comms/played", json={"id": "5-Gone"}, headers={**SAME, **KEY}).status_code == 404
    assert not (client.clips / "100-Juno.played").exists()


def test_an_unfinished_clip_is_not_offered(client):
    (client.clips / "100-Juno.json").write_text(json.dumps({"speaker": "Juno", "text": "x", "at": 1}))  # no wav yet
    assert client.get("/api/comms/next", headers=KEY).json()["clip"] is None


def test_nothing_in_comms_names_a_voice_outside_the_cast():
    comms = Path(__file__).resolve().parent.parent / "comms"
    cast = {line.split("\t")[1] for line in (comms / "cast.tsv").read_text().splitlines() if "\t" in line}
    assert len(cast) == 4 and all(re.fullmatch(r"en_(US|GB)-[a-z_]+-(low|medium|high)", v) for v in cast)
    banned = re.compile(r"sovits|indextts|\.pth\b|\.ckpt\b|\.safetensors|remote-hosts|ha\.env|site\.conf|"
                        r"announce\.conf|termux|home.?assistant", re.I)
    for p in comms.rglob("*"):
        if not p.is_file() or p.name == "cast.tsv" or "__pycache__" in p.parts:
            continue
        text = p.read_text()
        assert not banned.search(text), f"{p.name}: {banned.search(text).group(0)}"
        for m in re.findall(r"(?<![$\w.-])[\w.-]+\.onnx", text):
            assert m[:-5] in cast, f"{p.name} names {m}"
        if p.name.endswith(".md") and p.parent.name == "personas":
            assert not re.search(r"\btrav(is)?\b", text, re.I)
