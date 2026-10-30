import datetime

from claude_companion.fitness import summary
from claude_companion.fitness.demo import DemoSource


def rows(sleeps, start="2026-03-02", **extra):
    d0 = datetime.date.fromisoformat(start)
    out = []
    for i, s in enumerate(sleeps):
        r = {"date": (d0 + datetime.timedelta(days=i)).isoformat(), "sleep_min": s, "resting_hr": None,
             "steps": None, "distance_km": None, "weight_kg": None}
        out.append(r)
    return out


def test_zero_missing_and_repeated_nights_are_not_recorded():
    n = summary.nights(rows([400, 0, None, 380, 380, 420]))
    assert [x["recorded"] for x in n] == [True, False, False, True, False, True]
    assert [x["asleep"] for x in n] == [400, None, None, 380, None, 420]


def test_averages_leave_out_unrecorded_nights_and_count_them():
    v = summary.sleep_view(summary.nights(rows([400, 0, None, 380, 380, 420])), goal=420, chart_days=6)
    assert v["avg"]["7"]["min"] == 400.0 and v["avg"]["7"]["nights"] == 3
    assert v["recorded"] == {"nights": 3, "of": 6}
    assert v["debt"]["7"]["min"] == (400 - 420) + (380 - 420) + 0
    assert v["on_target"]["7"] == {"hit": 1, "nights": 3}
    assert [c["asleep"] for c in v["chart"]] == [400, None, None, 380, None, 420]


def test_all_unrecorded_is_none_not_zero():
    v = summary.sleep_view(summary.nights(rows([None, 0, None])), goal=420, chart_days=3)
    assert v["avg"]["7"]["min"] is None and v["debt"]["7"]["min"] is None
    assert v["recorded"] == {"nights": 0, "of": 3}
    assert v["last"]["recorded"] is False and v["last_recorded"] is None


def test_a_night_belongs_to_the_morning_it_ends():
    # Slept Sunday night into Monday: the row dated Monday carries it, and
    # Monday is the weekday a work-day setting is matched against.
    n = summary.nights(rows([410, 300], start="2026-03-01"), work_days={"mon"})   # 2026-03-01 is a Sunday
    assert n[0]["work"] is False and n[1]["work"] is True
    assert n[1]["date"] == "2026-03-02"


def test_work_split_only_when_configured():
    r = rows([420, 360, 480, 300, 450, 330, 390])
    assert summary.work_split(summary.nights(r)) is None
    ws = summary.work_split(summary.nights(r, work_days={"tue", "wed"}))
    assert ws["work"]["nights"] == 2 and ws["off"]["nights"] == 5


def test_view_converts_units_only_at_the_edge():
    d = rows([400, 410, 420])
    d[-1].update(distance_km=10.0, weight_kg=80.0, steps=9000, resting_hr=55)
    d[-2].update(weight_kg=81.0)
    metric = summary.view(d, units="metric")
    imp = summary.view(d, units="imperial")
    assert metric["units"] == {"distance": "km", "weight": "kg"}
    assert metric["weight"]["latest"] == 80.0 and metric["steps"]["km_today"] == 10.0
    assert imp["units"] == {"distance": "mi", "weight": "lb"}
    assert imp["weight"]["latest"] == 176.4 and imp["steps"]["km_today"] == 6.21
    assert imp["weight"]["change30"] == round((80 - 81) * 2.20462, 1)


def test_gaps_stay_gaps_in_every_series():
    d = rows([400, None, 410])
    d[0]["weight_kg"] = 80.0
    v = summary.view(d, units="metric")
    assert v["weight"]["chart"][1]["v"] is None
    assert v["heart"]["latest"] is None and v["heart"]["avg30"] is None
    assert v["steps"]["today"] is None and v["steps"]["week"] is None


def test_demo_has_ninety_days_with_gaps_and_a_view_with_no_nulls_as_text():
    d = DemoSource(today=datetime.date(2026, 3, 30)).daily(90)
    assert len(d) == 90 and d[-1]["date"] == "2026-03-30"
    assert any(r["sleep_min"] is None for r in d)
    v = summary.view(d, units="imperial")
    assert 0 < v["recorded"]["nights"] < 90
    assert "NaN" not in repr(v)


# --- the Home Assistant source, against a canned websocket -------------------

import asyncio  # noqa: E402
import json  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

import pytest  # noqa: E402

from claude_companion import fitness  # noqa: E402
from claude_companion.fitness import home_assistant as ha  # noqa: E402

UTC = ZoneInfo("UTC")


def ms(y, m, d, h=0):
    return int(datetime.datetime(y, m, d, h, tzinfo=UTC).timestamp() * 1000)


