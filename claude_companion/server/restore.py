"""Owner-only dashboard access to the local session restore core."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import restore as core
from . import auth

router = APIRouter()


@router.get("/api/restore", dependencies=[Depends(auth.require_token)])
def status():
    return core.status()


@router.post("/api/restore", dependencies=[Depends(auth.require_write)])
def restore_sessions():
    return core.restore()
