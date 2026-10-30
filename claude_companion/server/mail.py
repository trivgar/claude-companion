"""The Mail tab: drafted replies waiting for the owner.

Approving marks a reply; a pass then creates a DRAFT in the owner's own mail
account, and the owner sends it from there. There is no send route."""
from __future__ import annotations

import subprocess
import sys

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from .. import addons
from ..mail import queue, ready
from . import auth

TITLE = "Mail"
router = APIRouter()


def _kick(account: str) -> None:
    """Make the draft now rather than at the next timer run. Never under pytest;
    the timer's pass is the backstop if this fails."""
    if "pytest" in sys.modules:
        return
    try:
        subprocess.Popen([sys.executable, "-m", "claude_companion.mail", "approve", account],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError:
        pass


def _account(request: Request) -> str:
    """The requester's mailbox. A plug-in supplies a non-owner's mapping."""
    if auth.is_owner(request):
        return "owner"
    person = addons.person(request) or {}
    account = person.get("mail_account", person.get("id"))
    account = str(account or "")
    # The owner's account is never a shared mailbox. An add-on can select a
    # configured non-owner account for its person, but cannot widen this one.
    return "" if account == "owner" else account


def _ready(account: str) -> bool:
    return bool(account and ready(account))


@router.get("/api/mail", dependencies=[Depends(auth.require_person)])
def list_mail(request: Request):
    account = _account(request)
    if not _ready(account):
        return {"drafts": [], "counts": {"pending": 0, "total": 0}, "setup": False,
                "detail": "Mail is not set up for this account."}
    drafts = queue.read_queue(account=account)
    return {"drafts": drafts, "counts": queue.counts(drafts), "setup": True}


def _decide(draft_id: str, state: str, account: str) -> dict:
    if not queue.mark(draft_id, state, account=account):
        raise HTTPException(404, "no such draft")
    return {"ok": True, "state": state}


@router.post("/api/mail/{draft_id}", dependencies=[Depends(auth.require_person_write)])
def decide(request: Request, draft_id: str, body: dict = Body(default={})):
    account = _account(request)
    if not _ready(account):
        raise HTTPException(409, "Mail is not set up for this account.")
    state = str(body.get("state") or "") if isinstance(body, dict) else ""
    if state not in ("approved", "rejected", "pending"):
        raise HTTPException(400, "state must be approved, rejected or pending")
    out = _decide(draft_id, state, account)
    if state == "approved":
        _kick(account)
    return out


@router.post("/api/mail/{draft_id}/edit", dependencies=[Depends(auth.require_person_write)])
def edit(request: Request, draft_id: str, body: dict = Body(default={})):
    account = _account(request)
    if not _ready(account):
        raise HTTPException(409, "Mail is not set up for this account.")
    fields = body.get("fields") if isinstance(body, dict) else None
    if fields is None and isinstance(body, dict) and "reply" in body:
        fields = {"reply": body["reply"]}
    if not isinstance(fields, dict) or not fields:
        raise HTTPException(400, "fields must be text")
    if not queue.edit(draft_id, fields, account=account):
        raise HTTPException(409, "only a pending draft can be edited")
    return {"ok": True}
