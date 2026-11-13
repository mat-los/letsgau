"""Event crawl stage (default model: Sonnet).

1. Load the places (``data/places.json``), whitelist, blacklist and the known
   events (``data/events.json``).
2. Group the places by municipality (``Place.municipality``: Herzogsägmühle and
   Birkland are searched together with Peiting) and split the municipalities
   into batches of ``limits.places_per_request``; each batch gets the
   whitelist sources assigned to its places (entries without ``places`` go to
   the first batch) and its known events in short form.
3. AI calls per batch (``prompts/events.md``, JSON schema from
   :func:`answer_schema`), planned by :func:`plan_calls`: with
   ``depth.crawl_sources_per_request`` > 0, *source sweeps* that each read a few
   known sources (whitelist, then discovered ones) completely, then one *open
   search* with a query checklist (:func:`search_queries`); with 0, one
   combined call. In sweep mode every source is first downloaded as text
   (:mod:`letsgau.pages`): unchanged pages reuse the events read from them
   before, changed pages go to *page* calls without web access, and only pages
   that cannot be read as text are swept by the agent. Every page on the flyer
   list (``sources.flyers_file``) gets its own call: :mod:`letsgau.flyers`
   downloads its posters/PDFs and the model reads them without web access. The
   blacklist is passed as blocked domains and hard filtered afterwards. The
   search budget ``limits.max_web_searches_per_run`` is spread over the calls;
   ``limits.max_runtime_minutes`` stops starting new calls.
4. Every candidate is validated (:func:`to_event`): dates in the crawl window,
   place in the area, tags from the list, exactly one primary tag and source,
   no blacklisted source. Invalid entries are dropped and logged.
5. The events are handed to :mod:`letsgau.merge`, which writes
   ``data/events.json``; usage goes to ``data/state.json``.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from letsgau import flyers, instagram, merge, pages
from letsgau.ai_runner import FILES_DIR, AiError, AiRequest, Runner, make_runner
from letsgau.config import Config
from letsgau.models import (
    Event,
    EventsFile,
    EventSource,
    EventStatus,
    FlyerCacheEntry,
    FlyerCacheFile,
    InstagramAccount,
    InstagramFile,
    InstagramPost,
    LocalizedText,
    PageCacheEntry,
    PagesFile,
    Place,
    PlacesFile,
    Schedule,
    State,
    Usage,
    Venue,
)

DATA_DIR = Path("data")
PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
PROMPT_FILE = "events.md"
MIN_SEARCHES_PER_BATCH = 3
MAX_SEARCHES_PER_BATCH = 40


class CrawlError(Exception):
    """Missing inputs (places, source lists) or no usable result at all."""


# --- source lists -------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


MUNICIPALITY_SLUG = "{municipality_slug}"


class WhitelistEntry(_Strict):
    url: str = Field(pattern=r"^https?://\S+$")
    note: str = ""
    places: list[str] = []
    """Places the source covers; empty = regional, checked in the first batch.

    A URL containing ``{municipality_slug}`` is a template: it is expanded once per
    municipality (restricted to ``places`` if given), e.g. ``.../{municipality_slug}/``
    becomes ``.../hohenpeissenberg/``.
    """

    @property
    def is_template(self) -> bool:
        return MUNICIPALITY_SLUG in self.url

    def expand(self, municipalities: Sequence[str]) -> list[WhitelistEntry]:
        """Concrete entries, one per municipality (only for templates)."""
        if not self.is_template:
            return [self]
        wanted = {p.casefold() for p in self.places}
        return [
            self.model_copy(
                update={
                    "url": self.url.replace(MUNICIPALITY_SLUG, slugify(name, 80)),
                    "places": [name],
                }
            )
            for name in municipalities
            if not wanted or name.casefold() in wanted
        ]


def expand_whitelist(
    whitelist: Sequence[WhitelistEntry], municipalities: Sequence[str]
) -> list[WhitelistEntry]:
    return [e for entry in whitelist for e in entry.expand(municipalities)]


class FlyerEntry(WhitelistEntry):
    """A page whose programme is in images or PDFs (``instance/sources/flyers.yaml``)."""

    max_files: int = Field(default=15, ge=1, le=40)
    """Images/PDFs downloaded from the page per run (in page order)."""


class InstagramEntry(_Strict):
    """An Instagram account read on every run (``instance/sources/instagram.yaml``)."""

    username: str
    places: list[str] = []
    """Places of the account (decides the batch); without, the first batch."""
    note: str = ""

    @field_validator("username")
    @classmethod
    def _handle(cls, value: str) -> str:
        handle = instagram.normalize(value)
        if handle is None:
            raise ValueError(f"not an Instagram account name: {value!r}")
        return handle


class BlacklistEntry(_Strict):
    """``domain``: never used as a source, also blocked for the AI's web access.
    ``url``: this page (and the pages below it) is not read as a known source, for
    example a second front end of an event database that is already read elsewhere."""

    domain: str | None = Field(default=None, pattern=r"^[a-z0-9.-]+\.[a-z]{2,}$")
    url: str | None = Field(default=None, pattern=r"^https?://\S+$")
    reason: str = ""

    @model_validator(mode="after")
    def _one_kind(self) -> BlacklistEntry:
        if (self.domain is None) == (self.url is None):
            raise ValueError("give either domain or url")
        return self


def _load_list[T: (WhitelistEntry, FlyerEntry, BlacklistEntry, InstagramEntry)](
    path: Path, model: type[T]
) -> list[T]:
    if not path.exists():
        raise CrawlError(f"{path} not found")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if not isinstance(data, list):
        raise CrawlError(f"{path}: expected a list")
    try:
        return [model.model_validate(item) for item in data]
    except ValidationError as exc:
        raise CrawlError(f"{path}: {exc}") from exc


def domain_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


def is_blocked(url: str, blocked: Iterable[str]) -> bool:
    host = domain_of(url)
    return any(host == d or host.endswith("." + d) for d in blocked)


# --- ids and names --------------------------------------------------------------------

_UMLAUTS = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "ae", "Ö": "oe", "Ü": "ue"}
)


def slugify(text: str, max_len: int = 50) -> str:
    """ASCII slug: ``Schongauer Weihnachtsmarkt`` -> ``schongauer-weihnachtsmarkt``."""
    text = unicodedata.normalize("NFKD", text.translate(_UMLAUTS))
    text = text.encode("ascii", "ignore").decode().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    if len(slug) > max_len:
        slug = slug[:max_len].rsplit("-", 1)[0]
    return slug or "event"


def event_id(start: date, title: str, place: str) -> str:
    """Stable id: start date + slug of title and place (place only once)."""
    title_slug, place_slug = slugify(title), slugify(place, 30)
    parts = [start.isoformat(), title_slug]
    if place_slug not in title_slug:
        parts.append(place_slug)
    return "-".join(parts)


class PlaceIndex:
    """Maps place names from the AI to canonical names in ``places.json``."""

    def __init__(self, places: Sequence[Place]) -> None:
        self.names = {p.name.casefold(): p.name for p in places}
        # Municipality names are valid places too (e.g. Fuchstal has no place node).
        for place in places:
            if place.municipality:
                self.names.setdefault(place.municipality.casefold(), place.municipality)

    def resolve(self, name: str | None) -> str | None:
        if not name:
            return None
        key = name.strip().casefold()
        if key in self.names:
            return self.names[key]
        # "Landsberg" -> "Landsberg am Lech", "Peiting (Obb.)" -> "Peiting"
        for known, canonical in self.names.items():
            if known.startswith(key + " ") or key.startswith(known + " "):
                return canonical
        return None


# --- AI answer ----------------------------------------------------------------------


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def _obj(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def answer_schema(tag_ids: Sequence[str], files: Sequence[str] = ()) -> dict[str, Any]:
    """JSON schema of one batch answer (strict: every field required, nulls allowed).

    With ``files`` (flyer and page calls) every event also names the file it is printed on.
    ``known_seen`` lists known events found again, by id, with only what changed.
    """
    text = _obj({"de": {"type": "string"}, "en": {"type": "string"}})
    day = {"type": "string", "format": "date"}
    tag = {"type": "string", "enum": list(tag_ids)}
    event = _obj(
        {
            "ref": {"type": "string"},
            "parent_ref": _nullable({"type": "string"}),
            "title": text,
            "description": text,
            "venue": _obj(
                {
                    "name": _nullable({"type": "string"}),
                    "place": {"type": "string"},
                    "address": _nullable({"type": "string"}),
                }
            ),
            "schedule": _obj(
                {
                    "start_date": day,
                    "end_date": _nullable(day),
                    "start_time": _nullable({"type": "string"}),
                    "end_time": _nullable({"type": "string"}),
                    "all_day": {"type": "boolean"},
                    "dates": _nullable({"type": "array", "items": day}),
                    "opening_hours": _nullable(
                        {
                            "type": "array",
                            "items": _obj(
                                {
                                    "days": {"type": "string"},
                                    "from": {"type": "string"},
                                    "to": {"type": "string"},
                                }
                            ),
                        }
                    ),
                }
            ),
            "primary_tag": tag,
            "tags": {"type": "array", "items": tag},
            "status": {"type": "string", "enum": ["scheduled", "cancelled", "postponed"]},
            "postponed_to_date": _nullable(day),
            "sources": {
                "type": "array",
                "items": _obj({"url": {"type": "string"}, "primary": {"type": "boolean"}}),
            },
            **({"file": {"type": "string", "enum": list(files)}} if files else {}),
        }
    )
    checked = _obj(
        {
            "url": {"type": "string"},
            "result": {"type": "string", "enum": ["events_found", "no_events", "unreachable"]},
        }
    )
    seen = _obj(
        {
            "id": {"type": "string"},
            "status": {"type": "string", "enum": ["scheduled", "cancelled"]},
            "start_date": _nullable(day),
            "start_time": _nullable({"type": "string"}),
            "source_url": _nullable({"type": "string"}),
            **({"file": {"type": "string", "enum": list(files)}} if files else {}),
        }
    )
    return _obj(
        {
            "events": {"type": "array", "items": event},
            "known_seen": {"type": "array", "items": seen},
            "checked_sources": {"type": "array", "items": checked},
            "instagram_accounts": {"type": "array", "items": {"type": "string"}},
            "notes": {"type": "string"},
        }
    )


class CandidateSource(BaseModel):
    url: str
    primary: bool = False


class Candidate(BaseModel):
    """One event as returned by the AI, before validation against the config."""

    ref: str
    parent_ref: str | None = None
    title: LocalizedText
    description: LocalizedText
    venue: Venue
    schedule: Schedule
    primary_tag: str
    tags: list[str]
    status: EventStatus = EventStatus.SCHEDULED
    postponed_to_date: date | None = None
    sources: list[CandidateSource] = []


# --- validation ---------------------------------------------------------------------


@dataclass
class Rejected:
    title: str
    reason: str


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(",.;:- ")
    return cut + "…"


def to_event(
    candidate: Candidate,
    *,
    config: Config,
    places: PlaceIndex,
    blocked: Sequence[str],
    today: date,
    parent: Event | None = None,
) -> Event:
    """Validate a candidate against the config and turn it into an :class:`Event`.

    Raises ``ValueError`` with the reason if the candidate must be dropped.
    """
    store_end = today + timedelta(days=config.window.store_days)
    schedule = candidate.schedule
    if schedule.last_date < today:
        raise ValueError(f"already over ({schedule.last_date})")
    if schedule.start_date > store_end:
        raise ValueError(f"starts after store_days ({schedule.start_date})")

    place = places.resolve(candidate.venue.place)
    if place is None and parent is not None:
        place = parent.venue.place
    if place is None:
        raise ValueError(f"place {candidate.venue.place!r} is not in the area")

    allowed = set(config.tag_ids)
    unknown = [t for t in [candidate.primary_tag, *candidate.tags] if t not in allowed]
    if unknown:
        raise ValueError(f"unknown tags {unknown}")
    tags = list(dict.fromkeys([candidate.primary_tag, *candidate.tags]))
    if len(tags) > 3:
        raise ValueError(f"more than 3 tags {tags}")

    urls: list[CandidateSource] = []
    for source in candidate.sources:
        url = source.url.strip()
        if not url.startswith(("http://", "https://")) or is_blocked(url, blocked):
            continue
        if all(u.url != url for u in urls):
            urls.append(CandidateSource(url=url, primary=source.primary))
    if not urls:
        raise ValueError("no valid source (missing or blacklisted)")
    primary_index = next((i for i, u in enumerate(urls) if u.primary), 0)

    limit = config.events.description_max_chars
    description = LocalizedText(
        de=_truncate(candidate.description.de, limit),
        en=_truncate(candidate.description.en, limit),
    )
    venue = candidate.venue.model_copy(update={"place": place})
    return Event(
        id=event_id(schedule.start_date, candidate.title.de, place),
        parent_id=parent.id if parent else None,
        title=candidate.title,
        description=description,
        venue=venue,
        schedule=schedule,
        primary_tag=candidate.primary_tag,
        tags=tags,
        status=candidate.status,
        sources=[
            EventSource(url=u.url, primary=i == primary_index, first_seen=today, last_seen=today)
            for i, u in enumerate(urls)
        ],
        first_seen=today,
        last_verified=today,
    )


def events_from_answer(
    data: dict[str, Any],
    *,
    config: Config,
    places: PlaceIndex,
    blocked: Sequence[str],
    today: date,
) -> tuple[list[Event], list[Rejected]]:
    """Validate all candidates of one answer; parents are resolved before children."""
    raw = data.get("events") or []
    candidates: list[Candidate] = []
    rejected: list[Rejected] = []
    for item in raw:
        try:
            candidates.append(Candidate.model_validate(item))
        except ValidationError as exc:
            title = (item.get("title") or {}).get("de", "?") if isinstance(item, dict) else "?"
            first = exc.errors()[0]
            loc = ".".join(str(p) for p in first["loc"])
            rejected.append(Rejected(str(title), f"schema: {loc}: {first['msg']}"))

    by_ref: dict[str, Event] = {}
    events: list[Event] = []
    postponed: dict[str, str] = {}  # event id -> id of the same event at its new date
    # Parents first, so children can inherit the place and get the parent id.
    ordered = sorted(candidates, key=lambda c: c.parent_ref is not None)
    for cand in ordered:
        parent = by_ref.get(cand.parent_ref) if cand.parent_ref else None
        try:
            event = to_event(
                cand, config=config, places=places, blocked=blocked, today=today, parent=parent
            )
        except (ValueError, ValidationError) as exc:
            rejected.append(Rejected(cand.title.de, str(exc).splitlines()[0]))
            continue
        by_ref[cand.ref] = event
        events.append(event)
        if cand.status is EventStatus.POSTPONED and cand.postponed_to_date:
            new_id = event_id(cand.postponed_to_date, cand.title.de, event.venue.place)
            postponed[event.id] = new_id

    ids = {e.id for e in events}
    events = [
        e.model_copy(update={"postponed_to": postponed[e.id]}) if postponed.get(e.id) in ids else e
        for e in events
    ]
    return events, rejected


def seen_events(
    items: Iterable[Any],
    known: Mapping[str, Event],
    *,
    config: Config,
    blocked: Sequence[str],
    today: date,
) -> tuple[list[Event], list[Rejected]]:
    """Known events the AI found again (``known_seen``: id plus what changed), as events with
    the stored id, texts and tags, so the merge updates them without a full answer."""
    store_end = today + timedelta(days=config.window.store_days)
    found: list[Event] = []
    rejected: list[Rejected] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        event = known.get(str(item.get("id") or ""))
        if event is None:
            rejected.append(Rejected(str(item.get("id")), "known_seen: unknown id"))
            continue
        s = event.schedule
        update: dict[str, Any] = {}
        if not s.dates and s.end_date in (None, s.start_date):  # only single days move
            if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(item.get("start_time") or "")):
                update["start_time"] = item["start_time"]
            try:
                day = date.fromisoformat(str(item.get("start_date") or ""))
            except ValueError:
                day = None
            if day is not None and today <= day <= store_end:
                update |= {"start_date": day, "end_date": None}
        status = event.status
        if item.get("status") == "cancelled":
            status = EventStatus.CANCELLED
        elif status is EventStatus.UNCONFIRMED:
            status = EventStatus.SCHEDULED
        url = str(item.get("source_url") or "").strip()
        if not url.startswith(("http://", "https://")) or is_blocked(url, blocked):
            url = next(src.url for src in event.sources if src.primary)
        found.append(
            event.model_copy(
                update={
                    "schedule": s.model_copy(update=update),
                    "status": status,
                    "sources": [
                        EventSource(url=url, primary=True, first_seen=today, last_seen=today)
                    ],
                    "last_verified": today,
                    "misses": 0,
                }
            )
        )
    return found, rejected


def known_for(call: CrawlCall, batch: Batch) -> list[Event]:
    """Known events of the batch listed on the sources of a ``pages`` or ``sources`` call."""
    if call.mode == "pages":
        urls = [t.url for t in call.pages]
    elif call.mode == "sources":
        urls = [w.url for w in call.whitelist] + [s.url for s in call.sources]
    else:
        return []
    return [e for e in batch.known if any(_same_source(u, s.url) for u in urls for s in e.sources)]


# --- batches and prompt ---------------------------------------------------------------


@dataclass
class PlaceGroup:
    """A municipality with the places (districts) that belong to it."""

    name: str
    places: list[Place]

    @property
    def drive_time_minutes(self) -> float | None:
        times = [p.drive_time_minutes for p in self.places if p.drive_time_minutes is not None]
        return min(times) if times else None

    @property
    def districts(self) -> list[str]:
        """Place names other than the municipality itself."""
        return [p.name for p in self.places if p.name != self.name]


def group_places(places: Sequence[Place]) -> list[PlaceGroup]:
    """Group places by municipality, in the order of the place list (the config order)."""
    groups: dict[str, PlaceGroup] = {}
    for place in places:
        name = place.municipality or place.name
        groups.setdefault(name, PlaceGroup(name=name, places=[])).places.append(place)
    return list(groups.values())


@dataclass
class Batch:
    number: int
    groups: list[PlaceGroup]
    whitelist: list[WhitelistEntry] = field(default_factory=list)
    known: list[Event] = field(default_factory=list)
    sources: list[Any] = field(default_factory=list)
    """Known, well-rated sources of the batch's municipalities (from discovery)."""
    flyers: list[FlyerEntry] = field(default_factory=list)
    """Pages whose images/PDFs are downloaded and read (``flyers.yaml``)."""

    @property
    def places(self) -> list[Place]:
        return [p for g in self.groups for p in g.places]


