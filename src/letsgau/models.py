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
    DORMANT = "dormant"
    """Several sweeps in a row found nothing; read again only after a pause."""


class _DataModel(BaseModel):
    """Base for data files: unknown keys are rejected so schema drift is caught early."""

    model_config = ConfigDict(extra="forbid")


class PlaceOrigin(StrEnum):
    MUNICIPALITY = "municipality"  # a place in one of region.municipalities
    AREA = "area"  # legacy: found inside the drive-time isochrone (files before 2026-11)
    INCLUDE = "include"  # legacy: added via region.include_places


class Place(_DataModel):
    """One place (city, town, village, ...) in the area."""

    name: str = Field(min_length=1)
    osm_id: str = Field(pattern=r"^(node|way|relation)/\d+$")
    place_type: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    drive_time_minutes: float | None = Field(default=None, ge=0)
    """Legacy (drive-time area until 2026-11); ``None`` in new files."""
    distance_km: float | None = Field(default=None, ge=0)
    """Legacy, like ``drive_time_minutes``."""
    origin: PlaceOrigin = PlaceOrigin.MUNICIPALITY
    municipality: str | None = None
    """Municipality (OSM ``admin_level=8``, e.g. Peiting for Birkland); the place's own name if
    it lies in none or is listed in its municipality's ``separate``. ``None`` only in files
    written before Phase 2."""


class PlacesFile(_DataModel):
    """``data/places.json``: places in the area plus the inputs they were computed from."""

    schema_version: Literal[1] = 1
    fingerprint: str
    """Hash of ``region.municipalities`` (see ``geo.fingerprint``)."""
    generated_at: datetime
    municipalities: list[str] = []
    """The configured municipalities as ``name`` / ``postcode`` / ``name postcode``."""
    places: list[Place]
    """The municipalities' places (main place first), in the order of the config."""
    # Legacy inputs of the drive-time area (files before 2026-11); ignored.
    center: dict[str, float] | None = None
    drive_time_minutes: int | None = None
    place_types: list[str] = []
    include_places: list[str] = []
    exclude_places: list[str] = []


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
DATES_SPEC = r"^\d{4}-\d{2}-\d{2}(,\d{4}-\d{2}-\d{2})*$"
DAYS_SPEC = f"{WEEKDAY_SPEC}|{DATES_SPEC}"


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

    days: str = Field(pattern=DAYS_SPEC)
    """Weekdays (``mon-fri``, ``sat,sun``) or single dates (``2026-11-21``,
    ``2026-11-21,2026-11-28``); an entry for a date wins over one for its weekday."""
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


# --- page cache ------------------------------------------------------------------------------


class PageCacheEntry(_DataModel):
    """One source page read as text (:mod:`letsgau.pages`), keyed by its URL."""

    mode: Literal["text", "agent"]
    """``text``: read from its text; ``agent``: not readable as text, read by the agent."""
    reason: str = ""
    """Why the page is read by the agent."""
    sha256: str = ""
    """Fingerprint of the page text (``text`` mode)."""
    version: int
    """Extraction version; entries of an older version are read again."""
    first_seen: date
    last_seen: date
    """Last run that used the page; old entries are dropped."""
    last_changed: date
    """Last run in which the text (or the mode) changed."""
    events: list[dict[str, Any]] = []
    """Raw event candidates as the AI answered them (validated again on every use)."""
    seen: list[dict[str, Any]] = []
    """Known events the AI found again on the page (``known_seen``: id plus changes)."""


class PagesFile(_DataModel):
    """``data/pages.json``: source pages already read, so unchanged pages cost no AI call."""

    schema_version: Literal[1] = 1
    updated_at: datetime
    pages: dict[str, PageCacheEntry] = {}


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


class SeenVenue(_DataModel):
    """A café, bar, club or venue a discovery pass saw; input for the Instagram lookup."""

    places: list[str] = []
    website: str = ""
    first_seen: date
    last_seen: date


class RejectedCandidate(_DataModel):
    """A candidate account name from discovery that did not pass the profile check."""

    checked: date
    reason: str = ""
    name: str = ""
    """The organiser discovery attributed the name to."""


class InstagramFile(_DataModel):
    """``data/instagram.json``: known accounts, posts already read, rejected candidates."""

    schema_version: Literal[1] = 1
    updated_at: datetime
    accounts: dict[str, InstagramAccount] = {}
    posts: dict[str, InstagramPost] = {}
    rejected: dict[str, RejectedCandidate] = {}
    """Candidate names (discovery) that are not the organiser's account; checked again after
    ``discovery.CANDIDATE_RETRY_DAYS``."""
    venues: dict[str, SeenVenue] = {}
    """Venues seen by discovery, by name; the scheduled lookup session searches their
    accounts."""


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
    last_swept: date | None = None
    """Last events run that read this source."""
    last_result: Literal["events", "empty", "unreachable"] | None = None
    """What that run found there."""
    empty_runs: int = Field(default=0, ge=0)
    """Runs in a row without an event from this source (``dormant`` from
    ``discovery.DORMANT_AFTER_RUNS``)."""


class SourcesFile(_DataModel):
    """``data/sources.json``."""

    schema_version: Literal[1] = 1
    updated_at: datetime
    sources: list[Source] = []
