"""Drafts waiting for the owner to approve, one JSON file each.

Nothing in this module or the jobs around it can send. Approving marks a draft;
a later pass asks the mail source for a DRAFT in the owner's account, which the
owner opens and sends themselves.

The drafter owns the draft files; a decision lives in a sibling `.state.json`
so a torn write can never damage a draft. Every write is temp file then rename.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import time
from pathlib import Path

from .. import config

MAX_AGE = 14 * 86400          # a reply to a fortnight-old thread is worse than none
STATES = ("pending", "approved", "rejected")
EDITABLE = ("reply",)
EDIT_MAX = 40000


def queue_dir() -> Path:
    raw = config.env("MAIL_DIR") or config.get("mail_queue_dir")
    if raw:
        return Path(str(raw)).expanduser()
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "agent-companion" / "mail"


def _state_path(p: Path) -> Path:
    return p.with_suffix(".state.json")


def _read_json(p: Path) -> dict | None:
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _write(p: Path, data: dict) -> bool:
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, p)
        return True
    except OSError:
        return False


def _files() -> list[Path]:
    try:
        return sorted(p for p in queue_dir().iterdir()
                      if p.suffix == ".json" and not p.name.endswith(".state.json"))
    except OSError:
        return []


def _account(draft: dict) -> str:
    """The mailbox a draft belongs to. Older entries were the owner's."""
    return str(draft.get("account") or "owner")


def _find(draft_id: str, account: str | None = None) -> Path | None:
    for p in _files():
        d = _read_json(p)
        if d and d.get("id") == draft_id and (account is None or _account(d) == account):
            return p
    return None


def write_draft(item: dict, now: float | None = None) -> bool:
    """File one draft. Needs an id and a reply; the id becomes a filename, so
    anything outside a safe charset is replaced rather than trusted."""
    if not item.get("id") or not item.get("reply"):
        return False
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(item["id"]))[:120].strip("._")
    if not safe:
        return False
    account = _account(item)
    safe_account = re.sub(r"[^A-Za-z0-9_-]", "_", account)[:32].strip("._")
    if not safe_account:
        return False
    return _write(queue_dir() / f"{safe_account}--{safe}.json",
                  {**item, "id": str(item["id"]), "account": account, "at": now or time.time()})


def known_ids(account: str | None = None) -> set[str]:
    """Every id ever filed and still on disk, decided or not."""
    return {d["id"] for d in (_read_json(p) for p in _files())
            if d and d.get("id") and (account is None or _account(d) == account)}


def read_queue(now: float | None = None, account: str | None = None) -> list[dict]:
    """Every draft, newest first, with its state folded in. Never raises."""
    now = now or time.time()
    out = []
    for p in _files():
        d = _read_json(p)
        if not d or not d.get("id"):
            continue
        d["account"] = _account(d)
        if account is not None and d["account"] != account:
            continue
        at = d.get("at") or 0
        if isinstance(at, (int, float)) and at and now - at > MAX_AGE:
            continue
        s = _read_json(_state_path(p)) or {}
        d["state"] = s.get("state") if s.get("state") in STATES else "pending"
        d["decided_at"] = s.get("at")
        # This field is part of the queue contract. Folding it in is what
        # keeps an approval pass from creating a second Gmail draft.
        d["gmail_draft_id"] = s.get("gmail_draft_id") or ""
        d["draft_id"] = d["gmail_draft_id"]
        d["drafted_at"] = s.get("drafted_at")
        edits = s.get("edits") if isinstance(s.get("edits"), dict) else {}
        for k in EDITABLE:
            if isinstance(edits.get(k), str):
                d[k] = edits[k]
        d["edited"] = bool(edits)
        out.append(d)
    out.sort(key=lambda x: x.get("at") or 0, reverse=True)
    return out


def _update(draft_id: str, fn, account: str | None = None) -> bool:
    p = _find(draft_id, account)
    if p is None:
        return False
    sp = _state_path(p)
    cur = _read_json(sp) or {}
    if fn(cur) is False:
        return False
    return _write(sp, cur)


def mark(draft_id: str, state: str, now: float | None = None, account: str | None = None) -> bool:
    """Approve or skip. MERGES into the state file: an undo-then-reapprove must
    keep draft_id, or the next pass would make a second copy of the reply."""
    if state not in STATES:
        return False

    def fn(cur):
        cur["state"] = state
        cur["at"] = now or time.time()
    return _update(draft_id, fn, account)


def edit(draft_id: str, fields: dict, now: float | None = None, account: str | None = None) -> bool:
    """Change a PENDING draft's reply. Anything else is refused whole."""
    if not fields or any(k not in EDITABLE or not isinstance(v, str) or not v.strip() or len(v) > EDIT_MAX
                         for k, v in fields.items()):
        return False

    def fn(cur):
        if cur.get("state", "pending") != "pending":
            return False
        cur["edits"] = {**(cur.get("edits") if isinstance(cur.get("edits"), dict) else {}), **fields}
        cur["edited_at"] = now or time.time()
    return _update(draft_id, fn, account)


def mark_drafted(draft_id: str, gmail_draft_id: str, now: float | None = None,
                 account: str | None = None) -> bool:
    def fn(cur):
        cur["gmail_draft_id"] = str(gmail_draft_id)
        cur["drafted_at"] = now or time.time()
    return _update(draft_id, fn, account)


def awaiting_draft(account: str | None = None) -> list[dict]:
    return [d for d in read_queue(account=account) if d["state"] == "approved" and not d["gmail_draft_id"]]


def approve_pending(source) -> int:
    """Create a draft in the owner's account for every approved reply that has
    none, once. Returns how many were made. A lock keeps two passes (the page's
    and the timer's) from both drafting the same reply."""
    qd = queue_dir()
    try:
        qd.mkdir(parents=True, exist_ok=True)
        lock = open(qd / ".approve.lock", "w")
    except OSError:
        return 0
    with lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return 0
        n = 0
        account = str(getattr(source, "account", "owner") or "owner")
        for d in awaiting_draft(account):
            subject = str(d.get("subject") or "")
            if not subject.lower().startswith("re:"):
                subject = "Re: " + subject
            try:
                new_id = source.create_draft(str(d.get("thread") or ""), str(d.get("from") or ""),
                                             subject, str(d.get("reply") or ""))
            except Exception:
                continue
            if mark_drafted(d["id"], new_id, account=account):
                n += 1
        return n


def counts(drafts: list[dict]) -> dict:
    return {"pending": sum(1 for d in drafts if d.get("state") == "pending"), "total": len(drafts)}
