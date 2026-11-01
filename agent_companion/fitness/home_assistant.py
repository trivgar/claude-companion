"""Home Assistant as a fitness source: its long-term statistics, over the websocket.

HA keeps raw history for days but statistics for good, and its REST API does
not serve them: only the websocket does (`recorder/statistics_during_period`).
One connection per read: authenticate, ask, close. The server caches.

Config: ha_url, ha_token_file (a path to a 600 file holding a long-lived token;
never the token itself), and the optional entity ids fitness_sleep_entity,
fitness_hr_entity, fitness_steps_entity, fitness_distance_entity,
fitness_weight_entity. Units default to minutes, km and kg; say otherwise with
fitness_sleep_unit (min|h), fitness_distance_unit (km|mi|m|ft),
fitness_weight_unit (kg|lb). Totals (sleep, steps, distance) are read as the
day's last value; measurements (heart rate, weight) as the mean of the hour
starting at fitness_read_hour (default 20), after the morning's post and before
the next night. A daily mean would mix in the day before.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import sys
from pathlib import Path

from .. import config

TOTALS = {"sleep_min": "fitness_sleep_entity", "steps": "fitness_steps_entity",
          "distance_km": "fitness_distance_entity"}
MEASURES = {"resting_hr": "fitness_hr_entity", "weight_kg": "fitness_weight_entity"}
TO_KM = {"km": 1.0, "mi": 1.609344, "m": 0.001, "ft": 0.0003048}
TO_KG = {"kg": 1.0, "lb": 0.45359237}
TO_MIN = {"min": 1.0, "h": 60.0}


def ws_url(url: str) -> str:
    url = url.rstrip("/")
    return ("wss" + url[5:] if url.startswith("https") else "ws" + url[4:]) + "/api/websocket"


def _default_connect(timeout: float):
    if "pytest" in sys.modules:
        raise RuntimeError("no network under pytest: inject connect=")
    import websockets      # pip install websockets (the 'home_assistant' extra)
    return lambda url: websockets.connect(url, max_size=2 ** 25, open_timeout=timeout)


async def fetch(url: str, token: str, req: dict, connect, timeout: float) -> dict:
    async with connect(url) as ws:
        await asyncio.wait_for(ws.recv(), timeout)                      # auth_required
        await ws.send(json.dumps({"type": "auth", "access_token": token}))
        ok = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if ok.get("type") != "auth_ok":
            raise PermissionError("Home Assistant refused the token")
        await ws.send(json.dumps(dict(req, id=1)))
        r = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if not r.get("success"):
            raise ValueError((r.get("error") or {}).get("message") or "Home Assistant refused the request")
        return r.get("result") or {}


def _when(start, tz) -> datetime.datetime | None:
    """A statistics row's start: epoch milliseconds (or seconds), or ISO."""
    try:
        if isinstance(start, (int, float)):
            return datetime.datetime.fromtimestamp(start / 1000 if start > 1e11 else start, tz)
        return datetime.datetime.fromisoformat(str(start).replace("Z", "+00:00")).astimezone(tz)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def day_values(rows, tz, key: str = "state") -> dict[str, float]:
    """{'YYYY-MM-DD': value} from DAILY statistics: the day's last value."""
    out = {}
    for r in rows or []:
        t, v = _when(r.get("start"), tz), r.get(key)
        if t is not None and isinstance(v, (int, float)) and not isinstance(v, bool):
            out[t.date().isoformat()] = float(v)
    return out


def evening_values(rows, tz, hour: int) -> dict[str, float]:
    """{'YYYY-MM-DD': mean} from HOURLY statistics, the hour starting at `hour` local."""
    out = {}
    for r in rows or []:
        t, v = _when(r.get("start"), tz), r.get("mean")
        if t is not None and t.hour == hour and isinstance(v, (int, float)) and not isinstance(v, bool):
            out[t.date().isoformat()] = float(v)
    return out


