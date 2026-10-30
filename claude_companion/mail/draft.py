"""The drafting pass: new mail in, drafts for the owner to approve out.

list and read go through the MailSource; the writing step is one text-only
`claude -p` run that holds NO mail tool, so mail someone hostile wrote cannot
make it do anything but write a worse draft that the owner reads before
anything is approved."""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from . import claude, queue, validate, voice_guide

log = logging.getLogger(__name__)

PROMPT = Path(__file__).with_name("prompts") / "draft.md"
MAX_MESSAGES = 25
BODY_MAX = 6000
_NO_REPLY = re.compile(r"(?:^|[^a-z])(?:no[._-]?reply|do[._-]?not[._-]?reply)(?:[^a-z]|$)", re.I)


def watermark_path(account: str = "owner") -> Path:
    """One polling watermark per mailbox; the owner's old name stays put."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(account or "owner"))[:32].strip("._") or "owner"
    suffix = "" if safe == "owner" else "-" + safe
    return queue.queue_dir() / (".last-run" + suffix)


def _since(account: str = "owner") -> float:
    try:
        return float(watermark_path(account).read_text())
    except (OSError, ValueError):
        return time.time() - 2 * 86400


def _block(m: dict) -> str:
    return (f"=== MESSAGE id={m['id']} ===\nFrom: {m.get('from', '')}\nTo: {m.get('to', '')}\n"
            f"Subject: {m.get('subject', '')}\nDate: {m.get('date', '')}\n\n"
            f"{str(m.get('body') or m.get('snippet') or '')[:BODY_MAX]}\n=== END ===")


def _array(text: str) -> list:
    a, b = text.find("["), text.rfind("]")
    if a < 0 or b < a:
        return []
    try:
        out = json.loads(text[a:b + 1])
    except ValueError:
        return []
    return out if isinstance(out, list) else []


def run(source) -> int:
    """One pass. Returns how many drafts were queued. Raises if the source or
    claude fails, so the caller does not advance the watermark."""
    started = time.time()
    account = str(getattr(source, "account", "owner") or "owner")
    seen = queue.known_ids(account)
    new = []
    for raw in source.list_new(_since(account)):
        if not isinstance(raw, dict) or not raw.get("id") or str(raw["id"]) in seen:
            continue
        m = validate.check_message(raw)
        if m is None:
            log.warning("mail: skipped a message whose id, thread or addresses failed validation: %.60r", raw.get("id"))
            continue
        if not _NO_REPLY.search(m["from"]):
            new.append(m)
    new = new[:MAX_MESSAGES]
    n = 0
    if new:
        full = {}
        for m in new:
            body = source.read(str(m["id"]))
            # metadata comes from the list only; the read gives back the body text and nothing else
            text = body.get("body") if isinstance(body, dict) else ""
            full[m["id"]] = {**m, "body": str(text or "")}
        prompt = (PROMPT.read_text().replace("$VOICE", voice_guide.load() or "(no guide yet: plain, brief, polite)")
                  .replace("$MESSAGES", "\n\n".join(_block(m) for m in full.values())))
        for d in _array(claude.run(prompt, [])):
            # The id decides what the draft is about, and it must be one we read:
            # sender, subject and thread come from the mail, never from the model.
            m = full.get(str(d.get("id"))) if isinstance(d, dict) else None
            reply = str(d.get("reply") or "").strip() if m else ""
            if not m or not reply:
                continue
            if queue.write_draft({"id": m["id"], "account": account, "thread": m.get("thread") or "", "from": m.get("from") or "",
                                  "to": m.get("to") or "", "subject": m.get("subject") or "",
                                  "received": str(m.get("date") or "")[:32],
                                  "why": str(d.get("why") or "").strip().splitlines()[0][:300] if d.get("why") else "",
                                  "reply": reply}):
                n += 1
    queue.queue_dir().mkdir(parents=True, exist_ok=True)
    watermark_path(account).write_text(str(started))
    return n
