"""Owner-only HTTP interface for the local commit scoreboard."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import JSONResponse

from .. import scoreboard as core
from . import auth, sessions

TITLE = "Scoreboard"
router = APIRouter()


def _session_names() -> dict[str, str]:
    """Use a live session's display name when its commit hook id still exists."""
    try:
        rows = sessions.order(sessions._parts()["reader"].snapshot())
    except Exception:
        return {}
    return {str(row.get("session_id")): str(row.get("speaker") or row.get("name") or "")
            for row in rows if row.get("session_id") and (row.get("speaker") or row.get("name"))}


@router.get("/api/scoreboard", dependencies=[Depends(auth.require_token)])
def get_scoreboard():
    return JSONResponse(core.summary(session_names=_session_names()), headers={"Cache-Control": "no-store"})


@router.post("/api/scoreboard/mark", dependencies=[Depends(auth.require_write)])
def mark_commit(body: dict = Body(default={})):
    if not isinstance(body, dict):
        raise HTTPException(400, "a mark needs a commit and good or bad")
    try:
        return {"ok": True, **core.mark(body.get("sha"), body.get("mark"))}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
