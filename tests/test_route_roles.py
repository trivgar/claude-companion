import pytest

pytest.importorskip("fastapi")
from fastapi.routing import APIRoute  # noqa: E402

from claude_companion import server  # noqa: E402
from claude_companion.server import auth  # noqa: E402

GUARDS = {auth.require_token, auth.require_write, auth.require_hook, auth.require_person, auth.require_person_write}
OPEN = {"/", "/pair", "/api/pair", "/api/features", "/manifest.json"}


def _guards(route):
    return {d.call for d in route.dependant.dependencies} & GUARDS


def test_every_route_says_who_may_use_it():
    app = server.create_app(token="t" * 32)
    bad = [r.path for r in app.routes if isinstance(r, APIRoute)
           and r.path not in OPEN and not _guards(r)]
    assert not bad, f"routes with no role: {bad}"


def test_a_person_route_takes_an_owner_or_a_person(monkeypatch):
    from fastapi import APIRouter, Depends
    from fastapi.testclient import TestClient
    from claude_companion import addons
    owner = "o" * 64
    app = server.create_app(token="t" * 32, owner_key=owner)
    r = APIRouter()

    @r.get("/api/shared", dependencies=[Depends(auth.require_person)])
    def shared():
        return {"ok": True}
    app.include_router(r)
    c = TestClient(app)
    monkeypatch.setattr(addons, "person", lambda req: None)
    assert c.get("/api/shared").status_code == 401
    assert c.get("/api/shared", headers={auth.OWNER_HEADER: owner}).status_code == 200
    monkeypatch.setattr(addons, "person", lambda req: {"name": "alex"})
    assert c.get("/api/shared").status_code == 200


def test_a_person_never_opens_an_owner_route(monkeypatch):
    from claude_companion import addons
    from fastapi.testclient import TestClient
    monkeypatch.setattr(addons, "person", lambda req: {"name": "alex"})
    c = TestClient(server.create_app(token="t" * 32))
    assert c.get("/api/asks").status_code == 401
