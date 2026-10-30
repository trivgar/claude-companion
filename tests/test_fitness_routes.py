import datetime

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from claude_companion import fitness, server  # noqa: E402
from claude_companion.fitness.demo import DemoSource  # noqa: E402
from claude_companion.server import fitness as route  # noqa: E402

TOKEN = "t" * 32
OWNER = "o" * 64
KEY = {"x-claude-companion-key": OWNER}


@pytest.fixture
def c(monkeypatch):
    monkeypatch.setattr(server, "FEATURES", ["fitness"])
    route._cache.clear()
    yield TestClient(server.create_app(token=TOKEN, owner_key=OWNER))
    route._cache.clear()


def test_fitness_is_a_feature_and_needs_the_owner_key(c):
    assert c.get("/api/features").json() == [{"id": "fitness", "title": "Fitness"}]
    assert c.get("/api/fitness").status_code == 401
    assert c.get("/api/fitness", headers={"x-claude-companion-key": "x" * 64}).status_code == 401
    assert c.get("/api/fitness", headers={"x-claude-companion-token": TOKEN}).status_code == 401


def test_no_source_says_so_and_points_at_the_docs(c):
    d = c.get("/api/fitness", headers=KEY).json()
    assert d["ok"] is None and d["detail"] == "No fitness source set up" and d["docs"] == "docs/fitness-sources.md"


def test_demo_source_is_labelled_and_counted(c, monkeypatch):
    monkeypatch.setenv("CLAUDE_COMPANION_FITNESS_SOURCE", "demo")
    d = c.get("/api/fitness?days=90", headers=KEY).json()
    assert d["ok"] is True and d["demo"] is True and d["source"] == "Demo data"
    assert 0 < d["recorded"]["nights"] < d["recorded"]["of"] == 90
    assert len(d["chart"]) == 90 and d["units"] == {"distance": "km", "weight": "kg"}
    assert d["work_split"] is None


def test_units_goal_and_work_days_come_from_config(c, monkeypatch):
    monkeypatch.setenv("CLAUDE_COMPANION_FITNESS_SOURCE", "demo")
    monkeypatch.setenv("CLAUDE_COMPANION_UNITS", "imperial")
    monkeypatch.setenv("CLAUDE_COMPANION_FITNESS_SLEEP_GOAL_MIN", "480")
    monkeypatch.setenv("CLAUDE_COMPANION_FITNESS_WORK_DAYS", "mon,tue,wed")
    d = c.get("/api/fitness", headers=KEY).json()
    assert d["units"] == {"distance": "mi", "weight": "lb"} and d["goal_min"] == 480
    assert d["work_split"]["work"]["nights"] > 0 and d["work_split"]["off"]["nights"] > 0


def test_a_broken_source_is_said_in_words_not_a_500(c, monkeypatch):
    class Bad:
        name, demo = "Home Assistant", False
        def daily(self, days): raise PermissionError("Home Assistant refused the token")
        def freshness(self): return {}
    monkeypatch.setattr(fitness, "source", lambda name=None: Bad())
    d = c.get("/api/fitness", headers=KEY).json()
    assert d["ok"] is False and "refused the token" in d["detail"]


def test_cached_ten_minutes(c, monkeypatch):
    calls = []

    class Counting(DemoSource):
        def daily(self, days):
            calls.append(days)
            return super().daily(days)
    monkeypatch.setattr(fitness, "source", lambda name=None: Counting())
    c.get("/api/fitness", headers=KEY)
    c.get("/api/fitness", headers=KEY)
    assert len(calls) == 1
    now = route._clock()
    monkeypatch.setattr(route, "_clock", lambda: now + route.TTL + 1)
    c.get("/api/fitness", headers=KEY)
    assert len(calls) == 2


def test_days_are_clamped(c, monkeypatch):
    seen = []

    class Spy(DemoSource):
        def daily(self, days):
            seen.append(days)
            return super().daily(days)
    monkeypatch.setattr(fitness, "source", lambda name=None: Spy())
    c.get("/api/fitness?days=1", headers=KEY)
    c.get("/api/fitness?days=9999", headers=KEY)
    assert seen == [route.MIN_DAYS + 90, route.MAX_DAYS + 90]
