"""Web push: the page's public key, a device subscribing, and a test push."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ..webpush import PushStore
from . import auth
from .sessions import data_dir

TITLE = "Push"
router = APIRouter()


def store() -> PushStore:
    return PushStore(data_dir())


@router.get("/api/push/key", dependencies=[Depends(auth.require_token)])
def key():
    s = store()
    return {"key": s.public_key(), "devices": s.devices()}


@router.post("/api/push/subscribe", dependencies=[Depends(auth.require_write)])
async def subscribe(request: Request):
    try:
        sub = await request.json()
    except ValueError:
        sub = None
    s = store()
    if not s.subscribe(sub):
        raise HTTPException(400, "not a push subscription")
    return {"ok": True, "devices": s.devices()}


@router.post("/api/push/test", dependencies=[Depends(auth.require_write)])
def test():
    n = store().send("Companion", "Notifications are on.", url="/", tag="push-test")
    return {"ok": True, "sent": n}
