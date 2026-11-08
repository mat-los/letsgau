"""Tests for the job schedule parser."""

from __future__ import annotations

from datetime import date

import pytest

from letsgau.scheduler import Schedule

MONDAY = date(2026, 11, 9)


@pytest.mark.parametrize(
    ("value", "day", "expected"),
    [
        ("daily", MONDAY, True),
        ("off", MONDAY, False),
        ("weekly:mon,thu", MONDAY, True),
        ("weekly:mon,thu", date(2026, 11, 12), True),
        ("weekly:mon,thu", date(2026, 11, 10), False),
        ("monthly:1", date(2026, 12, 1), True),
        ("monthly:1", MONDAY, False),
    ],
)
def test_schedule_matches(value: str, day: date, expected: bool) -> None:
    assert Schedule.parse(value).matches(day) is expected


def test_invalid_schedule() -> None:
    with pytest.raises(ValueError):
        Schedule.parse("hourly")
