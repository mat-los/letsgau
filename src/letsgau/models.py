"""Pydantic models for the data files in ``data/`` (places, sources, events, state).

The field rules are documented in docs/DATA_MODEL.md. Only the shared enums are
defined in Phase 0; the full models follow with the stages that write them
(places: Phase 1, events: Phase 2/3, sources: Phase 7, state: Phase 6).
"""

from __future__ import annotations

from enum import StrEnum


class EventStatus(StrEnum):
    SCHEDULED = "scheduled"
    POSTPONED = "postponed"
    CANCELLED = "cancelled"
    UNCONFIRMED = "unconfirmed"


class SourceKind(StrEnum):
    MUNICIPALITY = "municipality"
    CLUB = "club"
    VENUE = "venue"
    CALENDAR = "calendar"
    PRESS = "press"
    OTHER = "other"


class SourceOrigin(StrEnum):
    WHITELIST = "whitelist"
    DISCOVERY = "discovery"
    CRAWL = "crawl"


class SourceStatus(StrEnum):
    ACTIVE = "active"
    BLACKLISTED = "blacklisted"
