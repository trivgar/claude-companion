"""Pairing: a one-time code becomes an owner key the server keeps only as a hash."""
import json
import os

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent_companion import server  # noqa: E402
from agent_companion.server import auth  # noqa: E402

SAME = {"origin": "http://testserver", "host": "testserver"}


@pytest.fixture
def paths(tmp_path, monkeypatch):
    keys, codes = tmp_path / "owner-keys.json", tmp_path / "pairing-codes.json"
    monkeypatch.setenv("AGENT_COMPANION_OWNER_KEYS", str(keys))
    monkeypatch.setenv("AGENT_COMPANION_PAIRING", str(codes))
    return keys, codes


@pytest.fixture
def client(paths, monkeypatch):
    monkeypatch.setattr(server, "FEATURES", [])
    return TestClient(server.create_app(token="t" * 32))


def _pair(client, code):
    return client.post("/api/pair", json={"code": code}, headers=SAME)


def test_a_code_pairs_once_and_the_key_then_opens_owner_routes(client, paths):
    code = auth.make_pairing_code("phone")
    assert len(code) == 8
    r = _pair(client, code)
    key = r.json()["key"]
    assert r.status_code == 200 and len(key) == 64
    assert _pair(client, code).status_code == 403, "a code works once"
    assert auth.is_owner_key(key)
    assert [d["name"] for d in auth.devices()] == ["phone"]


def test_a_code_expires(client, paths):
    code = auth.make_pairing_code("phone", now=1000)
    assert auth.redeem_pairing_code(code, now=1000 + auth.CODE_LIFE_S + 1) is None
    code = auth.make_pairing_code("phone", now=1000)
    assert auth.redeem_pairing_code(code, now=1000 + auth.CODE_LIFE_S - 1)


def test_nothing_is_stored_in_plain_text(client, paths):
    keys, codes = paths
    code = auth.make_pairing_code("phone")
    assert code not in codes.read_text(), "the code is stored hashed"
    key = _pair(client, code).json()["key"]
    text = keys.read_text()
    assert key not in text and auth.hash_secret(key) in text
    assert json.loads(text)[0].keys() == {"name", "hash", "created"}
    for p in (keys, codes):
        assert oct(os.stat(p).st_mode & 0o777) == "0o600"
    assert code not in codes.read_text() and "hash" in codes.read_text() or codes.read_text().strip() == "[]"


def test_pairing_comes_from_our_page_and_wrong_codes_are_throttled(client, paths):
    code = auth.make_pairing_code("phone")
    assert client.post("/api/pair", json={"code": code}).status_code == 403, "no Origin"
    assert client.post("/api/pair", content=b"x", headers={**SAME, "content-type": "text/plain"}).status_code == 403
    for _ in range(auth.MAX_FAILED_PAIRS):
        assert _pair(client, "WRONGONE").status_code == 403
    assert _pair(client, code).status_code == 429, "locked out, even for the right code"


def test_unpair_removes_the_device(client, paths):
    key = _pair(client, auth.make_pairing_code("phone")).json()["key"]
    other = _pair(client, auth.make_pairing_code("tablet")).json()["key"]
    assert auth.remove_device("phone") is True and auth.remove_device("phone") is False
    assert not auth.is_owner_key(key) and auth.is_owner_key(other)


def test_the_cli_pairs_and_unpairs(paths, capsys):
    assert server.main(["pair", "--name", "phone"]) == 0
    out = capsys.readouterr().out
    assert "/pair?code=" in out
    code = out.split("code=")[1].split()[0]
    assert auth.redeem_pairing_code(code)
    assert server.main(["unpair", "phone"]) == 0 and auth.devices() == []
    assert server.main(["unpair", "phone"]) == 1


def test_the_pair_page_is_served(client):
    r = client.get("/pair?code=ABCD2345")
    assert r.status_code == 200 and "Pair this device" in r.text
    assert client.get("/static/pair.js").status_code == 200


def test_the_hook_token_file_is_not_an_owner_key(client, paths):
    assert not auth.is_owner_key("t" * 32)


def test_host_allowlist(monkeypatch):
    monkeypatch.setenv("AGENT_COMPANION_URL", "https://box.example.test")
    for h in ("127.0.0.1", "127.0.0.1:8770", "localhost:1", "[::1]:8770", "::1", "box.example.test", "BOX.example.test:443"):
        assert auth.host_allowed(h), h
    for h in ("evil.example", "127.0.0.1.evil.example", "", "box.example.test.evil.example"):
        assert not auth.host_allowed(h), h


def test_unknown_host_gets_400(client):
    assert client.get("/api/features").status_code == 200
    assert client.get("/api/features", headers={"host": "evil.example"}).status_code == 400


def test_second_device_with_the_same_name_is_not_a_silent_replace(paths):
    keys, _ = paths
    auth.add_device("phone", "k1", keys)
    auth.add_device("phone", "k2", keys)
    auth.add_device("phone", "k3", keys)
    assert [d["name"] for d in auth.devices(keys)] == ["phone", "phone-2", "phone-3"]
    assert auth.is_owner_key("k1", keys) and auth.is_owner_key("k2", keys)
