"""Mail drafting, with a seam for the mail provider.

THE WALL: nothing here sends. A MailSource can list, read and create a DRAFT in
the owner's account; the owner opens it and sends it themselves. The interface
has no method that sends, replies or forwards, and tests/test_mail.py fails if
any registered source grows an attribute that looks like one.

To add a provider (Outlook, say) write `mail/outlook.py` with a class that
satisfies MailSource and register it below; docs/mail-outlook.md says how.
"""
from __future__ import annotations

import re
from typing import Callable, Protocol

from .. import config

_ACCOUNT = re.compile(r"[a-z][a-z0-9_]{0,31}")


class MailSource(Protocol):
    name: str
    account: str

    def list_new(self, since: float) -> list[dict]:
        """New mail since the epoch time: {id, thread, from, to, subject, date, snippet}."""

    def read(self, msg_id: str) -> dict:
        """One message, with its text under "body"."""

    def create_draft(self, thread_id: str, to: str, subject: str, body: str) -> str:
        """Put a draft in the owner's drafts folder, in that thread. Returns its id."""


_FACTORIES: dict[str, Callable[[str], MailSource]] = {}


def register(name: str, factory: Callable[[str], MailSource]) -> None:
    _FACTORIES[name] = factory


def registered() -> list[str]:
    return sorted(_FACTORIES)


def source(name: str, account: str = "owner") -> MailSource:
    try:
        return _FACTORIES[name](account)
    except KeyError:
        raise KeyError(f"no mail source {name!r}; have {registered()}") from None


def _gmail(account: str) -> MailSource:
    from .gmail import GmailSource
    return GmailSource(account)


def _imap(account: str) -> MailSource:
    from .imap import ImapSource
    return ImapSource(account)


def accounts() -> dict[str, dict]:
    """Configured account ids and their source settings."""
    raw = config.get("mail_accounts", {})
    if not isinstance(raw, dict):
        return {}
    out = {}
    for account, item in raw.items():
        if not isinstance(account, str) or not _ACCOUNT.fullmatch(account):
            continue
        if isinstance(item, str):
            out[account] = {"source": item}
        elif isinstance(item, dict):
            out[account] = item
    return out


def account_source(account: str) -> str:
    """The configured source name for one account."""
    item = accounts().get(account)
    name = item.get("source") if item else None
    if not isinstance(name, str) or name not in _FACTORIES:
        raise LookupError("Mail is not set up for this account.")
    return name


def source_for(account: str) -> MailSource:
    """The configured source for one account."""
    return source(account_source(account), account)


def ready(account: str) -> bool:
    """Whether the account has enough local setup to use its source."""
    try:
        name = account_source(account)
        if name == "imap":
            from . import imap
            return imap.ready(account)
        return True
    except (LookupError, ValueError):
        return False


register("gmail", _gmail)
register("imap", _imap)
