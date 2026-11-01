"""The money panel, with a seam for where the figures come from.

THE WALL: read only. A MoneySource reports accounts, net worth, cash flow,
spending, recurring charges and what is coming. It has no method that moves
money, pays, transfers or changes a record, and tests/test_money.py fails on any
source attribute that looks like one.

A figure a source does not have is None, never 0. The server sends it as null
and the page draws a dash. docs/money-sources.md says how to write a real
source; the only one shipped is the made-up DemoSource.

Sign convention: `balance` is what the account shows. For credit and loan
accounts that is the amount owed, as a positive number; net worth subtracts it.
"""
from __future__ import annotations

from typing import Callable, Protocol

KINDS = ("checking", "savings", "credit", "loan", "investment")
LIABILITIES = ("credit", "loan")


class MoneySource(Protocol):
    name: str

    def accounts(self) -> list[dict]:
        """{id, name, kind (one of KINDS), balance, currency, updated}."""

    def net_worth(self, days: int = 90) -> dict:
        """{total, history: [{date, total}]}, oldest first."""

    def cash_flow(self, months: int = 6) -> list[dict]:
        """[{month 'YYYY-MM', income, spending}], oldest first."""

    def spending_by_category(self, start: str, end: str) -> list[dict]:
        """[{category, amount}] for ISO dates start..end, biggest first."""

    def recurring(self) -> list[dict]:
        """[{name, amount, every ('weekly'|'monthly'|'yearly'), next_date}]."""

    def upcoming(self, days: int = 14) -> list[dict]:
        """[{date, name, amount}] due in the next days, soonest first."""

    def freshness(self) -> dict:
        """{source, as_of}: where the figures came from and how old they are."""


_FACTORIES: dict[str, Callable[[], MoneySource]] = {}


def register(name: str, factory: Callable[[], MoneySource]) -> None:
    _FACTORIES[name] = factory


def registered() -> list[str]:
    return sorted(_FACTORIES)


def source(name: str) -> MoneySource:
    try:
        return _FACTORIES[name]()
    except KeyError:
        raise KeyError(f"no money source {name!r}; have {registered()}") from None


def signed(account: dict) -> float | None:
    """The balance as it counts toward net worth, or None when unknown."""
    b = account.get("balance")
    if b is None:
        return None
    return -b if account.get("kind") in LIABILITIES else b


def _demo() -> MoneySource:
    from .demo import DemoSource
    return DemoSource()


register("demo", _demo)
