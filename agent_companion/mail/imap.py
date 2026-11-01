"""Gmail over IMAP for an account configured with a credential-file path.

The source only reads the inbox and appends approved drafts to the Gmail
Drafts folder. Credentials stay outside the configuration file and repository.
"""
from __future__ import annotations

import email
import email.policy
import email.utils
import imaplib
import json
import re
import secrets
import time
from email.message import EmailMessage
from pathlib import Path

from . import accounts, validate

HOST, PORT = "imap.gmail.com", 993
DRAFTS = "[Gmail]/Drafts"
IMAP = imaplib.IMAP4_SSL
MAX_TEXT = 20000
_ACCOUNT = re.compile(r"[a-z][a-z0-9_]{0,31}")
_APPEND_UID = re.compile(rb"APPENDUID\s+\d+\s+(\d+)")


def credentials_file(account: str) -> Path:
    """The configured credential file for one IMAP account."""
    item = accounts().get(account)
    raw = item.get("credentials_file") if item else None
    if not isinstance(raw, str) or not raw.strip():
        raise LookupError("Mail is not set up for this account.")
    return Path(raw).expanduser()


def _credentials(account: str) -> dict:
    try:
        data = json.loads(credentials_file(account).read_text())
    except (OSError, ValueError):
        raise LookupError("Mail is not set up for this account.") from None
    address = str(data.get("address") or "").strip() if isinstance(data, dict) else ""
    password = "".join(str(data.get("password") or "").split()) if isinstance(data, dict) else ""
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", address) or not password:
        raise LookupError("Mail is not set up for this account.")
    return {"address": address, "password": password}


def ready(account: str) -> bool:
    """Whether an account names a readable credential file."""
    try:
        _credentials(account)
    except LookupError:
        return False
    return True


def _login(credentials: dict):
    client = IMAP(HOST, PORT, timeout=20)
    try:
        client.login(credentials["address"], credentials["password"])
    except imaplib.IMAP4.error as exc:
        raise PermissionError("Gmail did not accept this account's credentials.") from exc
    return client


def _headers(raw: bytes) -> EmailMessage:
    return email.message_from_bytes(raw, policy=email.policy.default)


def _message_data(data) -> bytes:
    for item in data or ():
        if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], bytes):
            return item[1]
    raise LookupError("No such message.")


def _text(message) -> str:
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    text = part.get_content()
    if part.get_content_type() == "text/html":
        text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"[ \t]+\n", "\n", text).strip()[:MAX_TEXT]


class ImapSource:
    name = "imap"

    def __init__(self, account: str = "owner"):
        if not _ACCOUNT.fullmatch(account or ""):
            raise ValueError("not a plain account id")
        self.account = account

    def _open(self):
        credentials = _credentials(self.account)
        return credentials, _login(credentials)

    def _fetch(self, client, uid: str):
        client.select("INBOX", readonly=True)
        _, data = client.uid("fetch", str(uid), "(BODY.PEEK[])")
        return _headers(_message_data(data))

    def list_new(self, since: float) -> list[dict]:
        _, client = self._open()
        try:
            client.select("INBOX", readonly=True)
            day = time.strftime("%d-%b-%Y", time.gmtime(since or time.time() - 2 * 86400))
            _, found = client.uid("search", f"(SINCE {day})")
            uids = (found[0] or b"").split()[-40:]
            out = []
            for uid in reversed(uids):
                uid_text = uid.decode("ascii", "ignore")
                _, data = client.uid("fetch", uid_text, "(BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)])")
                header = _headers(_message_data(data))
                out.append({"id": uid_text, "thread": uid_text,
                            "from": str(header["from"] or ""), "to": str(header["to"] or ""),
                            "subject": str(header["subject"] or ""), "date": str(header["date"] or ""),
                            "snippet": ""})
            return out
        finally:
            client.logout()

    def read(self, msg_id: str) -> dict:
        if not validate.valid_id(msg_id):
            raise ValueError("not a message id")
        _, client = self._open()
        try:
            return {"id": msg_id, "body": _text(self._fetch(client, msg_id))}
        finally:
            client.logout()

    def create_draft(self, thread_id: str, to: str, subject: str, body: str) -> str:
        if not validate.valid_id(thread_id):
            raise ValueError("not a message id")
        address = validate.bare_address(to)
        if not address:
            raise ValueError("not an address")
        credentials, client = self._open()
        try:
            original = self._fetch(client, thread_id)
            message = EmailMessage()
            message["From"] = credentials["address"]
            message["To"] = address
            message["Subject"] = validate.clean_subject(subject)
            message["Date"] = email.utils.formatdate(localtime=True)
            message_id = str(original["message-id"] or "")
            if message_id:
                message["In-Reply-To"] = message_id
                message["References"] = f"{original['references'] or ''} {message_id}".strip()
            message.set_content(str(body))
            status, data = client.append(DRAFTS, "\\Draft", imaplib.Time2Internaldate(time.time()), message.as_bytes())
            if status != "OK":
                raise RuntimeError("Gmail did not keep the draft.")
            raw = b" ".join(item for item in data or () if isinstance(item, bytes))
            found = _APPEND_UID.search(raw)
            return found.group(1).decode() if found else "local-" + secrets.token_hex(12)
        finally:
            client.logout()