class FakeWS:
    def __init__(self, replies, sent):
        self.replies, self.sent = list(replies), sent

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send(self, text):
        self.sent.append(json.loads(text))

    async def recv(self):
        return json.dumps(self.replies.pop(0))


def fake_connect(sent, day_rows, hour_rows, token_ok=True):
    def connect(url):
        sent.append({"url": url})
        stats = day_rows if len(sent) < 4 else hour_rows
        return FakeWS([{"type": "auth_required"}, {"type": "auth_ok" if token_ok else "auth_invalid"},
                       {"id": 1, "type": "result", "success": True, "result": stats}], sent)
    return connect


def make(connect, **kw):
    ents = {"sleep_min": "sensor.s", "steps": "sensor.st", "distance_km": "sensor.di",
            "resting_hr": "sensor.hr", "weight_kg": "sensor.w"}
    return ha.HomeAssistantSource("https://ha.example.com", "tok", ents, connect=connect, tz=UTC,
                                  today=datetime.date(2026, 3, 4), **kw)


def test_ha_parses_a_canned_reply_into_daily_rows():
    sent = []
    day = {"sensor.s": [{"start": ms(2026, 3, 3), "state": 400.0}, {"start": ms(2026, 3, 4), "state": 0.0}],
           "sensor.st": [{"start": ms(2026, 3, 4), "state": 8000}], "sensor.di": [{"start": ms(2026, 3, 4), "state": 6.0}]}
    hour = {"sensor.hr": [{"start": ms(2026, 3, 3, 20), "mean": 55.0}, {"start": ms(2026, 3, 3, 9), "mean": 90.0}],
            "sensor.w": [{"start": ms(2026, 3, 4, 20), "mean": 80.0}]}
    src = make(fake_connect(sent, day, hour), units=None)
    rows = src.daily(3)
    assert [r["date"] for r in rows] == ["2026-03-02", "2026-03-03", "2026-03-04"]
    assert rows[1]["sleep_min"] == 400.0 and rows[0]["sleep_min"] is None
    assert rows[2]["sleep_min"] == 0.0 and rows[2]["steps"] == 8000
    assert rows[1]["resting_hr"] == 55.0          # the 20:00 hour, not the 09:00 one
    assert rows[2]["weight_kg"] == 80.0 and rows[2]["distance_km"] == 6.0
    assert src.freshness() == {"source": "Home Assistant", "as_of": "2026-03-04"}
    auth = [m for m in sent if m.get("type") == "auth"]
    assert auth and auth[0]["access_token"] == "tok"
    asks = [m for m in sent if m.get("type") == "recorder/statistics_during_period"]
    assert [a["period"] for a in asks] == ["day", "hour"]
    assert sent[0]["url"] == "wss://ha.example.com/api/websocket"


def test_ha_units_are_converted_to_metric():
    sent = []
    day = {"sensor.s": [{"start": ms(2026, 3, 4), "state": 7.0}], "sensor.di": [{"start": ms(2026, 3, 4), "state": 10.0}]}
    hour = {"sensor.w": [{"start": ms(2026, 3, 4, 20), "mean": 176.0}]}
    r = make(fake_connect(sent, day, hour), units={"sleep": "h", "distance": "mi", "weight": "lb"}).daily(1)[0]
    assert r["sleep_min"] == 420.0 and r["distance_km"] == 16.093 and r["weight_kg"] == 79.832


def test_ha_refused_token_is_said():
    with pytest.raises(PermissionError):
        make(fake_connect([], {}, {}, token_ok=False)).daily(2)


def test_ha_never_opens_a_socket_under_pytest():
    with pytest.raises(RuntimeError, match="no network"):
        make(None).daily(2)


def test_ha_config_needs_url_and_token_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_COMPANION_FITNESS_SOURCE", "home_assistant")
    with pytest.raises(LookupError, match="ha_url"):
        fitness.source()
    tok = tmp_path / "tok"
    tok.write_text("secret\n")
    monkeypatch.setenv("CLAUDE_COMPANION_HA_URL", "http://ha.example.com:8123")
    monkeypatch.setenv("CLAUDE_COMPANION_HA_TOKEN_FILE", str(tok))
    monkeypatch.setenv("CLAUDE_COMPANION_FITNESS_SLEEP_ENTITY", "sensor.x")
    s = fitness.source()
    assert s.entities == {"sleep_min": "sensor.x"} and s._token == "secret"


def test_source_registry():
    assert fitness.registered() == ["demo", "home_assistant"]
    assert fitness.source("") is None
    with pytest.raises(LookupError):
        fitness.source("nope")
    assert fitness.source("demo").demo is True
