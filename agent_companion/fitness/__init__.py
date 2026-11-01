"""Fitness data, with a seam for where it comes from.

A FitnessSource gives one row per day, metric, oldest first. A missing value is
None: NOT RECORDED IS A FACT, NEVER ZERO. The page and the sums (summary.py)
leave such a day out instead of counting it as nothing.

To add a source (Garmin, a Health Connect export, an Apple Health export) write
`fitness/<name>.py` with a class that satisfies FitnessSource, register it
below; docs/fitness-sources.md says how, and which config key picks it.
"""
from __future__ import annotations

from typing import Callable, Protocol

DOCS = "docs/fitness-sources.md"
FIELDS = ("sleep_min", "resting_hr", "steps", "distance_km", "weight_kg")


class FitnessSource(Protocol):
    name: str
    demo: bool

    def daily(self, days: int) -> list[dict]:
        """The last `days` days up to today, oldest first. Each row:
        {date: 'YYYY-MM-DD', sleep_min, resting_hr, steps, distance_km, weight_kg},
        every value a number or None. A night belongs to the MORNING IT ENDS."""

    def freshness(self) -> dict:
        """{source: 'Home Assistant', as_of: ISO time of the latest data, or None}."""


_FACTORIES: dict[str, Callable[[], FitnessSource]] = {}


def register(name: str, factory: Callable[[], FitnessSource]) -> None:
    _FACTORIES[name] = factory


def registered() -> list[str]:
    return sorted(_FACTORIES)


def source(name: str | None = None) -> FitnessSource | None:
    """The source named, or the one in config (`fitness_source`). None when
    nothing is set up. LookupError for a name nobody registered."""
    if name is None:
        from .. import config
        name = str(config.get("fitness_source") or "").strip().lower()
    if not name:
        return None
    if name not in _FACTORIES:
        raise LookupError(f"fitness_source {name!r} is not one of: {', '.join(registered()) or 'none'}")
    return _FACTORIES[name]()


def _home_assistant() -> FitnessSource:
    from .home_assistant import from_config
    return from_config()


def _demo() -> FitnessSource:
    from .demo import DemoSource
    return DemoSource()


register("home_assistant", _home_assistant)
register("demo", _demo)
