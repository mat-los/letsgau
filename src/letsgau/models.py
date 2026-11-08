"""Pydantic models for the data files in ``data/`` (places, sources, events, state).

The field rules are documented in docs/DATA_MODEL.md. Only the shared enums are
defined in Phase 0; the full models follow with the stages that write them
(places: Phase 1, events: Phase 2/3, sources: Phase 7, state: Phase 6).

Phase 1 adds :class:`PlacesFile` (``data/places.json``) and a first, minimal
:class:`State` (``data/state.json``) holding the geo fingerprint.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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


class _DataModel(BaseModel):
    """Base for data files: unknown keys are rejected so schema drift is caught early."""

    model_config = ConfigDict(extra="forbid")


class PlaceOrigin(StrEnum):
    AREA = "area"  # found inside the isochrone
    INCLUDE = "include"  # added via region.include_places


class Place(_DataModel):
    """One place (city, town, village, ...) in the area."""

    name: str = Field(min_length=1)
    osm_id: str = Field(pattern=r"^(node|way|relation)/\d+$")
    place_type: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    drive_time_minutes: float | None = Field(default=None, ge=0)
    """Drive time by car from the center (ORS matrix); ``None`` if not routable."""
    distance_km: float | None = Field(default=None, ge=0)
    """Road distance from the center in km; ``None`` if not routable."""
    origin: PlaceOrigin = PlaceOrigin.AREA


class PlacesFile(_DataModel):
    """``data/places.json``: places in the area plus the inputs they were computed from."""

    schema_version: Literal[1] = 1
    fingerprint: str
    """Hash of ``region.center`` and ``region.drive_time_minutes`` (see ``geo.fingerprint``)."""
    generated_at: datetime
    center: dict[str, float]
    drive_time_minutes: int
    place_types: list[str]
    include_places: list[str] = []
    exclude_places: list[str] = []
    places: list[Place]


class State(_DataModel):
    """``data/state.json``. Phase 1 stores the geo fingerprint; Phase 6 adds run history."""

    schema_version: Literal[1] = 1
    geo_fingerprint: str | None = None
    force_discovery: bool = False
    """Set when the geo fingerprint changes; cleared by the discovery stage (Phase 7)."""
    last_run: dict[str, datetime] = {}
    """Last successful run per job (``geo``, ``discovery``, ``events``, ``build``)."""
