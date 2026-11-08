"""Scheduler: decide which jobs are due and run them in order.

The workflow starts once a day; this module reads ``config.yaml`` and
``data/state.json`` and runs only the due stages (geo, discovery, events,
build). Phase 0 provides the job names and the schedule parser; the due-check
against ``state.json`` follows in Phase 6.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from letsgau.config import WEEKDAYS

JOBS = ("auto", "geo", "discovery", "events", "build", "all")
"""Values accepted by ``letsgau run --job`` and the workflow_dispatch input."""

STAGES = ("geo", "discovery", "events", "build")
"""Stages in execution order. ``events`` includes crawl + merge."""


@dataclass(frozen=True)
class Schedule:
    """Parsed job frequency (``off | daily | weekly:mon,thu | monthly:1``)."""

    kind: str
    weekdays: tuple[int, ...] = ()
    day_of_month: int | None = None

    @classmethod
    def parse(cls, value: str) -> Schedule:
        if value in ("off", "daily"):
            return cls(kind=value)
        kind, _, arg = value.partition(":")
        if kind == "weekly" and arg:
            return cls(kind=kind, weekdays=tuple(WEEKDAYS.index(d) for d in arg.split(",")))
        if kind == "monthly" and arg.isdigit():
            return cls(kind=kind, day_of_month=int(arg))
        raise ValueError(f"invalid schedule: {value!r}")

    def matches(self, day: date) -> bool:
        """True if the job is scheduled on ``day`` (ignores previous runs)."""
        if self.kind == "daily":
            return True
        if self.kind == "weekly":
            return day.weekday() in self.weekdays
        if self.kind == "monthly":
            return day.day == self.day_of_month
        return False
