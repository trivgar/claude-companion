"""The Fitness tab: sleep, resting heart rate, steps, distance and weight, from
whatever source the config names (agent_companion/fitness, docs/fitness-sources.md).

The server does every sum; the page only says them. Cached ten minutes: the
numbers change once a morning, and a source may be a slow websocket."""
from __future__ import annotations

import threading
import time

from fastapi import APIRouter, Depends

from .. import config, fitness
from ..fitness import summary
from . import auth

TITLE = "Fitness"
TTL = 600.0
DEFAULT_DAYS, MIN_DAYS, MAX_DAYS = 90, 14, 365
router = APIRouter()
_lock = threading.Lock()
_cache: dict[tuple, tuple[float, dict]] = {}
_clock = time.monotonic


def _int(key: str, default: int, lo: int = 0, hi: int = 10 ** 6) -> int:
    try:
        return min(hi, max(lo, int(float(config.get(key) or default))))
    except (TypeError, ValueError):
        return default


def build(days: int) -> dict:
    try:
        src = fitness.source()
    except LookupError as exc:
        return {"ok": None, "detail": str(exc), "docs": fitness.DOCS}
    if src is None:
        return {"ok": None, "detail": "No fitness source set up", "docs": fitness.DOCS}
    try:
        rows = src.daily(days + 90)       # 90 more, so the 90-night average has a window before it
        fresh = src.freshness()
    except LookupError as exc:
        return {"ok": None, "detail": str(exc), "docs": fitness.DOCS}
    except Exception as exc:               # source down, token refused, a bad answer: say which
        return {"ok": False, "detail": f"{src.name} could not be read: {str(exc)[:80]}"}
    units = "imperial" if str(config.get("units") or "").lower() == "imperial" else "metric"
    goal = _int("fitness_sleep_goal_min", summary.GOAL_MIN, 60, 1200)
    v = summary.view(rows, units=units, goal=goal, chart_days=days,
                     work_days=summary.work_days_from(config.get("fitness_work_days")))
    return {"ok": True, "demo": bool(getattr(src, "demo", False)), **fresh, **v}


@router.get("/api/fitness", dependencies=[Depends(auth.require_token)])
def get_fitness(days: int = DEFAULT_DAYS):
    days = min(MAX_DAYS, max(MIN_DAYS, days))
    cfg = (str(config.get("fitness_source")), str(config.get("units")), days,
           str(config.get("fitness_work_days")), str(config.get("fitness_sleep_goal_min")))
    with _lock:
        hit = _cache.get(cfg)
        if hit and _clock() - hit[0] < TTL:
            return hit[1]
    out = build(days)
    with _lock:
        _cache[cfg] = (_clock(), out)
    return out
