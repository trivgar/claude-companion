"""The Money tab: read only. Every route here is a GET; nothing writes.

The source is the `money_source` setting. Empty means none is set up, and every
route says so. A figure the source does not have goes out as null, never 0."""
from __future__ import annotations

import math
from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from .. import config, money
from . import auth

TITLE = "Money"
router = APIRouter()


def clean(v):
    """None for anything that is not a finite number where a number should be."""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, dict):
        return {k: clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [clean(x) for x in v]
    return v


def _source():
    name = str(config.get("money_source", "") or "").strip()
    if not name:
        return None, {"configured": False, "message": "No money source set up", "docs": "docs/money-sources.md"}
    try:
        return money.source(name), None
    except KeyError as exc:
        return None, {"configured": False, "message": str(exc.args[0]), "docs": "docs/money-sources.md"}


def _answer(key: str, call):
    src, none = _source()
    if src is None:
        return none
    try:
        data = call(src)
    except Exception as exc:        # a broken source degrades one panel, not the server
        return {"configured": True, "error": f"{type(exc).__name__}: {exc}"[:200], key: None}
    return {"configured": True, key: clean(data)}


@router.get("/api/money/accounts", dependencies=[Depends(auth.require_token)])
def accounts():
    return _answer("accounts", lambda s: s.accounts())


@router.get("/api/money/net_worth", dependencies=[Depends(auth.require_token)])
def net_worth(days: int = 90):
    return _answer("net_worth", lambda s: s.net_worth(max(1, min(days, 730))))


@router.get("/api/money/cash_flow", dependencies=[Depends(auth.require_token)])
def cash_flow(months: int = 6):
    return _answer("cash_flow", lambda s: s.cash_flow(max(1, min(months, 36))))


@router.get("/api/money/categories", dependencies=[Depends(auth.require_token)])
def categories(start: str = "", end: str = ""):
    today = date.today()
    try:
        s = date.fromisoformat(start) if start else today.replace(day=1)
        e = date.fromisoformat(end) if end else today
    except ValueError:
        raise HTTPException(400, "start and end are ISO dates")
    if e < s:
        raise HTTPException(400, "end is before start")
    out = _answer("categories", lambda src: src.spending_by_category(s.isoformat(), e.isoformat()))
    out["start"], out["end"] = s.isoformat(), e.isoformat()
    return out


@router.get("/api/money/recurring", dependencies=[Depends(auth.require_token)])
def recurring():
    return _answer("recurring", lambda s: s.recurring())


@router.get("/api/money/upcoming", dependencies=[Depends(auth.require_token)])
def upcoming(days: int = 14):
    return _answer("upcoming", lambda s: s.upcoming(max(1, min(days, 90))))


@router.get("/api/money/freshness", dependencies=[Depends(auth.require_token)])
def freshness():
    src, none = _source()
    if src is None:
        return none
    try:
        f = clean(src.freshness())
    except Exception as exc:
        return {"configured": True, "error": f"{type(exc).__name__}: {exc}"[:200]}
    return {"configured": True, "demo": bool(getattr(src, "demo", False)), **f}