def make_batches(
    groups: Sequence[PlaceGroup],
    size: int,
    whitelist: Sequence[WhitelistEntry],
    known: Sequence[Event],
    flyers: Sequence[FlyerEntry] = (),
) -> list[Batch]:
    """Batches of ``size`` municipalities with their whitelist/flyer sources and known events."""
    batches = [
        Batch(number=i // size + 1, groups=list(groups[i : i + size]))
        for i in range(0, len(groups), size)
    ]
    if not batches:
        return []
    index = {p.name.casefold(): b for b in batches for p in b.places}
    index.update({g.name.casefold(): b for b in batches for g in b.groups})
    municipalities = [g.name for b in batches for g in b.groups]
    for entry in expand_whitelist(whitelist, municipalities):
        target = next(
            (index[n.casefold()] for n in entry.places if n.casefold() in index), batches[0]
        )
        target.whitelist.append(entry)
    for flyer in (e for entry in flyers for e in entry.expand(municipalities)):
        target = next(
            (index[n.casefold()] for n in flyer.places if n.casefold() in index), batches[0]
        )
        target.flyers.append(flyer)  # type: ignore[arg-type]
    for event in known:
        batch = index.get(event.venue.place.casefold())
        if batch is not None:
            batch.known.append(event)
    return batches


MONTHS_DE = (
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
)  # fmt: skip
SEARCH_TOPICS = (
    "Konzert",
    "Party",
    "Markt",
    "Theater OR Kabarett",
    "Kino",
    "Ausstellung",
    "Vortrag OR Lesung",
    "Führung",
    "Kinder Familie",
    "Heimspiel OR Turnier",
    "Pfarrei Kirche",
    "Verein Fest",
    "Advent OR Weihnachten",
    "Jugendzentrum OR Juze",
    "Livemusik OR Band live",
)
SWEEP_MAX_SEARCHES = 8
"""Searches for a source-sweep or re-check call (only to find moved pages)."""
SEARCHES_PER_SEARCH_CALL = 13
"""Target searches per open-search call: the open-search budget of a batch
(``depth.crawl_searches_per_request``) is split into calls of about this size, each with a
part of the checklist; one long call with the whole checklist stopped after a few searches."""
RECHECK_PER_CALL = 20
"""Known events per re-check call."""
PAGE_CHARS_PER_CALL = 60_000
"""Characters of source text per ``pages`` call (several changed pages share one call)."""


def search_queries(
    groups: Sequence[PlaceGroup], today: date, window_end: date
) -> list[tuple[str, list[str]]]:
    """The fixed query checklist of the open-search call, per municipality."""
    months: list[str] = []
    day = today.replace(day=1)
    while day <= window_end:
        months.append(f"{MONTHS_DE[day.month - 1]} {day.year}")
        day = (day + timedelta(days=32)).replace(day=1)
    plan = []
    for group in groups:
        m = group.name
        queries = [f"{m} Veranstaltungen", f"{m} Veranstaltungskalender"]
        queries += [f"{m} Veranstaltungen {month}" for month in months]
        queries += [f"{m} {topic}" for topic in SEARCH_TOPICS]
        queries += [f"{d} Veranstaltungen" for d in group.districts]
        plan.append((m, queries))
    return plan


@dataclass
class CrawlCall:
    """One AI call of a batch: a combined call, a sweep over known sources, or the open search."""

    mode: str  # "all" | "pages" | "sources" | "flyers" | "instagram" | "recheck" | "search"
    whitelist: list[WhitelistEntry] = field(default_factory=list)
    sources: list[Any] = field(default_factory=list)
    flyer: FlyerEntry | None = None
    """The page of a ``flyers`` call."""
    events: list[Event] = field(default_factory=list)
    """Known events of a ``recheck`` call."""
    queries: list[tuple[str, list[str]]] = field(default_factory=list)
    """Checklist part of a ``search`` call: (municipality, queries)."""
    pages: list[pages.PageText] = field(default_factory=list)
    """Downloaded source texts of a ``pages`` call."""


def rotate_queries(
    queries: Sequence[tuple[str, list[str]]], today: date
) -> list[tuple[str, list[str]]]:
    """The checklist starting at a different query in every run.

    A search call usually runs only part of its checklist, so a fixed order would always
    skip the same queries. Two runs a week (Monday and Thursday) start about half a list
    apart, and over a few runs every query gets its turn. The order depends only on the
    date, so a dry run on the same day plans the same queries.
    """
    flat = [(m, q) for m, items in queries for q in items]
    if not flat:
        return []
    run = today.isocalendar().week * 2 + (1 if today.weekday() >= 3 else 0)
    start = run * ((len(flat) + 1) // 2) % len(flat)
    rotated: list[tuple[str, list[str]]] = []
    for m, q in flat[start:] + flat[:start]:
        if rotated and rotated[-1][0] == m:
            rotated[-1][1].append(q)
        else:
            rotated.append((m, [q]))
    return rotated


def split_queries(
    queries: Sequence[tuple[str, list[str]]], parts: int
) -> list[list[tuple[str, list[str]]]]:
    """Cut the checklist into ``parts`` parts of nearly equal size, keeping the grouping."""
    flat = [(m, q) for m, items in queries for q in items]
    parts = max(1, min(parts, len(flat)))
    result: list[list[tuple[str, list[str]]]] = []
    for i in range(parts):
        part: list[tuple[str, list[str]]] = []
        for m, q in flat[len(flat) * i // parts : len(flat) * (i + 1) // parts]:
            if part and part[-1][0] == m:
                part[-1][1].append(q)
            else:
                part.append((m, [q]))
        result.append(part)
    return result


def plan_calls(
    batch: Batch,
    sources_per_request: int,
    *,
    queries: Sequence[tuple[str, list[str]]] = (),
    recheck: Sequence[Event] = (),
    search_calls: int = 1,
    instagram: bool = False,
    sweep: Sequence[Any] | None = None,
    texts: Sequence[pages.PageText] = (),
) -> list[CrawlCall]:
    """Split a batch into source sweeps (``sources_per_request`` sources each), one call per
    flyer page, re-check calls for the known events of the deep-check window
    (:data:`RECHECK_PER_CALL` each) and open-search calls with a part of the checklist
    (``search_calls`` calls, see :data:`SEARCHES_PER_SEARCH_CALL`).

    With ``sources_per_request == 0`` the batch is one combined call (``mode="all"``) plus
    the flyer calls. The Instagram step (``instagram``) comes last.

    ``texts`` are changed source pages read as text: they come first, in ``pages`` calls of
    up to :data:`PAGE_CHARS_PER_CALL` characters. ``sweep`` are the sources the agent reads
    (default: all of the batch's whitelist and known sources).
    """
    flyer_calls = [CrawlCall("flyers", flyer=f) for f in batch.flyers]
    # last, so that accounts mined by every other call of the batch are read in the same run
    instagram_calls = [CrawlCall("instagram")] if instagram else []
    if sources_per_request <= 0:
        return [
            CrawlCall("all", list(batch.whitelist), list(batch.sources)),
            *flyer_calls,
            *instagram_calls,
        ]
    items: list[Any] = [*batch.whitelist, *batch.sources] if sweep is None else list(sweep)
    calls = []
    for text in texts:
        size = sum(len(t.text) for t in calls[-1].pages) if calls else 0
        if not calls or size + len(text.text) > PAGE_CHARS_PER_CALL:
            calls.append(CrawlCall("pages"))
        calls[-1].pages.append(text)
    for start in range(0, len(items), sources_per_request):
        chunk = items[start : start + sources_per_request]
        calls.append(
            CrawlCall(
                "sources",
                whitelist=[i for i in chunk if isinstance(i, WhitelistEntry)],
                sources=[i for i in chunk if not isinstance(i, WhitelistEntry)],
            )
        )
    calls += flyer_calls
    recheck = list(recheck)
    for start in range(0, len(recheck), RECHECK_PER_CALL):
        calls.append(CrawlCall("recheck", events=recheck[start : start + RECHECK_PER_CALL]))
    calls += [CrawlCall("search", queries=p) for p in split_queries(queries, search_calls)] or [
        CrawlCall("search")
    ]
    return calls + instagram_calls


def confirmed(known: Event, found: Mapping[str, Event], municipality_of: Mapping[str, str]) -> bool:
    """True if an event found in this run is the known event (same id, or the same
    match the merge would make)."""
    if known.id in found:
        return True
    return any(
        merge.candidate_score(event, known, municipality_of) >= merge.CLEAR
        for event in found.values()
    )


def render_prompt(
    config: Config,
    batch: Batch,
    *,
    today: date,
    blocked: Sequence[str],
    max_searches: int,
    prompts_dir: Path = PROMPTS_DIR,
    call: CrawlCall | None = None,
    files: Sequence[Any] = (),
    posts: Sequence[Any] = (),
    mine_instagram: bool = False,
) -> str:
    env = Environment(
        loader=FileSystemLoader(prompts_dir),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )
    template = env.get_template(PROMPT_FILE)
    deep_end = today + timedelta(days=config.window.deep_check_days)
    window_end = today + timedelta(days=config.window.crawl_days)
    call = call or CrawlCall("all", list(batch.whitelist), list(batch.sources))
    return template.render(
        mode=call.mode,
        region=config.region,
        groups=batch.groups,
        today=today,
        window_end=window_end,
        store_end=today + timedelta(days=config.window.store_days),
        deep_check_end=deep_end,
        whitelist=call.whitelist,
        deep_check_events=(
            call.events
            if call.mode == "recheck"
            else [e for e in batch.known if e.schedule.start_date <= deep_end]
            if call.mode == "all"
            else []
        ),
        later_events=[e for e in batch.known if deep_end < e.schedule.start_date <= window_end],
        known_events=known_for(call, batch),
        sources=call.sources,
        covered_sources=[w.url for w in batch.whitelist]
        + [s.url for s in batch.sources]
        + [f.url for f in batch.flyers],
        flyer=call.flyer,
        pages=[(f"page {i}", t.url, t.text) for i, t in enumerate(call.pages, 1)],
        files=[(f"{FILES_DIR}/{f.path.name}", f.url, f.kind) for f in files],
        posts=posts,
        mine_instagram=mine_instagram,
        queries=call.queries or search_queries(batch.groups, today, window_end),
        tags=config.tags,
        excluded_topics=config.excluded_topics,
        blocked_domains=blocked,
        max_searches=max_searches,
        description_max_chars=config.events.description_max_chars,
    )


# --- stage ----------------------------------------------------------------------------


@dataclass
class BatchReport:
    number: int
    places: list[str]
    """Municipalities of the batch."""
    events: int = 0
    rejected: list[Rejected] = field(default_factory=list)
    whitelist_missing: list[str] = field(default_factory=list)
    notes: str = ""
    """Free-text remarks of the AI (moved sources, unreachable pages, ...)."""
    error: str | None = None
    usage: Usage = field(default_factory=Usage)
    calls: int = 0
    """AI calls of the batch that returned an answer."""
    last_usage: Usage = field(default_factory=Usage)
    """Usage of the most recent flyer call (added to the run total by the caller)."""


@dataclass
class CrawlResult:
    events: list[Event]
    batches: list[BatchReport]
    usage: Usage
    warnings: list[str]
    skipped_places: list[str] = field(default_factory=list)
    """Municipalities left out by ``--limit-places``."""
    covered_places: set[str] = field(default_factory=set)
    """Places and municipalities of the batches that ran without error."""
    municipality_of: dict[str, str] = field(default_factory=dict)
    """Place name -> municipality, for matching in the merge."""
    merge: merge.MergeReport | None = None
    """What the merge did (set by :func:`run`)."""
    flyer_cache: FlyerCacheFile | None = None
    """Updated flyer cache (written by :func:`run` unless it is a dry run)."""
    instagram_cache: InstagramFile | None = None
    """Updated Instagram accounts and posts (written by :func:`run` unless a dry run)."""
    page_cache: PagesFile | None = None
    """Updated page cache (written by :func:`run` unless it is a dry run)."""
    swept: dict[str, str] = field(default_factory=dict)
    """Source URL -> ``events`` | ``empty`` | ``unreachable`` for every whitelist and known
    source a successful call of this run read (see :func:`discovery.record_sweeps`)."""


def _same_source(url: str, checked: str) -> bool:
    a, b = urlsplit(url), urlsplit(checked)
    path_a, path_b = a.path.rstrip("/"), b.path.rstrip("/")
    return domain_of(url) == domain_of(checked) and (
        path_b.startswith(path_a) or path_a.startswith(path_b)
    )


def crawl(
    config: Config,
    runner: Runner,
    *,
    data_dir: Path = DATA_DIR,
    limit_places: int | None = None,
    today: date | None = None,
    prompts_dir: Path = PROMPTS_DIR,
    clock: Any = time.monotonic,
    fetch: flyers.Fetch | None = None,
    graph_get: instagram.Get | None = None,
    env: Mapping[str, str] | None = None,
) -> CrawlResult:
    """Run the AI over all batches and return validated events (writes nothing)."""
    today = today or date.today()
    places_file = _load(data_dir / "places.json", PlacesFile)
    if places_file is None or not places_file.places:
        raise CrawlError("data/places.json is missing or empty; run `letsgau run --job geo` first")
    whitelist = _load_list(Path(config.sources.whitelist_file), WhitelistEntry)
    blacklist = _load_list(Path(config.sources.blacklist_file), BlacklistEntry)
    blocked = sorted({e.domain for e in blacklist if e.domain})
    existing = _load(data_dir / "events.json", EventsFile)
    known = [e for e in (existing.events if existing else []) if e.parent_id is None]

    groups = group_places(places_file.places)
    skipped: list[str] = []
    if limit_places is not None:
        skipped = [g.name for g in groups[limit_places:]]
        groups = groups[:limit_places]
    index = PlaceIndex(places_file.places)
    flyer_list = load_flyers(config)
    batches = make_batches(groups, config.limits.places_per_request, whitelist, known, flyer_list)
    from letsgau import discovery  # late import: discovery imports this module

    sources_file = discovery.load_sources(data_dir)
    sweeps = config.depth.crawl_sources_per_request
    for batch in batches:
        batch.sources = discovery.known_sources_for(
            sources_file,
            [g.name for g in batch.groups],
            [w.url for w in batch.whitelist],
            limit=None if sweeps else discovery.MAX_KNOWN_PER_BATCH,
            skip_prefixes=[e.url for e in blacklist if e.url],
            today=today,
        )

    budget = config.limits.max_web_searches_per_run
    deadline = clock() + config.limits.max_runtime_minutes * 60
    usage = Usage()
    events: dict[str, Event] = {}
    reports: list[BatchReport] = []
    warnings: list[str] = []
    if config.sources.flyers_file and not Path(config.sources.flyers_file).exists():
        warnings.append(f"{config.sources.flyers_file} not found; no flyer pages are read")
    fetch = fetch or flyers.http_fetch
    robots = flyers.Robots(fetch)
    flyer_cache = _load(data_dir / "flyers.json", FlyerCacheFile) or FlyerCacheFile(
        updated_at=datetime.now(UTC).replace(microsecond=0)
    )
    insta_on = bool(config.sources.instagram_file)
    insta_entries = load_instagram(config)
    if insta_on and not Path(config.sources.instagram_file).exists():
        warnings.append(f"{config.sources.instagram_file} not found; only mined accounts are read")
    insta_cache = _load(data_dir / "instagram.json", InstagramFile) or InstagramFile(
        updated_at=datetime.now(UTC).replace(microsecond=0)
    )
    insta_state: dict[str, Any] = {"read": 0, "stop": False, "warned": False}

    deep_end = today + timedelta(days=config.window.deep_check_days)
    window_end = today + timedelta(days=config.window.crawl_days)
    municipality_of = {p.name: p.municipality or p.name for p in places_file.places}
    if insta_on and config.sources.instagram_candidates_file:
        _check_listed_candidates(
            config,
            insta_cache,
            index=index,
            municipality_of=municipality_of,
            today=today,
            env=os.environ if env is None else env,
            get=graph_get or instagram.http_get,
            warnings=warnings,
        )
    by_batch: dict[int, BatchReport] = {}

    def report_of(batch: Batch) -> BatchReport:
        if batch.number not in by_batch:
            by_batch[batch.number] = BatchReport(batch.number, [g.name for g in batch.groups])
            reports.append(by_batch[batch.number])
        return by_batch[batch.number]

    page_cache = _load(data_dir / "pages.json", PagesFile) or PagesFile(
        updated_at=datetime.now(UTC).replace(microsecond=0)
    )
    checked_by_batch: dict[int, list[str]] = {}
    read_sources: set[str] = set()  # source URLs read by a call that answered, or from text
    text_read: set[str] = set()  # source pages read as text in this run
    to_sweep: dict[int, list[Any]] = {}
    changed: dict[int, list[pages.PageText]] = {}
    for batch in batches:
        if not sweeps:
            continue
        to_sweep[batch.number], changed[batch.number] = [], []
        for item in [*batch.whitelist, *batch.sources]:
            entry = page_cache.pages.get(item.url)
            if pages.needs_agent(entry, today):
                page_cache.pages[item.url] = entry.model_copy(update={"last_seen": today})
                to_sweep[batch.number].append(item)
                continue
            try:
                text = pages.read(item.url, fetch, robots)
            except pages.PageError as exc:
                page_cache.pages[item.url] = PageCacheEntry(
                    mode="agent",
                    reason=str(exc),
                    version=pages.PAGE_CACHE_VERSION,
                    first_seen=entry.first_seen if entry else today,
                    last_seen=today,
                    last_changed=today,
                )
                to_sweep[batch.number].append(item)
                continue
            if entry is not None and pages.unchanged(entry, text):
                page_cache.pages[item.url] = entry.model_copy(update={"last_seen": today})
                _add_items(
                    entry.events,
                    report_of(batch),
                    events,
                    config=config,
                    index=index,
                    blocked=blocked,
                    today=today,
                    seen=entry.seen,
                    known={e.id: e for e in batch.known},
                )
                text_read.add(item.url)
                read_sources.add(item.url)
                checked_by_batch.setdefault(batch.number, []).append(item.url)
            else:
                changed[batch.number].append(text)
        reused = len(batch.whitelist) + len(batch.sources) - len(to_sweep[batch.number])
        reused -= len(changed[batch.number])
        _log(
            f"batch {batch.number}: pages: {reused} unchanged (reused), "
            f"{len(changed[batch.number])} changed (read as text), "
            f"{len(to_sweep[batch.number])} for the agent"
        )
    plan = [
        (batch, call)
        for batch in batches
        for call in plan_calls(
            batch,
            sweeps,
            queries=rotate_queries(search_queries(batch.groups, today, window_end), today),
            recheck=[e for e in batch.known if e.schedule.start_date <= deep_end],
            search_calls=max(
                1, round(config.depth.crawl_searches_per_request / SEARCHES_PER_SEARCH_CALL)
            ),
            instagram=insta_on,
            sweep=to_sweep.get(batch.number),
            texts=changed.get(batch.number, ()),
        )
    ]
    unreachable: set[str] = set()  # URLs a call reported as unreachable
    for position, (batch, call) in enumerate(plan):
        report = report_of(batch)
        several = sweeps > 0
        same_mode = [c for b, c in plan if b is batch and c.mode == call.mode]
        number = sum(1 for b, c in plan[: position + 1] if b is batch and c.mode == call.mode)
        tag = call.mode
        if call.mode in ("sources", "pages") or (len(same_mode) > 1 and call.mode != "flyers"):
            tag = f"{call.mode} {number}"
        prefix = f"[{tag}] " if several else ""
        remaining = budget - usage.web_searches
        if clock() > deadline:
            _skip(report, prefix + "skipped: max_runtime_minutes reached")
            continue
        if call.mode == "recheck":
            # the source calls of this run read the same pages: an event they reported
            # again is confirmed, only the others need a look at their own source
            # an event whose source page was read as text in this run is either among the
            # events found there or no longer on it: no AI call needed either way
            open_events = [
                e
                for e in call.events
                if not confirmed(e, events, municipality_of)
                and not any(_same_source(p, s.url) for p in text_read for s in e.sources)
            ]
            if not open_events:
                _log(
                    f"batch {batch.number}: {prefix}skipped, all {len(call.events)} events "
                    "confirmed by the source reading"
                )
                continue
            if len(open_events) < len(call.events):
                _log(
                    f"batch {batch.number}: {prefix}{len(call.events) - len(open_events)} of "
                    f"{len(call.events)} events confirmed by the source reading"
                )
            call = replace(call, events=open_events)
        if call.mode == "pages":
            done = _run_pages_call(
                config,
                runner,
                batch,
                call,
                report,
                events,
                today=today,
                blocked=blocked,
                prompts_dir=prompts_dir,
                index=index,
                step=f"events batch {batch.number}/{len(batches)} ({tag})",
                prefix=prefix,
                cache=page_cache,
            )
            usage = usage.add(report.last_usage)
            text_read.update(done)
            read_sources.update(done)
            checked_by_batch.setdefault(batch.number, []).extend(done)
            continue
        if call.mode == "flyers":
            _run_flyer_call(
                config,
                runner,
                batch,
                call,
                report,
                events,
                today=today,
                blocked=blocked,
                prompts_dir=prompts_dir,
                index=index,
                fetch=fetch,
                robots=robots,
                warnings=warnings,
                step=f"events batch {batch.number}/{len(batches)} (flyers {call.flyer.url})",
                cache=flyer_cache,
            )
            usage = usage.add(report.last_usage)
            continue
        if call.mode == "instagram":
            _run_instagram_call(
                config,
                runner,
                batch,
                report,
                events,
                today=today,
                blocked=blocked,
                prompts_dir=prompts_dir,
                index=index,
                fetch=fetch,
                warnings=warnings,
                step=f"events batch {batch.number}/{len(batches)} (instagram)",
                cache=insta_cache,
                entries=insta_entries,
                state=insta_state,
                env=os.environ if env is None else env,
                get=graph_get or instagram.http_get,
            )
            usage = usage.add(report.last_usage)
            continue
        if remaining < MIN_SEARCHES_PER_BATCH:
            _skip(report, prefix + "skipped: max_web_searches_per_run used up")
            continue
        if call.mode in ("sources", "recheck"):
            cap = SWEEP_MAX_SEARCHES
        elif call.mode == "search":
            # the open-search budget of a batch is spread over its search calls
            cap = max(
                MIN_SEARCHES_PER_BATCH,
                -(-config.depth.crawl_searches_per_request // len(same_mode)),
            )
        else:
            cap = config.depth.crawl_searches_per_request
        cap = min(cap, MAX_SEARCHES_PER_BATCH) if not several else cap
        per_call = min(cap, max(MIN_SEARCHES_PER_BATCH, remaining // (len(plan) - position)))
        prompt = render_prompt(
            config,
            batch,
            today=today,
            blocked=blocked,
            max_searches=per_call,
            prompts_dir=prompts_dir,
            call=call,
            mine_instagram=insta_on,
        )
        step = f"events batch {batch.number}/{len(batches)}"
        request = AiRequest(
            step=step + (f" ({tag})" if several else ""),
            model=config.models.events,
            prompt=prompt,
            schema=answer_schema(config.tag_ids),
            blocked_domains=blocked,
            max_searches=per_call,
        )
        try:
            result = runner.run(request)
        except AiError as exc:
            _skip(report, prefix + str(exc))
            _log(f"batch {batch.number}: {prefix}{exc}")
            continue
        report.calls += 1
        report.usage = report.usage.add(result.usage)
        note = str(result.data.get("notes") or "").strip()
        if note:
            report.notes = "\n".join(filter(None, [report.notes, prefix + note]))
        usage = usage.add(result.usage)
        found, rejected = events_from_answer(
            result.data, config=config, places=index, blocked=blocked, today=today
        )
        again, unknown = seen_events(
            result.data.get("known_seen") or [],
            {e.id: e for e in batch.known},
            config=config,
            blocked=blocked,
            today=today,
        )
        found += again
        rejected += unknown
        report.rejected += rejected
        for event in found:
            if event.id in events:
                events[event.id] = merge.combine(events[event.id], event)
            else:
                events[event.id] = event
        report.events += len(found)
        if insta_on:
            mined = _mine_instagram(result.data, batch, insta_cache, today)
            if mined:
                _log(f"batch {batch.number}: {prefix}Instagram accounts found: {', '.join(mined)}")
        checked = checked_by_batch.setdefault(batch.number, [])
        checked += [c.get("url", "") for c in result.data.get("checked_sources") or []]
        checked += result.fetched_urls
        if call.mode in ("sources", "all"):
            read_sources.update(w.url for w in call.whitelist)
            read_sources.update(s.url for s in call.sources)
        unreachable.update(
            str(c.get("url", ""))
            for c in result.data.get("checked_sources") or []
            if c.get("result") == "unreachable"
        )
        for title_reason in rejected:
            _log(f"batch {batch.number}: dropped {title_reason.title!r}: {title_reason.reason}")

    for batch, report in zip(batches, reports, strict=True):
        if report.calls:
            checked = checked_by_batch.get(batch.number, [])
            report.whitelist_missing = [
                w.url for w in batch.whitelist if not any(_same_source(w.url, c) for c in checked)
            ]

    for report in reports:
        for url in report.whitelist_missing:
            warnings.append(f"whitelist source not checked in batch {report.number}: {url}")
        if report.error:
            warnings.append(f"batch {report.number}: {report.error}")
    if batches and all(r.error and not r.calls and "skipped:" not in r.error for r in reports):
        raise CrawlError("every batch failed: " + "; ".join(r.error or "" for r in reports))
    covered: set[str] = set()
    for batch, report in zip(batches, reports, strict=True):
        if report.error is None:
            covered.update(p.name for p in batch.places)
            covered.update(g.name for g in batch.groups)
    event_urls = [src.url for e in events.values() for src in e.sources]
    swept = {
        url: "events"
        if any(_same_source(url, u) for u in event_urls)
        else "unreachable"
        if any(_same_source(url, u) for u in unreachable)
        else "empty"
        for url in read_sources
    }
    keep_after = today - timedelta(days=FLYER_CACHE_KEEP_DAYS)
    flyer_cache = flyer_cache.model_copy(
        update={
            "updated_at": datetime.now(UTC).replace(microsecond=0),
            "files": {k: v for k, v in flyer_cache.files.items() if v.last_seen >= keep_after},
        }
    )
    return CrawlResult(
        events=sorted(events.values(), key=_sort_key),
        batches=reports,
        usage=usage,
        warnings=warnings,
        skipped_places=skipped,
        covered_places=covered,
        municipality_of=municipality_of,
        flyer_cache=flyer_cache,
        instagram_cache=_prune_instagram(insta_cache, today, config) if insta_on else None,
        page_cache=pages.prune(page_cache, today).model_copy(
            update={"updated_at": datetime.now(UTC).replace(microsecond=0)}
        ),
        swept=swept,
    )


def _add_items(
    items: Sequence[dict[str, Any]],
    report: BatchReport,
    events: dict[str, Event],
    *,
    config: Config,
    index: PlaceIndex,
    blocked: Sequence[str],
    today: date,
    seen: Sequence[dict[str, Any]] = (),
    known: Mapping[str, Event] | None = None,
) -> None:
    """Validate raw event candidates and known events found again (fresh or cached) and add
    them to ``events``."""
    found, rejected = events_from_answer(
        {"events": list(items)}, config=config, places=index, blocked=blocked, today=today
    )
    again, unknown = seen_events(seen, known or {}, config=config, blocked=blocked, today=today)
    found += again
    report.rejected += rejected + unknown
    for event in found:
        events[event.id] = merge.combine(events[event.id], event) if event.id in events else event
    report.events += len(found)


def _run_pages_call(
    config: Config,
    runner: Runner,
    batch: Batch,
    call: CrawlCall,
    report: BatchReport,
    events: dict[str, Event],
    *,
    today: date,
    blocked: Sequence[str],
    prompts_dir: Path,
    index: PlaceIndex,
    step: str,
    prefix: str,
    cache: PagesFile,
) -> list[str]:
    """Let the AI read changed source pages from their text (no web access); cache what it
    found per page. Returns the URLs of the pages read (none if the call failed)."""
    report.last_usage = Usage()
    refs = [f"page {i}" for i in range(1, len(call.pages) + 1)]
    request = AiRequest(
        step=step,
        model=config.models.events,
        prompt=render_prompt(
            config,
            batch,
            today=today,
            blocked=blocked,
            max_searches=0,
            prompts_dir=prompts_dir,
            call=call,
        ),
        schema=answer_schema(config.tag_ids, files=refs),
        blocked_domains=blocked,
        max_searches=0,
        web=False,
    )
    try:
        result = runner.run(request)
    except AiError as exc:
        _skip(report, prefix + str(exc))
        _log(f"batch {batch.number}: {prefix}{exc}")
        return []
    report.calls += 1
    report.last_usage = result.usage
    report.usage = report.usage.add(result.usage)
    note = str(result.data.get("notes") or "").strip()
    if note:
        report.notes = "\n".join(filter(None, [report.notes, prefix + note]))
    by_page: dict[str, list[dict[str, Any]]] = {t.url: [] for t in call.pages}
    for item in result.data.get("events") or []:
        if not isinstance(item, dict):
            continue
        urls = [str(s.get("url", "")) for s in item.get("sources") or []]
        owner = next(
            (t for ref, t in zip(refs, call.pages, strict=True) if ref == item.get("file")),
            next(
                (t for t in call.pages if any(_same_source(t.url, u) for u in urls)),
                call.pages[0],
            ),
        )
        by_page[owner.url].append({k: v for k, v in item.items() if k != "file"})
    seen_by_page: dict[str, list[dict[str, Any]]] = {t.url: [] for t in call.pages}
    for item in result.data.get("known_seen") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("source_url") or "")
        owner = next(
            (t for ref, t in zip(refs, call.pages, strict=True) if ref == item.get("file")),
            next((t for t in call.pages if _same_source(t.url, url)), call.pages[0]),
        )
        seen_by_page[owner.url].append({k: v for k, v in item.items() if k != "file"})
    for text in call.pages:
        old = cache.pages.get(text.url)
        cache.pages[text.url] = PageCacheEntry(
            mode="text",
            sha256=text.sha256,
            version=pages.PAGE_CACHE_VERSION,
            first_seen=old.first_seen if old else today,
            last_seen=today,
            last_changed=today,
            events=by_page[text.url],
            seen=seen_by_page[text.url],
        )
    _add_items(
        [item for items in by_page.values() for item in items],
        report,
        events,
        config=config,
        index=index,
        blocked=blocked,
        today=today,
        seen=[item for items in seen_by_page.values() for item in items],
        known={e.id: e for e in batch.known},
    )
    return [t.url for t in call.pages]


def load_flyers(config: Config) -> list[FlyerEntry]:
    """The flyer list; empty if ``sources.flyers_file`` is unset or the file is missing."""
    path = Path(config.sources.flyers_file) if config.sources.flyers_file else None
    if path is None or not path.exists():
        return []
    return _load_list(path, FlyerEntry)


FLYER_CACHE_VERSION = 1
"""Bump when the flyer extraction changes, so cached files are read again."""
FLYER_CACHE_KEEP_DAYS = 30
"""Cache entries of files not seen on their page for this long are dropped."""


def _rekey(item: dict[str, Any], sha: str) -> dict[str, Any]:
    """Make ``ref``/``parent_ref`` unique across files that are combined into one answer."""
    item = {k: v for k, v in item.items() if k != "file"}
    item["ref"] = f"{sha[:10]}-{item.get('ref', '')}"
    if item.get("parent_ref"):
        item["parent_ref"] = f"{sha[:10]}-{item['parent_ref']}"
    return item


def _run_flyer_call(
    config: Config,
    runner: Runner,
    batch: Batch,
    call: CrawlCall,
    report: BatchReport,
    events: dict[str, Event],
    *,
    today: date,
    blocked: Sequence[str],
    prompts_dir: Path,
    index: PlaceIndex,
    fetch: flyers.Fetch,
    robots: flyers.Robots,
    warnings: list[str],
    step: str,
    cache: FlyerCacheFile,
) -> None:
    """Download the flyers of one page; the AI reads only files not in ``cache``."""
    flyer = call.flyer
    assert flyer is not None
    report.last_usage = Usage()
    items: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="letsgau-flyers-") as tmp:
        files, problems = flyers.collect(
            flyer.url, Path(tmp), max_files=flyer.max_files, fetch=fetch, robots=robots
        )
        warnings.extend(f"flyers: {p}" for p in problems)
        if not files:
            return
        fresh = []
        for f in files:
            entry = cache.files.get(f.sha256)
            if entry is not None and entry.version == FLYER_CACHE_VERSION:
                cache.files[f.sha256] = entry.model_copy(update={"last_seen": today})
                items += [_rekey(item, f.sha256) for item in entry.events]
            else:
                fresh.append(f)
        _log(
            f"batch {batch.number}: {len(files)} flyer file(s) from {flyer.url}, "
            f"{len(files) - len(fresh)} already read, {len(fresh)} new"
        )
        if fresh:
            names = [f"{FILES_DIR}/{f.path.name}" for f in fresh]
            request = AiRequest(
                step=step,
                model=config.models.events,
                prompt=render_prompt(
                    config,
                    batch,
                    today=today,
                    blocked=blocked,
                    max_searches=0,
                    prompts_dir=prompts_dir,
                    call=call,
                    files=fresh,
                ),
                schema=answer_schema(config.tag_ids, files=names),
                blocked_domains=blocked,
                max_searches=0,
                web=False,
                files=[f.path for f in fresh],
            )
            try:
                result = runner.run(request)
            except AiError as exc:
                _skip(report, f"[flyers {flyer.url}] {exc}")
                _log(f"batch {batch.number}: [flyers] {exc}")
                result = None
            if result is not None:
                report.calls += 1
                report.last_usage = result.usage
                report.usage = report.usage.add(result.usage)
                note = str(result.data.get("notes") or "").strip()
                if note:
                    report.notes = "\n".join(filter(None, [report.notes, f"[flyers] {note}"]))
                by_file: dict[str, list[dict[str, Any]]] = {f.sha256: [] for f in fresh}
                for item in result.data.get("events") or []:
                    if not isinstance(item, dict):
                        continue
                    urls = {str(s.get("url", "")) for s in item.get("sources") or []}
                    owner = next(
                        (f for f in fresh if f"{FILES_DIR}/{f.path.name}" == item.get("file")),
                        next((f for f in fresh if f.url in urls), fresh[0]),
                    )
                    by_file[owner.sha256].append({k: v for k, v in item.items() if k != "file"})
                for f in fresh:
                    cache.files[f.sha256] = FlyerCacheEntry(
                        page_url=flyer.url,
                        file_url=f.url,
                        kind="pdf" if f.kind == "pdf" else "image",
                        version=FLYER_CACHE_VERSION,
                        first_seen=today,
                        last_seen=today,
                        events=by_file[f.sha256],
                    )
                    items += [_rekey(item, f.sha256) for item in by_file[f.sha256]]
    found, rejected = events_from_answer(
        {"events": items}, config=config, places=index, blocked=blocked, today=today
    )
    report.rejected += rejected
    for event in found:
        events[event.id] = merge.combine(events[event.id], event) if event.id in events else event
    report.events += len(found)


def load_instagram(config: Config) -> list[InstagramEntry]:
    """The Instagram list; empty if ``sources.instagram_file`` is unset or missing."""
    path = Path(config.sources.instagram_file) if config.sources.instagram_file else None
    if path is None or not path.exists():
        return []
    return _load_list(path, InstagramEntry)


INSTAGRAM_VERSION = 1
"""Bump when the Instagram extraction changes, so cached posts are read again."""
INSTAGRAM_POSTS_PER_CALL = 6
"""New posts read in one AI call (caption plus up to 4 images each)."""
INSTAGRAM_RETRY_DAYS = 30
"""Accounts that were not readable (personal account, not found) are tried again after this."""


def _check_listed_candidates(
    config: Config,
    cache: InstagramFile,
    *,
    index: PlaceIndex,
    municipality_of: Mapping[str, str],
    today: date,
    env: Mapping[str, str],
    get: instagram.Get,
    warnings: list[str],
) -> None:
    """Check the entries of ``sources.instagram_candidates_file`` against their profile, so
    that a merged lookup pull request takes effect in the next events run."""
    from letsgau import discovery  # late import: discovery imports this module

    names_in: dict[str, list[str]] = {}
    for place, municipality in municipality_of.items():
        names_in.setdefault(municipality, []).append(place)

    def resolve(name: str) -> str | None:
        place = index.resolve(name)
        return municipality_of.get(place, place) if place else None

    found = discovery.listed_candidates(config, resolve=resolve, default_places=sorted(names_in))
    if not found:
        return
    accepted, rejected, _ = discovery.verify_candidates(
        cache,
        found,
        names_of=lambda m: names_in.get(m, [m]),
        today=today,
        env=env,
        get=get,
        api_version=config.instagram.api_version,
        warnings=warnings,
    )
    if accepted or rejected:
        _log(
            f"instagram candidates: confirmed {', '.join(accepted) or 'none'}; "
            f"rejected {', '.join(rejected) or 'none'}"
        )


def _mine_instagram(
    data: dict[str, Any], batch: Batch, cache: InstagramFile, today: date
) -> list[str]:
    """Add the account names an AI call found on the pages it read; returns the new ones."""
    added = []
    for raw in data.get("instagram_accounts") or []:
        handle = instagram.normalize(str(raw))
        if handle and handle not in cache.accounts:
            cache.accounts[handle] = InstagramAccount(
                origin="mined", places=[g.name for g in batch.groups], first_seen=today
            )
            added.append(handle)
    return added


def _prune_instagram(cache: InstagramFile, today: date, config: Config) -> InstagramFile:
    keep_after = today - timedelta(days=config.instagram.max_post_age_days + 30)
    return cache.model_copy(
        update={
            "updated_at": datetime.now(UTC).replace(microsecond=0),
            "posts": {k: v for k, v in cache.posts.items() if v.posted >= keep_after},
        }
    )


SCREEN_MIN_CAPTION = 40
"""Posts with a shorter caption skip the pre-screen: the picture probably carries the text."""


def screen_schema() -> dict[str, Any]:
    item = {
        "type": "object",
        "properties": {"post": {"type": "integer"}, "may_be_event": {"type": "boolean"}},
        "required": ["post", "may_be_event"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"posts": {"type": "array", "items": item}},
        "required": ["posts"],
        "additionalProperties": False,
    }


def _prescreen_posts(
    config: Config,
    runner: Runner,
    posts: list[instagram.Post],
    cache: InstagramFile,
    *,
    today: date,
    report: BatchReport,
) -> list[instagram.Post]:
    """Drop posts whose caption is clearly not about an upcoming event, before their images
    are read (``instagram.prescreen``; one text-only call to the merge model).

    Dropped posts are cached without events, so they are not read again. Posts with a short
    caption, and all posts if the call fails, go on to the image reading.
    """
    candidates = [p for p in posts if len(p.caption.strip()) >= SCREEN_MIN_CAPTION]
    if not candidates:
        return posts
    env = Environment(
        loader=FileSystemLoader(PROMPTS_DIR),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )
    prompt = env.get_template("instagram_screen.md").render(
        today=today,
        posts=[
            {"n": n, "username": p.username, "posted": p.posted.date(), "caption": p.caption[:1500]}
            for n, p in enumerate(candidates, 1)
        ],
    )
    request = AiRequest(
        step="instagram pre-screen",
        model=config.models.merge,
        prompt=prompt,
        schema=screen_schema(),
        web=False,
        max_searches=0,
    )
    try:
        result = runner.run(request)
    except AiError as exc:
        _log(f"instagram pre-screen failed, all posts are read: {exc}")
        return posts
    report.usage = report.usage.add(result.usage)
    report.last_usage = report.last_usage.add(result.usage)
    no_event = {
        int(x["post"])
        for x in result.data.get("posts") or []
        if isinstance(x, dict)
        and x.get("may_be_event") is False
        and str(x.get("post", "")).isdigit()
    }
    dropped = {candidates[n - 1].id for n in no_event if 1 <= n <= len(candidates)}
    for post in posts:
        if post.id in dropped:
            cache.posts[post.id] = InstagramPost(
                username=post.username,
                permalink=post.permalink,
                posted=post.posted.date(),
                version=INSTAGRAM_VERSION,
                first_seen=today,
                last_seen=today,
                events=[],
            )
    if dropped:
        _log(f"instagram pre-screen: {len(dropped)} of {len(posts)} post(s) are no event")
    return [p for p in posts if p.id not in dropped]


def _run_instagram_call(
    config: Config,
    runner: Runner,
    batch: Batch,
    report: BatchReport,
    events: dict[str, Event],
    *,
    today: date,
    blocked: Sequence[str],
    prompts_dir: Path,
    index: PlaceIndex,
    fetch: flyers.Fetch,
    warnings: list[str],
    step: str,
    cache: InstagramFile,
    entries: Sequence[InstagramEntry],
    state: dict[str, Any],
    env: Mapping[str, str],
    get: instagram.Get,
) -> None:
    """Read the recent posts of the batch's Instagram accounts; only new posts go to the AI."""
    report.last_usage = Usage()
    settings = config.instagram
    names = {p.name.casefold() for p in batch.places} | {g.name.casefold() for g in batch.groups}

    def here(places: Sequence[str]) -> bool:
        return bool({p.casefold() for p in places} & names) or (not places and batch.number == 1)

    listed = [e for e in entries if here(e.places)]
    for entry in listed:
        old = cache.accounts.get(entry.username)
        cache.accounts[entry.username] = (
            old.model_copy(update={"origin": "whitelist", "places": entry.places})
            if old
            else InstagramAccount(origin="whitelist", places=entry.places, first_seen=today)
        )
    accounts = [e.username for e in listed]
    if settings.read_mined:
        accounts += sorted(
            (
                u
                for u, a in cache.accounts.items()
                if a.origin == "mined" and a.places and here(a.places)
            ),
            # least recently read first, so that all are read when there are more than the limit
            key=lambda u: (cache.accounts[u].last_checked or date.min, u),
        )
    if not accounts:
        return
    token, user_id = env.get(instagram.TOKEN_ENV, ""), env.get(instagram.USER_ENV, "")
    if not token or not user_id:
        if not state["warned"]:
            warnings.append(
                f"instagram: {instagram.TOKEN_ENV} or {instagram.USER_ENV} not set; "
                "Instagram accounts are not read"
            )
            state["warned"] = True
        return

    cutoff = today - timedelta(days=settings.max_post_age_days)
    items: list[dict[str, Any]] = []
    fresh: list[instagram.Post] = []
    read = 0
    for username in accounts:
        if state["stop"]:
            break
        if state["read"] >= settings.max_accounts_per_run:
            _log(f"batch {batch.number}: instagram: max_accounts_per_run reached")
            break
        account = cache.accounts[username]
        if (
            account.status in ("not_business", "not_found")
            and account.last_checked is not None
            and (today - account.last_checked).days < INSTAGRAM_RETRY_DAYS
        ):
            continue
        state["read"] += 1
        read += 1
        try:
            posts = instagram.fetch_posts(
                username,
                user_id=user_id,
                token=token,
                limit=settings.posts_per_account,
                api_version=settings.api_version,
                get=get,
            )
        except instagram.AccountError as exc:
            status = exc.kind if exc.kind in ("not_business", "not_found") else "error"
            cache.accounts[username] = account.model_copy(
                update={"status": status, "last_checked": today, "last_error": str(exc)}
            )
            _log(f"batch {batch.number}: instagram @{username}: {exc.kind}: {exc}")
            if exc.kind in ("auth", "permission", "rate_limit", "network"):
                state["stop"] = True
                warnings.append(f"instagram: {exc}; no more accounts are read in this run")
            elif account.origin == "whitelist":
                warnings.append(f"instagram: @{username} cannot be read ({exc.kind}): {exc}")
            continue
        cache.accounts[username] = account.model_copy(
            update={"status": "active", "last_checked": today, "last_error": ""}
        )
        for post in posts:
            if post.posted.date() < cutoff:
                continue
            cached = cache.posts.get(post.id)
            if cached is not None and cached.version == INSTAGRAM_VERSION:
                cache.posts[post.id] = cached.model_copy(update={"last_seen": today})
                items += [_rekey(item, f"ig{post.id}") for item in cached.events]
            else:
                fresh.append(post)
    _log(f"batch {batch.number}: instagram: {read} account(s) read, {len(fresh)} new post(s)")
    if fresh and settings.prescreen:
        fresh = _prescreen_posts(config, runner, fresh, cache, today=today, report=report)

    for start in range(0, len(fresh), INSTAGRAM_POSTS_PER_CALL):
        chunk = fresh[start : start + INSTAGRAM_POSTS_PER_CALL]
        number = start // INSTAGRAM_POSTS_PER_CALL + 1
        with tempfile.TemporaryDirectory(prefix="letsgau-instagram-") as tmp:
            infos: list[dict[str, Any]] = []
            paths: list[Path] = []
            for n, post in enumerate(chunk, 1):
                files = []
                for k, url in enumerate(post.image_urls, 1):
                    try:
                        body, content_type = fetch(url, flyers.MAX_IMAGE_BYTES)
                    except OSError:
                        continue
                    if flyers._kind(body, content_type, url) != "image":
                        continue
                    path = Path(tmp) / f"post{n}-{k}{flyers._ext(body, 'image')}"
                    path.write_bytes(body)
                    paths.append(path)
                    files.append(f"{FILES_DIR}/{path.name}")
                infos.append(
                    {
                        "ref": f"post {n}",
                        "username": post.username,
                        "posted": post.posted.date().isoformat(),
                        "permalink": post.permalink,
                        "caption": post.caption.strip()[:2200],
                        "files": files,
                    }
                )
            result = None
            if any(i["caption"] or i["files"] for i in infos):
                request = AiRequest(
                    step=step + (f" {number}" if len(fresh) > INSTAGRAM_POSTS_PER_CALL else ""),
                    model=config.models.events,
                    prompt=render_prompt(
                        config,
                        batch,
                        today=today,
                        blocked=blocked,
                        max_searches=0,
                        prompts_dir=prompts_dir,
                        call=CrawlCall("instagram"),
                        posts=infos,
                    ),
                    schema=answer_schema(config.tag_ids, files=[i["ref"] for i in infos]),
                    blocked_domains=blocked,
                    max_searches=0,
                    web=False,
                    files=paths,
                )
                try:
                    result = runner.run(request)
                except AiError as exc:
                    _skip(report, f"[instagram] {exc}")
                    _log(f"batch {batch.number}: [instagram] {exc}")
                    continue  # not cached: read again next run
            by_post: dict[str, list[dict[str, Any]]] = {p.id: [] for p in chunk}
            if result is not None:
                report.calls += 1
                report.last_usage = report.last_usage.add(result.usage)
                report.usage = report.usage.add(result.usage)
                note = str(result.data.get("notes") or "").strip()
                if note:
                    report.notes = "\n".join(filter(None, [report.notes, f"[instagram] {note}"]))
                for item in result.data.get("events") or []:
                    if not isinstance(item, dict):
                        continue
                    urls = {str(s.get("url", "")) for s in item.get("sources") or []}
                    owner = next(
                        (
                            p
                            for p, i in zip(chunk, infos, strict=True)
                            if i["ref"] == item.get("file")
                        ),
                        next((p for p in chunk if p.permalink in urls), chunk[0]),
                    )
                    by_post[owner.id].append({k: v for k, v in item.items() if k != "file"})
            for post in chunk:
                cache.posts[post.id] = InstagramPost(
                    username=post.username,
                    permalink=post.permalink,
                    posted=post.posted.date(),
                    version=INSTAGRAM_VERSION,
                    first_seen=today,
                    last_seen=today,
                    events=by_post[post.id],
                )
                items += [_rekey(item, f"ig{post.id}") for item in by_post[post.id]]
    found, rejected = events_from_answer(
        {"events": items}, config=config, places=index, blocked=blocked, today=today
    )
    report.rejected += rejected
    for event in found:
        events[event.id] = merge.combine(events[event.id], event) if event.id in events else event
    report.events += len(found)


def _skip(report: BatchReport, reason: str) -> None:
    report.error = "; ".join(filter(None, [report.error, reason]))


def _sort_key(event: Event) -> tuple[date, str, str]:
    return (event.schedule.start_date, event.schedule.start_time or "", event.id)


def _load[T: (PlacesFile, EventsFile, State, FlyerCacheFile)](
    path: Path, model: type[T]
) -> T | None:
    if not path.exists():
        return None
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        raise CrawlError(f"{path} is invalid: {exc}") from exc


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _log(message: str) -> None:
    print(f"crawl: {message}", file=sys.stderr, flush=True)


def run(
    config: Config,
    *,
    dry_run: bool = False,
    limit_places: int | None = None,
    runner: Runner | None = None,
    data_dir: Path = DATA_DIR,
    today: date | None = None,
    fetch: flyers.Fetch | None = None,
) -> CrawlResult:
    """CLI entry point for ``--job events``: crawl, merge, write (unless ``dry_run``)."""
    today = today or date.today()
    runner = runner or make_runner(config)
    result = crawl(
        config,
        runner,
        data_dir=data_dir,
        limit_places=limit_places,
        today=today,
        fetch=fetch,
    )
    for warning in result.warnings:
        print(f"warning: crawl: {warning}", file=sys.stderr)
    u = result.usage
    print(
        f"crawl: {len(result.events)} events from {len(result.batches)} batches "
        f"({sum(len(r.places) for r in result.batches)} municipalities); "
        f"{u.web_searches} searches, {u.input_tokens + u.output_tokens} tokens, "
        f"~${u.cost_usd:.2f} at API prices, {u.duration_s:.0f}s"
        + (
            f"; {len(result.skipped_places)} municipalities skipped"
            if result.skipped_places
            else ""
        )
        + ("; dry run, nothing written" if dry_run else "")
    )
    for report in result.batches:
        if report.notes:
            print(f"crawl: note from batch {report.number}: {report.notes}")
    for event in result.events:
        s = event.schedule
        when = s.start_date.isoformat()
        if s.end_date and s.end_date != s.start_date:
            when += f" to {s.end_date}"
        time_ = f" {s.start_time}" if s.start_time else ""
        flag = "" if event.status is EventStatus.SCHEDULED else f" [{event.status}]"
        print(f"  {when}{time_}  {event.title.de} ({event.venue.place}){flag}")

    events_path = data_dir / "events.json"
    existing = _load(events_path, EventsFile)
    report = merge.MergeReport()
    merged = merge.apply(
        existing,
        result.events,
        today=today,
        covered_places=result.covered_places,
        municipality_of=result.municipality_of,
        deep_check_days=config.window.deep_check_days,
        unconfirmed_after=config.events.unconfirmed_after_misses,
        decide=merge.ai_decider(runner, config.models.merge, config.excluded_topics),
        report=report,
    )
    result.merge = report
    print(
        f"merge: {len(report.added)} new, {len(report.updated)} updated "
        f"({len(report.merged_fuzzy)} by similarity), {len(report.cancelled)} cancelled, "
        f"{len(report.postponed)} postponed, {len(report.unconfirmed)} now unconfirmed, "
        f"{len(report.excluded)} excluded by topic, {len(report.deleted)} past deleted"
    )
    if not dry_run:
        _write_json(events_path, merged.model_dump(mode="json", by_alias=True))
        if result.flyer_cache is not None and result.flyer_cache.files:
            _write_json(data_dir / "flyers.json", result.flyer_cache.model_dump(mode="json"))
        if result.page_cache is not None and result.page_cache.pages:
            _write_json(data_dir / "pages.json", result.page_cache.model_dump(mode="json"))
        if result.instagram_cache is not None and (
            result.instagram_cache.accounts or result.instagram_cache.posts
        ):
            _write_json(data_dir / "instagram.json", result.instagram_cache.model_dump(mode="json"))
        state_path = data_dir / "state.json"
        state = _load(state_path, State) or State()
        now = datetime.now(UTC).replace(microsecond=0)
        state.last_run["events"] = now
        state.last_usage["events"] = result.usage
        _write_json(state_path, state.model_dump(mode="json"))
        from letsgau import discovery

        blacklist = _load_list(Path(config.sources.blacklist_file), BlacklistEntry)
        sources = discovery.record_hits(
            discovery.load_sources(data_dir),
            result.events,
            result.municipality_of,
            sorted({e.domain for e in blacklist if e.domain}),
            today,
        )
        swept, dormant = discovery.record_sweeps(sources, result.swept, today)
        discovery.save_sources(swept or sources, data_dir)
        with_events = sum(r == "events" for r in result.swept.values())
        print(
            f"crawl: sources: {len(result.swept)} read, {with_events} with events"
            + (f"; now dormant: {', '.join(dormant)}" if dormant else "")
        )
        print(f"crawl: wrote {events_path} ({len(merged.events)} events)")
    return result


# --- source check (letsgau sources) -----------------------------------------------------


def list_sources(
    config: Config, places_path: Path = DATA_DIR / "places.json"
) -> list[WhitelistEntry]:
    """Whitelist with templates expanded for the municipalities in ``places_path``."""
    whitelist = _load_list(Path(config.sources.whitelist_file), WhitelistEntry)
    places_file = _load(places_path, PlacesFile)
    municipalities = [g.name for g in group_places(places_file.places)] if places_file else []
    return expand_whitelist(whitelist, municipalities)


def check_url(url: str, timeout: float = 20.0) -> str:
    """HTTP status of ``url`` as text (``200``, ``404``, ``error: ...``)."""
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": "letsgau source check"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return str(response.status)
    except urllib.error.HTTPError as exc:
        return str(exc.code)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return f"error: {exc}"
