import json
import re
from datetime import date

import pytest

from agent_companion import money
from agent_companion.money import demo

BAD = re.compile(r"move|transfer|pay|send|create|update|delete", re.I)
TODAY = date(2026, 10, 29)


def test_no_source_can_write():
    assert "demo" in money.registered()
    for attr in dir(money.MoneySource):
        assert not BAD.search(attr), attr
    for attr in dir(money.source("demo")):
        assert not BAD.search(attr), attr


def test_unknown_source_names_the_choices():
    with pytest.raises(KeyError, match="demo"):
        money.source("nope")


def test_demo_is_consistent():
    s = demo.DemoSource(today=TODAY)
    accts = s.accounts()
    assert {a["kind"] for a in accts} == set(money.KINDS)
    assert all({"id", "name", "kind", "balance", "currency", "updated"} <= set(a) for a in accts)
    nw = s.net_worth()
    assert nw["total"] == sum(money.signed(a) for a in accts) == 22900
    assert nw["history"][-1] == {"date": "2026-10-29", "total": nw["total"]}
    dates = [p["date"] for p in nw["history"]]
    assert dates == sorted(dates)
    cf = s.cash_flow(6)
    assert [r["month"] for r in cf][-1] == "2026-10" and len(cf) == 6
    cats = s.spending_by_category("2026-10-01", "2026-10-31")
    assert sum(c["amount"] for c in cats) == cf[-1]["spending"]
    assert [c["amount"] for c in cats] == sorted((c["amount"] for c in cats), reverse=True)
    assert s.freshness() == {"source": "demo", "as_of": "2026-10-29"}


def test_demo_upcoming_comes_from_recurring():
    s = demo.DemoSource(today=TODAY)
    names = {r["name"] for r in s.recurring()}
    up = s.upcoming(14)
    assert up and {u["name"] for u in up} <= names
    assert all("2026-10-29" <= u["date"] <= "2026-11-12" for u in up)
    assert [u["date"] for u in up] == sorted(u["date"] for u in up)
    assert all(r["next_date"] >= "2026-10-29" for r in s.recurring())


# --- routes ---
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent_companion import server  # noqa: E402
from routes_walk import all_routes  # noqa: E402

TOKEN, OWNER = "t" * 32, "o" * 64
KEY = {"x-agent-companion-key": OWNER}
PATHS = ["accounts", "net_worth", "cash_flow", "categories", "recurring", "upcoming", "freshness"]


@pytest.fixture
def c(monkeypatch):
    monkeypatch.setattr(server, "FEATURES", ["money"])
    return TestClient(server.create_app(token=TOKEN, owner_key=OWNER))


def test_routes_need_the_owner_key(c, monkeypatch):
    monkeypatch.setenv("AGENT_COMPANION_MONEY_SOURCE", "demo")
    assert c.get("/api/features").json() == [{"id": "money", "title": "Money"}]
    for p in PATHS:
        assert c.get(f"/api/money/{p}").status_code == 401, p
        assert c.get(f"/api/money/{p}", headers={"x-agent-companion-key": TOKEN}).status_code == 401, p
        assert c.get(f"/api/money/{p}", headers=KEY).status_code == 200, p


def test_nothing_writes(c):
    for p in PATHS:
        assert c.post(f"/api/money/{p}", json={}, headers=KEY).status_code in (404, 405)
    assert all(r.methods <= {"GET", "HEAD"} for r in all_routes(c.app) if getattr(r, "path", "").startswith("/api/money"))


def test_unset_source_says_so(c):
    for p in PATHS:
        d = c.get(f"/api/money/{p}", headers=KEY).json()
        assert d["configured"] is False and "No money source set up" in d["message"], p
        assert d["docs"] == "docs/money-sources.md"


def test_demo_through_the_routes(c, monkeypatch):
    monkeypatch.setenv("AGENT_COMPANION_MONEY_SOURCE", "demo")
    f = c.get("/api/money/freshness", headers=KEY).json()
    assert f["demo"] is True and f["source"] == "demo"
    assert len(c.get("/api/money/accounts", headers=KEY).json()["accounts"]) == 5
    assert c.get("/api/money/categories?start=bad", headers=KEY).status_code == 400
    assert c.get("/api/money/categories", headers=KEY).json()["categories"]


def test_unknown_figures_are_null_never_zero(c, monkeypatch):
    class Gappy(demo.DemoSource):
        def accounts(self):
            return [{"id": "x", "name": "Mystery", "kind": "checking", "balance": None,
                     "currency": "USD", "updated": None}]

        def cash_flow(self, months=6):
            return [{"month": "2026-10", "income": float("nan"), "spending": None}]

    money.register("gappy", Gappy)
    monkeypatch.setenv("AGENT_COMPANION_MONEY_SOURCE", "gappy")
    a = c.get("/api/money/accounts", headers=KEY)
    assert a.json()["accounts"][0]["balance"] is None and '"balance":null' in a.text.replace(" ", "")
    cf = c.get("/api/money/cash_flow", headers=KEY).json()["cash_flow"][0]
    assert cf["income"] is None and cf["spending"] is None


def test_a_broken_source_degrades_one_route(c, monkeypatch):
    class Broken(demo.DemoSource):
        def recurring(self):
            raise RuntimeError("feed down")

    money.register("broken", Broken)
    monkeypatch.setenv("AGENT_COMPANION_MONEY_SOURCE", "broken")
    d = c.get("/api/money/recurring", headers=KEY).json()
    assert d["recurring"] is None and "feed down" in d["error"]
    assert c.get("/api/money/accounts", headers=KEY).json()["accounts"]
