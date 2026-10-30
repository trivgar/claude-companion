"""Asks plug-in events can take over the created push."""
import sys
import types

import pytest

pytest.importorskip("fastapi")
from starlette.requests import Request  # noqa: E402

from claude_companion import addons  # noqa: E402
from claude_companion.server import asks  # noqa: E402


def _plug(monkeypatch, fn):
    module = types.ModuleType("fake_ask_addons")
    module.on_ask = fn
    monkeypatch.setitem(sys.modules, "fake_ask_addons", module)
    monkeypatch.setenv("CLAUDE_COMPANION_ADDONS", "fake_ask_addons")
    addons.load.cache_clear()


@pytest.fixture
def asks_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_COMPANION_ASKS", str(tmp_path / "asks"))
    addons.load.cache_clear()
    yield
    addons.load.cache_clear()


def _request():
    return Request({"type": "http", "headers": []})


def _create():
    return asks.asks_create(_request(), {"speaker": "Juno", "tool": "Bash", "input": {"command": "ls"}})


def test_created_plugin_can_replace_the_core_push(asks_dir, monkeypatch):
    _plug(monkeypatch, lambda event, ask: {"pushed": True})
    pushed = []
    monkeypatch.setattr(asks, "_push", lambda ask: pushed.append(ask["id"]))
    _create()
    assert pushed == []


def test_created_keeps_the_core_push_without_a_plugin(asks_dir, monkeypatch):
    monkeypatch.delenv("CLAUDE_COMPANION_ADDONS", raising=False)
    addons.load.cache_clear()
    pushed = []
    monkeypatch.setattr(asks, "_push", lambda ask: pushed.append(ask["id"]))
    response = _create()
    assert pushed == [response["id"]]


def test_created_plugin_can_return_a_question_to_the_terminal(asks_dir, monkeypatch):
    _plug(monkeypatch, lambda event, ask: {"answerable": False})
    monkeypatch.setattr(asks, "_push", lambda ask: None)
    assert _create()["answerable"] is False


def test_decided_answered_and_withdrawn_each_fire_once(asks_dir, monkeypatch):
    seen = []
    _plug(monkeypatch, lambda event, ask: seen.append((event, ask["id"])))
    monkeypatch.setattr(asks, "_push", lambda ask: None)
    permission = _create()["id"]
    assert asks.asks_decide(permission, {"decision": "deny"})["ok"] is True
    body = {"speaker": "Vance", "tool": "AskUserQuestion",
            "input": {"questions": [{"question": "Continue?", "options": [{"label": "Yes"}]}]}}
    question = asks.asks_create(_request(), body)["id"]
    assert asks.asks_answer(question, {"answers": {"Continue?": "Yes"}})["ok"] is True
    withdrawn = _create()["id"]
    assert asks.asks_withdraw(_request(), withdrawn)["ok"] is True
    assert seen == [("created", permission), ("decided", permission), ("created", question),
                    ("answered", question), ("created", withdrawn), ("withdrawn", withdrawn)]


def test_a_raising_plugin_keeps_the_core_push_and_normal_response(asks_dir, monkeypatch):
    def broken(event, ask):
        raise RuntimeError("broken")

    _plug(monkeypatch, broken)
    pushed = []
    monkeypatch.setattr(asks, "_push", lambda ask: pushed.append(ask["id"]))
    response = _create()
    assert response == {"ok": True, "id": response["id"]}
    assert pushed == [response["id"]]
