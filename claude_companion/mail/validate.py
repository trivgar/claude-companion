"""What may cross from mail someone else wrote into a command or a queue file.

Ids, addresses and subjects are checked here before a prompt is built or a
draft is written. A value that fails is refused, never repaired."""
from __future__ import annotations

import re

ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
# One mailbox, optionally "Name <addr>". No control chars, no angle brackets inside, no spaces in the address.
_ADDR = r"[A-Za-z0-9._%+'-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63}){1,8}"
EMAIL = re.compile(_ADDR)
MAILBOX = re.compile(r"(?:[^<>\r\n\x00-\x1f\x7f]{0,120}\s<(" + _ADDR + r")>|<(" + _ADDR + r")>|" + _ADDR + r")")
SUBJECT_MAX = 300
_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")


def valid_id(v) -> bool:
    return isinstance(v, str) and ID.fullmatch(v) is not None


def valid_address(v) -> bool:
    """A single address or "Name <addr>" (from headers); a to list is each one checked."""
    return isinstance(v, str) and MAILBOX.fullmatch(v.strip()) is not None


def bare_address(v) -> str:
    """Only the address out of "Name <addr>", or "" when it is not one mailbox.
    What goes to a model is the address alone: a display name is someone
    else's words and never crosses into a prompt."""
    if not isinstance(v, str):
        return ""
    m = MAILBOX.fullmatch(v.strip())
    if not m:
        return ""
    return (m.group(1) or m.group(2) or m.group(0)).strip()


def valid_to(v) -> bool:
    """One or more comma-separated addresses."""
    if not isinstance(v, str) or not v.strip() or _CTRL.search(v):
        return False
    parts = [p.strip() for p in v.split(",")]
    return all(valid_address(p) for p in parts)


def clean_subject(v) -> str:
    s = _CTRL.sub(" ", str(v or ""))
    return " ".join(s.split())[:SUBJECT_MAX]


def check_message(m: dict) -> dict | None:
    """The cleaned metadata of a listed message, or None if any part fails."""
    if not isinstance(m, dict) or not valid_id(str(m.get("id") or "")):
        return None
    thread = str(m.get("thread") or m.get("id"))
    frm, to = str(m.get("from") or ""), str(m.get("to") or "")
    if not valid_id(thread) or not valid_address(frm) or (to and not valid_to(to)):
        return None
    return {"id": str(m["id"]), "thread": thread, "from": frm.strip(), "to": to.strip(),
            "subject": clean_subject(m.get("subject")), "date": _CTRL.sub(" ", str(m.get("date") or ""))[:32],
            "snippet": _CTRL.sub(" ", str(m.get("snippet") or ""))[:300]}
