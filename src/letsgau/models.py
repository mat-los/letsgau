"""Pydantic models for the data files in ``data/`` (places, sources, events, state).

The field rules are documented in docs/DATA_MODEL.md. Only the shared enums are
defined in Phase 0; the full models follow with the stages that write them
(places: Phase 1, events: Phase 2/3, sources: Phase 7, state: Phase 6).

Phase 1 adds :class:`PlacesFile` (``data/places.json``) and a first, minimal
:class:`State` (``data/state.json``) holding the geo fingerprint. Phase 2 adds
:class:`Event` and :class:`EventsFile` (``data/events.json``) and the usage of
the last run in ``State``.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    municipality: str | None = None
    """Municipality (OSM ``admin_level=8``, e.g. Peiting for Herzogsägmühle); the place's own
    name if it lies in none. ``None`` only in files written before Phase 2."""


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
    last_usage: dict[str, Usage] = {}
    """AI usage of the last run per job (searches, tokens, cost, duration)."""


class Usage(_DataModel):
    """AI usage of one run or one request. Cost is an estimate (0 in subscription mode)."""

    requests: int = 0
    web_searches: int = 0
    web_fetches: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    duration_s: float = 0.0

    def add(self, other: Usage) -> Usage:
        return Usage(
            **{name: getattr(self, name) + getattr(other, name) for name in Usage.model_fields}
        )


# --- events ---------------------------------------------------------------------

Time = Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]
"""Local time ``HH:MM``."""
WEEKDAY_SPEC = r"^(mon|tue|wed|thu|fri|sat|sun)([-,](mon|tue|wed|thu|fri|sat|sun))*$"


class LocalizedText(_DataModel):
    """Text in both site languages; proper names stay unchanged."""

    de: str = Field(min_length=1)
    en: str = Field(min_length=1)


class Venue(_DataModel):
    name: str | None = None
    place: str = Field(min_length=1)
    """Place from ``data/places.json`` (canonical name)."""
    address: str | None = None


class OpeningHours(_DataModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    days: str = Field(pattern=WEEKDAY_SPEC)
    """e.g. ``mon-fri``, ``sat,sun``."""
    from_: Time = Field(alias="from")
    to: Time

    def model_dump(self, **kwargs: object) -> dict[str, object]:  # type: ignore[override]
        kwargs.setdefault("by_alias", True)
        return super().model_dump(**kwargs)  # type: ignore[arg-type]


class Schedule(_DataModel):
    start_date: date
    end_date: date | None = None
    start_time: Time | None = None
    end_time: Time | None = None
    all_day: bool = False
    dates: list[date] | None = None
    """Single days if not continuous, e.g. only the Advent weekends."""
    opening_hours: list[OpeningHours] | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Schedule:
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("end_date is before start_date")
        if self.dates:
            last = self.end_date or self.start_date
            if any(d < self.start_date or d > last for d in self.dates):
                raise ValueError("dates must lie between start_date and end_date")
        return self

    @property
    def last_date(self) -> date:
        return self.end_date or self.start_date


class EventSource(_DataModel):
    url: str = Field(pattern=r"^https?://\S+$")
    primary: bool = False
    first_seen: date
    last_seen: date


class Event(_DataModel):
    """One event in ``data/events.json``; rules in docs/DATA_MODEL.md."""

    id: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}-[a-z0-9]+(-[a-z0-9]+)*$")
    parent_id: str | None = None
    title: LocalizedText
    description: LocalizedText
    venue: Venue
    schedule: Schedule
    primary_tag: str
    tags: list[str] = Field(min_length=1, max_length=3)
    status: EventStatus = EventStatus.SCHEDULED
    postponed_to: str | None = None
    sources: list[EventSource] = Field(min_length=1)
    first_seen: date
    last_verified: date
    misses: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _rules(self) -> Event:
        if len(set(self.tags)) != len(self.tags):
            raise ValueError("tags must be unique")
        if self.primary_tag not in self.tags:
            raise ValueError("primary_tag must be one of tags")
        if sum(s.primary for s in self.sources) != 1:
            raise ValueError("exactly one source must be primary")
        return self


class EventsFile(_DataModel):
    """``data/events.json``: every event exactly once, from yesterday to ``store_days`` ahead."""

    schema_version: Literal[1] = 1
    updated_at: datetime
    events: list[Event] = []


# --- flyer cache -----------------------------------------------------------------------------


class FlyerCacheEntry(_DataModel):
    """What the AI read from one flyer file (image/PDF), keyed by the file's SHA-256."""

    page_url: str
    file_url: str
    kind: Literal["image", "pdf"]
    version: int
    """Extraction version; entries of an older version are read again."""
    first_seen: date
    last_seen: date
    """Last run in which the file was still on its page; old entries are dropped."""
    events: list[dict[str, Any]] = []
    """Raw event candidates as the AI answered them (validated again on every use)."""


class FlyerCacheFile(_DataModel):
    """``data/flyers.json``: flyer files already read, so unchanged files cost no AI call."""

    schema_version: Literal[1] = 1
    updated_at: datetime
    files: dict[str, FlyerCacheEntry] = {}


class InstagramAccount(_DataModel):
    """One Instagram account in ``data/instagram.json``."""

    origin: Literal["whitelist", "mined"]
    places: list[str] = []
    """Places/municipalities the account belongs to (decides the batch)."""
    status: Literal["new", "active", "not_business", "not_found", "error"] = "new"
    first_seen: date
    last_checked: date | None = None
    last_error: str = ""


class InstagramPost(_DataModel):
    """What the AI read from one post, keyed by the post's media ID."""

    username: str
    permalink: str
    posted: date
    version: int
    """Extraction version; posts of an older version are read again."""
    first_seen: date
    last_seen: date
    events: list[dict[str, Any]] = []
    """Raw event candidates as the AI answered them (validated again on every use)."""


class InstagramFile(_DataModel):
    """``data/instagram.json``: known accounts and posts already read."""

    schema_version: Literal[1] = 1
    updated_at: datetime
    accounts: dict[str, InstagramAccount] = {}
    posts: dict[str, InstagramPost] = {}


# --- sources (Phase 7) --------------------------------------------------------------------


class Source(_DataModel):
    """One known event source in ``data/sources.json``; rules in docs/DATA_MODEL.md."""

    url: str = Field(pattern=r"^https?://\S+$")
    domain: str
    name: str = ""
    kind: SourceKind = SourceKind.OTHER
    places: list[str] = []
    """Municipalities the source covers."""
    origin: SourceOrigin
    status: SourceStatus = SourceStatus.ACTIVE
    rating: int | None = Field(default=None, ge=1, le=5)
    """Discovery rating: 5 = current dates as text, updated often; 1 = useless."""
    update_frequency: Literal["weekly", "monthly", "rare", "unknown"] = "unknown"
    note: str = ""
    first_seen: date
    last_checked: date | None = None
    hits: int = Field(default=0, ge=0)
    """Events found on this source in total (counted by the events stage)."""
    last_hit: date | None = None


class SourcesFile(_DataModel):
    """``data/sources.json``."""

    schema_version: Literal[1] = 1
    updated_at: datetime
    sources: list[Source] = []
