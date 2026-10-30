import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from claude_companion import addons, server  # noqa: E402
from claude_companion.mail import queue  # noqa: E402

TOKEN = "t" * 32
OWNER = "o" * 64
KEY = {"x-claude-companion-key": OWNER}
SAME = {"origin": "http://testserver", "host": "testserver"}


@pytest.fixture
def c(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_COMPANION_MAIL_DIR", str(tmp_path / "queue"))
    credentials = tmp_path / "owner.json"
    credentials.write_text(json.dumps({"address": "owner@example.com", "password": "app password"}))
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        '[mail_accounts.owner]\nsource = "gmail"\n'
        '[mail_accounts.second]\nsource = "imap"\n'
        f'credentials_file = "{credentials}"\n')
    monkeypatch.setenv("CLAUDE_COMPANION_CONFIG", str(config_file))
    monkeypatch.setattr(server, "FEATURES", ["mail"])
    queue.write_draft({"id": "m1", "account": "owner", "thread": "t1", "from": "Sender <sender@example.com>",
                       "subject": "Question", "why": "asked", "reply": "Yes."})
    queue.write_draft({"id": "m2", "account": "second", "thread": "t2", "from": "Other <other@example.com>",
                       "subject": "Private", "why": "asked", "reply": "No."})
    return TestClient(server.create_app(token=TOKEN, owner_key=OWNER))


def test_mail_is_a_feature_and_owner_reads_only_owner_mail(c):
    assert c.get("/api/features").json() == [{"id": "mail", "title": "Mail"}]
    assert c.get("/api/mail").status_code == 401
    data = c.get("/api/mail", headers=KEY).json()
    assert data["setup"] and [draft["id"] for draft in data["drafts"]] == ["m1"]


def test_a_person_can_read_only_their_configured_mailbox(c, monkeypatch):
    monkeypatch.setattr(addons, "person", lambda request: {"mail_account": "second"})
    data = c.get("/api/mail").json()
    assert [draft["id"] for draft in data["drafts"]] == ["m2"]
    assert c.post("/api/mail/m1", json={"state": "approved"}, headers=SAME).status_code == 404


def test_a_person_cannot_claim_the_owner_mailbox(c, monkeypatch):
    monkeypatch.setattr(addons, "person", lambda request: {"mail_account": "owner"})
    assert c.get("/api/mail").json()["setup"] is False


def test_unconfigured_mail_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "FEATURES", ["mail"])
    monkeypatch.setenv("CLAUDE_COMPANION_MAIL_DIR", str(tmp_path / "queue"))
    monkeypatch.setenv("CLAUDE_COMPANION_CONFIG", str(tmp_path / "missing.toml"))
    c = TestClient(server.create_app(token=TOKEN, owner_key=OWNER))
    data = c.get("/api/mail", headers=KEY).json()
    assert data == {"drafts": [], "counts": {"pending": 0, "total": 0}, "setup": False,
                    "detail": "Mail is not set up for this account."}


def test_writes_need_identity_json_and_the_page_origin(c):
    assert c.post("/api/mail/m1", json={"state": "approved"}, headers=SAME).status_code == 401
    assert c.post("/api/mail/m1", json={"state": "approved"},
                  headers={**KEY, "origin": "http://evil.example", "host": "testserver"}).status_code == 403


def test_decide_and_edit_keep_the_existing_addresses(c):
    headers = {**KEY, **SAME}
    assert c.post("/api/mail/m1/edit", json={"fields": {"reply": "Yes, later."}}, headers=headers).status_code == 200
    assert c.post("/api/mail/m1", json={"state": "approved"}, headers=headers).status_code == 200
    assert c.get("/api/mail", headers=KEY).json()["drafts"][0]["state"] == "approved"
    assert c.post("/api/mail/m1", json={"state": "rejected"}, headers=headers).status_code == 200


def test_no_delivery_address_exists(c):
    headers = {**KEY, **SAME}
    assert c.post("/api/mail/m1/forward", json={}, headers=headers).status_code in (404, 405)
