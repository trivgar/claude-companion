import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from claude_companion import server  # noqa: E402


def _client(monkeypatch, name=None):
    if name is None:
        monkeypatch.delenv("CLAUDE_COMPANION_APP_NAME", raising=False)
    else:
        monkeypatch.setenv("CLAUDE_COMPANION_APP_NAME", name)
    monkeypatch.setenv("CLAUDE_COMPANION_CONFIG", "/nonexistent")
    return TestClient(server.create_app(token="t" * 32))


def test_the_default_name(monkeypatch):
    c = _client(monkeypatch)
    assert "<title>Companion</title>" in c.get("/").text
    assert c.get("/manifest.json").json()["name"] == "Companion"


def test_your_name_everywhere(monkeypatch):
    c = _client(monkeypatch, "Hilltop")
    assert "<title>Hilltop</title>" in c.get("/").text
    assert "Hilltop" in c.get("/pair").text
    m = c.get("/manifest.json").json()
    assert m["name"] == "Hilltop" and m["short_name"] == "Hilltop"


def test_a_name_is_text_not_markup(monkeypatch):
    c = _client(monkeypatch, "<b>x</b>")
    assert "<b>x</b>" not in c.get("/").text
    assert "&lt;b&gt;x&lt;/b&gt;" in c.get("/").text
