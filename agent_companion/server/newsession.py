"""Owner-only routes for starting a configured Claude Code session."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException

from .. import newsession
from . import auth

router = APIRouter()


@router.get("/api/new-session/projects", dependencies=[Depends(auth.require_token)])
def projects():
    configured = newsession.settings()
    return {"projects": newsession.project_rows(configured.projects), "default_model": configured.default_model}


@router.post("/api/new-session", dependencies=[Depends(auth.require_write)])
def start(body: dict = Body(default={})):
    # A plain def: FastAPI runs it in a worker thread, so the up-to-10 s start
    # check never stalls the event loop for every other request.
    if not isinstance(body, dict):
        raise HTTPException(400, "a project is required")
    configured = newsession.settings()
    project = newsession.find_project(body.get("project"), configured.projects)
    if project is None:
        raise HTTPException(400, "unknown configured project")
    prompt = body.get("prompt", "")
    result = newsession.launch(project, configured.default_model, prompt)
    if not result.ok:
        raise HTTPException(502, result.detail)
    return {"ok": True, "session": result.session}
