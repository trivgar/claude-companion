"""The Deferred dashboard view: a locked Markdown backlog, never a shell command."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import JSONResponse

from .. import config, deferred
from . import auth


TITLE = "Deferred"
router = APIRouter()


def _owner_label() -> str:
    """A configurable, non-secret label for entries created from the page."""
    return str(config.get("deferred_by") or config.get("name") or "owner").strip()[:120] or "owner"


@router.get("/api/deferred", dependencies=[Depends(auth.require_token)])
def get_deferred():
    entries = [deferred.public(entry) for entry in deferred.read()]
    entries.sort(key=lambda entry: entry.get("timestamp", ""), reverse=True)
    return JSONResponse({"entries": entries}, headers={"Cache-Control": "no-store"})


@router.post("/api/deferred", dependencies=[Depends(auth.require_write)])
def add_deferred(body: dict = Body(default={})):
    text = body.get("text") if isinstance(body, dict) else ""
    when = body.get("when") if isinstance(body, dict) else ""
    for_project = body.get("for") if isinstance(body, dict) else ""
    try:
        # The page has no checkout of its own: the server's folder says nothing.
        entry = deferred.add(str(text or ""), str(when or ""), _owner_label(), str(for_project or ""),
                             project="dashboard", branch="-")
    except deferred.DeferredError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "entry": entry}
