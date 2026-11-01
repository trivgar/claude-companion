"""Made-up, plausible days for trying the page. Labelled DEMO everywhere."""
from __future__ import annotations

import datetime
import math
import random


class DemoSource:
    name = "Demo data"
    demo = True

    def __init__(self, today: datetime.date | None = None, seed: int = 7):
        self._today = today
        self._seed = seed

    def daily(self, days: int) -> list[dict]:
        today = self._today or datetime.date.today()
        rng = random.Random(self._seed)
        rows, prev_sleep = [], None
        for i in range(days):
            d = today - datetime.timedelta(days=days - 1 - i)
            sleep = round(min(540, max(300, rng.gauss(405, 45))))
            weekday_dip = 25 if d.weekday() in (0, 1) else 0
            sleep = sleep - weekday_dip
            if i % 11 == 4:
                sleep = None                    # the watch was off
            elif i % 17 == 9 and prev_sleep:
                sleep = prev_sleep              # a stale post: same minutes as the day before
            prev_sleep = sleep if sleep is not None else prev_sleep
            hr = round(54 + 3 * math.sin(i / 9) + rng.uniform(-1, 1)) if i % 13 != 6 else None
            steps = round(max(1500, rng.gauss(7800, 2600))) if i % 19 != 3 else None
            dist = round(steps * 0.00075, 2) if steps else None
            weight = round(78.5 - i * 0.015 + rng.uniform(-0.3, 0.3), 1) if i % 5 == 0 else None
            rows.append({"date": d.isoformat(), "sleep_min": sleep, "resting_hr": hr, "steps": steps,
                         "distance_km": dist, "weight_kg": weight})
        return rows

    def freshness(self) -> dict:
        return {"source": self.name, "as_of": (self._today or datetime.date.today()).isoformat()}
