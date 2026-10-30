"""A made-up household, so the panel can be seen before a real source exists.

Every figure is round and invented. Nothing here is anyone's money. The panel
labels it DEMO wherever it shows.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

from . import signed

CATEGORIES = [("Housing", 1200), ("Groceries", 600), ("Transport", 300),
              ("Dining out", 250), ("Utilities", 200), ("Fun", 150)]
INCOME = 4000
SPENDING = sum(a for _, a in CATEGORIES)          # 2700, every month
_ACCOUNTS = [
    ("chk", "Everyday checking", "checking", 2500),
    ("sav", "Rainy day savings", "savings", 8000),
    ("crd", "Travel card", "credit", 600),
    ("car", "Car loan", "loan", 7000),
    ("inv", "Index fund", "investment", 20000),
]
# name, amount, every, day of month (monthly) or (month, day) for yearly or weekday for weekly
_RECURRING = [
    ("Rent", 1200, "monthly", 1), ("Car loan payment", 350, "monthly", 15),
    ("Streaming", 15, "monthly", 8), ("Gym", 30, "monthly", 20),
    ("Car insurance", 600, "yearly", (11, 5)), ("Farmers market", 40, "weekly", 5),
]


def _monthly(y: int, m: int, d: int) -> date:
    return date(y, m, min(d, calendar.monthrange(y, m)[1]))


def _next(every: str, anchor, today: date) -> date:
    if every == "weekly":
        return today + timedelta(days=(anchor - today.weekday()) % 7)
    if every == "yearly":
        d = date(today.year, anchor[0], anchor[1])
        return d if d >= today else date(today.year + 1, anchor[0], anchor[1])
    d = _monthly(today.year, today.month, anchor)
    if d >= today:
        return d
    y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    return _monthly(y, m, anchor)


def _add_months(d: date, n: int) -> tuple[int, int]:
    i = d.year * 12 + d.month - 1 + n
    return i // 12, i % 12 + 1


class DemoSource:
    name = "demo"
    demo = True

    def __init__(self, today: date | None = None):
        self.today = today or date.today()

    def accounts(self) -> list[dict]:
        return [{"id": i, "name": n, "kind": k, "balance": b, "currency": "USD",
                 "updated": self.today.isoformat()} for i, n, k, b in _ACCOUNTS]

    def _total(self) -> int:
        return sum(signed(a) for a in self.accounts())

    def net_worth(self, days: int = 90) -> dict:
        total = self._total()
        step = 7
        hist = []
        for back in range(days - days % step, -1, -step):
            hist.append({"date": (self.today - timedelta(days=back)).isoformat(),
                         "total": total - 100 * (back // step)})
        if hist[-1]["date"] != self.today.isoformat():
            hist.append({"date": self.today.isoformat(), "total": total})
        return {"total": total, "history": hist}

    def cash_flow(self, months: int = 6) -> list[dict]:
        out = []
        for back in range(months - 1, -1, -1):
            y, m = _add_months(self.today, -back)
            out.append({"month": f"{y:04d}-{m:02d}", "income": INCOME, "spending": SPENDING})
        return out

    def spending_by_category(self, start: str, end: str) -> list[dict]:
        s, e = date.fromisoformat(start), date.fromisoformat(end)
        days = max(0, (e - s).days + 1)
        dim = calendar.monthrange(s.year, s.month)[1]
        if days >= dim:
            days = dim
        out = [{"category": c, "amount": round(a * days / dim)} for c, a in CATEGORIES]
        return sorted(out, key=lambda r: -r["amount"])

    def recurring(self) -> list[dict]:
        return [{"name": n, "amount": a, "every": ev, "next_date": _next(ev, an, self.today).isoformat()}
                for n, a, ev, an in _RECURRING]

    def upcoming(self, days: int = 14) -> list[dict]:
        end = self.today + timedelta(days=days)
        out = []
        for n, a, ev, an in _RECURRING:
            d = _next(ev, an, self.today)
            while d <= end:
                out.append({"date": d.isoformat(), "name": n, "amount": a})
                if ev != "weekly":
                    break
                d += timedelta(days=7)
        return sorted(out, key=lambda r: (r["date"], r["name"]))

    def freshness(self) -> dict:
        return {"source": "demo", "as_of": self.today.isoformat()}
