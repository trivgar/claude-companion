import json

import pytest

pytest.importorskip("fastapi")
from claude_companion.server import auth  # noqa: E402
from claude_companion.server import deferred as route  # noqa: E402


def test_deferred_routes_use_owner_read_and_same_origin_write_guards(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_COMPANION_DEFERRED_FILE", str(tmp_path / "deferred.md"))
    monkeypatch.setenv("CLAUDE_COMPANION_DEFERRED_BY", "page owner")
    get_route = next(item for item in route.router.routes if item.path == "/api/deferred" and "GET" in item.methods)
    add_route = next(item for item in route.router.routes if item.path == "/api/deferred" and "POST" in item.methods)
    assert get_route.dependencies[0].dependency is auth.require_token
    assert add_route.dependencies[0].dependency is auth.require_write
    entry = route.add_deferred({"text": "one", "for": "other", "when": "later"})["entry"]
    assert entry["by"] == "page owner" and entry["for"] == "other" and entry["when"] == "later"
    body = json.loads(route.get_deferred().body)
    assert [item["header"] for item in body["entries"]] == [entry["header"]]
