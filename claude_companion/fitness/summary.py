"""Everything the fitness page says, computed from a source's daily rows.

NOT RECORDED IS A FACT, NEVER ZERO. A night with no time asleep, zero, or the
same minutes as the morning before (the sensor did not move, so nothing was
posted) is out of every average and of the debt, and the view says how many
nights were recorded. A night belongs to the MORNING IT ENDS: row `date` is
that morning. Stored values are metric; units are converted at the edge.
"""
from __future__ import annotations

import datetime

GOAL_MIN = 420
KG_TO_LB = 2.20462
KM_TO_MI = 0.621371
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v else None


def _mean(xs):
    return round(sum(xs) / len(xs), 1) if xs else None


def work_days_from(value) -> set[str]:
    """`fitness_work_days`: "mon,tue" or a list. Names of the mornings you get
    up for work. Empty means the split is off."""
    if isinstance(value, str):
        value = value.split(",")
    names = {str(v).strip().lower()[:3] for v in (value or ())}
    return {n for n in names if n in WEEKDAYS}


def filled(daily: list[dict]) -> list[dict]:
    """The rows on consecutive dates, oldest first; a date nobody reported is a row of None."""
    by = {}
    for r in daily or []:
        try:
            by[datetime.date.fromisoformat(str(r.get("date"))[:10])] = r
        except ValueError:
            continue
    if not by:
        return []
    d, last = min(by), max(by)
    out = []
    while d <= last:
        r = by.get(d, {})
        out.append({"date": d.isoformat(), **{k: _num(r.get(k)) for k in
                    ("sleep_min", "resting_hr", "steps", "distance_km", "weight_kg")}})
        d += datetime.timedelta(days=1)
    return out


def nights(daily: list[dict], work_days: set[str] | None = None) -> list[dict]:
    """One row per morning, oldest first: {date, recorded, asleep, work}."""
    rows, before = [], None
    for r in filled(daily):
        a = r["sleep_min"]
        recorded = bool(a) and a != before
        before = a
        work = None
        if work_days:
            wd = WEEKDAYS[datetime.date.fromisoformat(r["date"]).weekday()]
            work = wd in work_days
        rows.append({"date": r["date"], "recorded": recorded, "asleep": a if recorded else None, "work": work})
    return rows


def _asleep(rows) -> list[float]:
    return [r["asleep"] for r in rows if r["recorded"]]


def sleep_view(rows: list[dict], goal: int = GOAL_MIN, chart_days: int = 90) -> dict:
    rec = [r for r in rows if r["recorded"]]
    last = dict(rows[-1]) if rows else None
    if last is not None:
        last["short"] = last["asleep"] - goal if last["recorded"] else None
    window = rows[-chart_days:]
    out = {"last": last,
           "last_recorded": dict(rec[-1], short=rec[-1]["asleep"] - goal) if rec else None,
           "goal_min": goal, "avg": {}, "debt": {}, "on_target": {}, "chart": [],
           "recorded": {"nights": len(_asleep(window)), "of": len(window)}}
    for n in (7, 30, 90):
        cur = _asleep(rows[-n:])
        prev = _asleep(rows[-2 * n:-n] if len(rows) > n else [])
        out["avg"][str(n)] = {"min": _mean(cur), "prev": _mean(prev), "nights": len(cur), "of": min(n, len(rows))}
    for n in (7, 30):
        cur = _asleep(rows[-n:])
        out["debt"][str(n)] = {"min": round(sum(a - goal for a in cur)) if cur else None, "nights": len(cur)}
        out["on_target"][str(n)] = {"hit": sum(1 for a in cur if a >= goal), "nights": len(cur)}
    base = len(rows) - len(window)
    for j, r in enumerate(window, start=base):
        out["chart"].append({"date": r["date"], "asleep": r["asleep"],
                             "avg7": _mean(_asleep(rows[max(0, j - 6):j + 1])), "work": r["work"]})
    return out


def work_split(rows: list[dict]) -> dict | None:
    """Time asleep on work mornings against the rest. None when no work days are set."""
    if not any(r["work"] is not None for r in rows):
        return None
    work = [r["asleep"] for r in rows if r["recorded"] and r["work"] is True]
    off = [r["asleep"] for r in rows if r["recorded"] and r["work"] is False]
    return {"work": {"min": _mean(work), "nights": len(work)}, "off": {"min": _mean(off), "nights": len(off)}}


def _series(days, key, conv=lambda v: v, nd=1):
    return [{"date": d["date"], "v": None if d[key] is None else round(conv(d[key]), nd)} for d in days]


def view(daily: list[dict], units: str = "metric", goal: int = GOAL_MIN,
         work_days: set[str] | None = None, chart_days: int = 90) -> dict:
    imperial = str(units).lower() == "imperial"
    days = filled(daily)
    rows = nights(daily, work_days)
    kg = (lambda v: v * KG_TO_LB) if imperial else (lambda v: v)
    km = (lambda v: v * KM_TO_MI) if imperial else (lambda v: v)
    out = sleep_view(rows, goal, chart_days)
    out["work_split"] = work_split(rows[-chart_days:])
    out["units"] = {"distance": "mi" if imperial else "km", "weight": "lb" if imperial else "kg"}

    hr = [(d["date"], d["resting_hr"]) for d in days if d["resting_hr"] is not None]
    out["heart"] = {"latest": hr[-1][1] if hr else None, "date": hr[-1][0] if hr else None,
                    "avg30": _mean([d["resting_hr"] for d in days[-30:] if d["resting_hr"] is not None]),
                    "chart": _series(days[-chart_days:], "resting_hr", nd=0)}

    wk = days[-7:]
    st = [d["steps"] for d in wk if d["steps"] is not None]
    ds = [d["distance_km"] for d in wk if d["distance_km"] is not None]
    today = days[-1] if days else {"steps": None, "distance_km": None}
    out["steps"] = {"today": None if today["steps"] is None else int(today["steps"]),
                    "week": int(sum(st)) if st else None,
                    "km_today": None if today["distance_km"] is None else round(km(today["distance_km"]), 2),
                    "km_week": round(km(sum(ds)), 1) if ds else None,
                    "chart": [{"date": d["date"], "steps": None if d["steps"] is None else int(d["steps"]),
                               "km": None if d["distance_km"] is None else round(km(d["distance_km"]), 2)} for d in wk]}

    pts = [(d["date"], d["weight_kg"]) for d in days[-chart_days:] if d["weight_kg"] is not None]
    weighed = None
    for (_, a), (d, b) in zip(pts, pts[1:]):
        if abs(b - a) >= 0.05:
            weighed = d
    latest = pts[-1][1] if pts else None
    # 30 days ago: the last weight at or before then; with less history, the earliest one in the window.
    old = [d["weight_kg"] for d in days[:-30] if d["weight_kg"] is not None]
    base = old[-1] if old else (pts[0][1] if len(pts) > 1 else None)
    out["weight"] = {"latest": None if latest is None else round(kg(latest), 1),
                     "date": pts[-1][0] if pts else None, "weighed": weighed,
                     "flat": len(pts) >= 2 and weighed is None,
                     "change30": None if latest is None or base is None else round(kg(latest - base), 1),
                     "window": chart_days, "chart": _series(days[-chart_days:], "weight_kg", kg)}
    return out