class HomeAssistantSource:
    name = "Home Assistant"
    demo = False

    def __init__(self, url: str, token: str, entities: dict[str, str], units: dict | None = None,
                 connect=None, tz=None, read_hour: int = 20, timeout: float = 20, today=None):
        self.url, self._token, self.entities = url, token, {k: v for k, v in entities.items() if v}
        self.units = {"sleep": "min", "distance": "km", "weight": "kg", **(units or {})}
        self._connect, self._timeout, self.read_hour = connect, timeout, read_hour
        self.tz = tz or datetime.datetime.now().astimezone().tzinfo
        self._today = today
        self._as_of: str | None = None

    def _stats(self, ids, start, end, period, types) -> dict:
        req = {"type": "recorder/statistics_during_period", "start_time": start.isoformat(),
               "end_time": end.isoformat(), "period": period, "statistic_ids": list(ids), "types": list(types)}
        connect = self._connect or _default_connect(self._timeout)
        return asyncio.run(fetch(ws_url(self.url), self._token, req, connect, self._timeout))

    def daily(self, days: int) -> list[dict]:
        if not self.entities:
            raise LookupError("No Home Assistant entities are set: see docs/fitness-sources.md")
        today = self._today or datetime.datetime.now(self.tz).date()
        first = today - datetime.timedelta(days=days - 1)
        start = datetime.datetime.combine(first, datetime.time(0), self.tz)
        end = datetime.datetime.combine(today + datetime.timedelta(days=1), datetime.time(0), self.tz)
        tot = {k: e for k, e in ((k, self.entities.get(k)) for k in TOTALS) if e}
        mea = {k: e for k, e in ((k, self.entities.get(k)) for k in MEASURES) if e}
        vals: dict[str, dict] = {}
        if tot:
            res = self._stats(tot.values(), start, end, "day", ["state"])
            for k, e in tot.items():
                vals[k] = day_values(res.get(e), self.tz)
        if mea:
            res = self._stats(mea.values(), start, end, "hour", ["mean"])
            for k, e in mea.items():
                vals[k] = evening_values(res.get(e), self.tz, self.read_hour)
        scale = {"sleep_min": TO_MIN[self.units["sleep"]], "distance_km": TO_KM[self.units["distance"]],
                 "weight_kg": TO_KG[self.units["weight"]]}
        rows = []
        for i in range(days):
            d = (first + datetime.timedelta(days=i)).isoformat()
            row = {"date": d}
            for k in (*TOTALS, *MEASURES):
                v = vals.get(k, {}).get(d)
                row[k] = None if v is None else round(v * scale.get(k, 1.0), 3)
            rows.append(row)
        latest = [r["date"] for r in rows if any(r[k] is not None for k in (*TOTALS, *MEASURES))]
        self._as_of = latest[-1] if latest else None
        return rows

    def freshness(self) -> dict:
        return {"source": self.name, "as_of": self._as_of}


def from_config() -> HomeAssistantSource:
    """Built from config. LookupError says which key is missing."""
    url = str(config.get("ha_url") or "").strip()
    tfile = str(config.get("ha_token_file") or "").strip()
    if not url or not tfile:
        raise LookupError("Home Assistant needs ha_url and ha_token_file in the config")
    try:
        token = Path(tfile).expanduser().read_text().strip()
    except OSError:
        raise LookupError("ha_token_file cannot be read") from None
    if not token:
        raise LookupError("ha_token_file is empty")
    entities = {k: str(config.get(c) or "").strip() for k, c in {**TOTALS, **MEASURES}.items()}
    units = {"sleep": str(config.get("fitness_sleep_unit") or "min").lower(),
             "distance": str(config.get("fitness_distance_unit") or "km").lower(),
             "weight": str(config.get("fitness_weight_unit") or "kg").lower()}
    if units["sleep"] not in TO_MIN or units["distance"] not in TO_KM or units["weight"] not in TO_KG:
        raise LookupError("a fitness_*_unit key has a unit that is not supported: see docs/fitness-sources.md")
    try:
        hour = int(config.get("fitness_read_hour") or 20)
    except ValueError:
        hour = 20
    return HomeAssistantSource(url, token, entities, units, read_hour=hour)
