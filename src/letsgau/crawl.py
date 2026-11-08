"""Event crawl stage (default model: Sonnet).

1. Load the places (``data/places.json``), whitelist, blacklist and the known
   events (``data/events.json``).
2. Split the places into batches of ``limits.places_per_request``; each batch
   gets the whitelist sources assigned to its places (entries without
   ``places`` go to the first batch) and its known events in short form.
3. One AI call per batch (``prompts/events.md``, JSON schema from
   :func:`answer_schema`). The blacklist is passed as blocked domains and hard
   filtered afterwards. The search budget ``limits.max_web_searches_per_run``
   is spread over the batches; ``limits.max_runtime_minutes`` stops starting
   new batches.
4. Every candidate is validated (:func:`to_event`): dates in the crawl window,
   place in the area, tags from the list, exactly one primary tag and source,
   no blacklisted source. Invalid entries are dropped and logged.
5. The events are handed to :mod:`letsgau.merge`, which writes
   ``data/events.json``; usage goes to ``data/state.json``.
"""

from __future__ import annotations

import json
import re
import sys
import time
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from letsgau import merge
from letsgau.ai_runner import AiError, AiRequest, Runner, make_runner
from letsgau.config import Config
from letsgau.models import (
    Event,
    EventsFile,
    EventSource,
    EventStatus,
    LocalizedText,
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


class WhitelistEntry(_Strict):
    url: str = Field(pattern=r"^https?://\S+$")
    note: str = ""
    places: list[str] = []
    """Places the source covers; empty = regional, checked in the first batch."""


class BlacklistEntry(_Strict):
    domain: str = Field(pattern=r"^[a-z0-9.-]+\.[a-z]{2,}$")
    reason: str = ""


def _load_list[T: (WhitelistEntry, BlacklistEntry)](path: Path, model: type[T]) -> list[T]:
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


def answer_schema(tag_ids: Sequence[str]) -> dict[str, Any]:
    """JSON schema of one batch answer (strict: every field required, nulls allowed)."""
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
        }
    )
    checked = _obj(
        {
            "url": {"type": "string"},
            "result": {"type": "string", "enum": ["events_found", "no_events", "unreachable"]},
        }
    )
    return _obj(
        {
            "events": {"type": "array", "items": event},
            "checked_sources": {"type": "array", "items": checked},
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
    window_end = today + timedelta(days=config.window.crawl_days)
    schedule = candidate.schedule
    if schedule.last_date < today:
        raise ValueError(f"already over ({schedule.last_date})")
    if schedule.start_date > window_end:
        raise ValueError(f"starts after the crawl window ({schedule.start_date})")

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


# --- batches and prompt ---------------------------------------------------------------


@dataclass
class Batch:
    number: int
    places: list[Place]
    whitelist: list[WhitelistEntry] = field(default_factory=list)
    known: list[Event] = field(default_factory=list)


def make_batches(
    places: Sequence[Place],
    size: int,
    whitelist: Sequence[WhitelistEntry],
    known: Sequence[Event],
) -> list[Batch]:
    batches = [
        Batch(number=i // size + 1, places=list(places[i : i + size]))
        for i in range(0, len(places), size)
    ]
    if not batches:
        return []
    index = {p.name.casefold(): b for b in batches for p in b.places}
    for entry in whitelist:
        target = next(
            (index[n.casefold()] for n in entry.places if n.casefold() in index), batches[0]
        )
        target.whitelist.append(entry)
    for event in known:
        batch = index.get(event.venue.place.casefold())
        if batch is not None:
            batch.known.append(event)
    return batches


def render_prompt(
    config: Config,
    batch: Batch,
    *,
    today: date,
    blocked: Sequence[str],
    max_searches: int,
    prompts_dir: Path = PROMPTS_DIR,
) -> str:
    env = Environment(
        loader=FileSystemLoader(prompts_dir),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )
    template = env.get_template(PROMPT_FILE)
    deep_end = today + timedelta(days=config.window.deep_check_days)
    return template.render(
        region=config.region,
        places=batch.places,
        today=today,
        window_end=today + timedelta(days=config.window.crawl_days),
        deep_check_end=deep_end,
        whitelist=batch.whitelist,
        deep_check_events=[e for e in batch.known if e.schedule.start_date <= deep_end],
        later_events=[e for e in batch.known if e.schedule.start_date > deep_end],
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
    events: int = 0
    rejected: list[Rejected] = field(default_factory=list)
    whitelist_missing: list[str] = field(default_factory=list)
    error: str | None = None
    usage: Usage = field(default_factory=Usage)


@dataclass
class CrawlResult:
    events: list[Event]
    batches: list[BatchReport]
    usage: Usage
    warnings: list[str]
    skipped_places: list[str] = field(default_factory=list)


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
) -> CrawlResult:
    """Run the AI over all batches and return validated events (writes nothing)."""
    today = today or date.today()
    places_file = _load(data_dir / "places.json", PlacesFile)
    if places_file is None or not places_file.places:
        raise CrawlError("data/places.json is missing or empty; run `letsgau run --job geo` first")
    whitelist = _load_list(Path(config.sources.whitelist_file), WhitelistEntry)
    blacklist = _load_list(Path(config.sources.blacklist_file), BlacklistEntry)
    blocked = sorted({e.domain for e in blacklist})
    existing = _load(data_dir / "events.json", EventsFile)
    known = [e for e in (existing.events if existing else []) if e.parent_id is None]

    places = list(places_file.places)
    skipped: list[str] = []
    if limit_places is not None:
        skipped = [p.name for p in places[limit_places:]]
        places = places[:limit_places]
    index = PlaceIndex(places_file.places)
    batches = make_batches(places, config.limits.places_per_request, whitelist, known)

    budget = config.limits.max_web_searches_per_run
    deadline = clock() + config.limits.max_runtime_minutes * 60
    usage = Usage()
    events: dict[str, Event] = {}
    reports: list[BatchReport] = []
    warnings: list[str] = []

    for position, batch in enumerate(batches):
        report = BatchReport(batch.number, [p.name for p in batch.places])
        reports.append(report)
        remaining = budget - usage.web_searches
        if clock() > deadline:
            report.error = "skipped: max_runtime_minutes reached"
            continue
        if remaining < MIN_SEARCHES_PER_BATCH:
            report.error = "skipped: max_web_searches_per_run used up"
            continue
        per_batch = min(
            MAX_SEARCHES_PER_BATCH,
            max(MIN_SEARCHES_PER_BATCH, remaining // (len(batches) - position)),
        )
        prompt = render_prompt(
            config,
            batch,
            today=today,
            blocked=blocked,
            max_searches=per_batch,
            prompts_dir=prompts_dir,
        )
        request = AiRequest(
            step=f"events batch {batch.number}/{len(batches)}",
            model=config.models.events,
            prompt=prompt,
            schema=answer_schema(config.tag_ids),
            blocked_domains=blocked,
            max_searches=per_batch,
        )
        try:
            result = runner.run(request)
        except AiError as exc:
            report.error = str(exc)
            _log(f"batch {batch.number}: {exc}")
            continue
        report.usage = result.usage
        usage = usage.add(result.usage)
        found, rejected = events_from_answer(
            result.data, config=config, places=index, blocked=blocked, today=today
        )
        report.rejected = rejected
        for event in found:
            if event.id in events:
                events[event.id] = merge.combine(events[event.id], event)
            else:
                events[event.id] = event
        report.events = len(found)
        checked = [c.get("url", "") for c in result.data.get("checked_sources") or []]
        checked += result.fetched_urls
        report.whitelist_missing = [
            w.url for w in batch.whitelist if not any(_same_source(w.url, c) for c in checked)
        ]
        for title_reason in rejected:
            _log(f"batch {batch.number}: dropped {title_reason.title!r}: {title_reason.reason}")

    for report in reports:
        for url in report.whitelist_missing:
            warnings.append(f"whitelist source not checked in batch {report.number}: {url}")
        if report.error:
            warnings.append(f"batch {report.number}: {report.error}")
    if batches and all(r.error and not r.error.startswith("skipped") for r in reports):
        raise CrawlError("every batch failed: " + "; ".join(r.error or "" for r in reports))
    return CrawlResult(
        events=sorted(events.values(), key=_sort_key),
        batches=reports,
        usage=usage,
        warnings=warnings,
        skipped_places=skipped,
    )


def _sort_key(event: Event) -> tuple[date, str, str]:
    return (event.schedule.start_date, event.schedule.start_time or "", event.id)


def _load[T: (PlacesFile, EventsFile, State)](path: Path, model: type[T]) -> T | None:
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
) -> CrawlResult:
    """CLI entry point for ``--job events``: crawl, merge, write (unless ``dry_run``)."""
    today = today or date.today()
    result = crawl(
        config,
        runner or make_runner(config),
        data_dir=data_dir,
        limit_places=limit_places,
        today=today,
    )
    for warning in result.warnings:
        print(f"warning: crawl: {warning}", file=sys.stderr)
    u = result.usage
    print(
        f"crawl: {len(result.events)} events from {len(result.batches)} batches "
        f"({sum(len(r.places) for r in result.batches)} places); "
        f"{u.web_searches} searches, {u.input_tokens + u.output_tokens} tokens, "
        f"~${u.cost_usd:.2f}, {u.duration_s:.0f}s"
        + (f"; {len(result.skipped_places)} places skipped" if result.skipped_places else "")
        + ("; dry run, nothing written" if dry_run else "")
    )
    for event in result.events:
        s = event.schedule
        when = s.start_date.isoformat()
        if s.end_date and s.end_date != s.start_date:
            when += f" to {s.end_date}"
        time_ = f" {s.start_time}" if s.start_time else ""
        flag = "" if event.status is EventStatus.SCHEDULED else f" [{event.status}]"
        print(f"  {when}{time_}  {event.title.de} ({event.venue.place}){flag}")

    if not dry_run:
        events_path = data_dir / "events.json"
        existing = _load(events_path, EventsFile)
        merged = merge.apply(existing, result.events, today=today)
        _write_json(events_path, merged.model_dump(mode="json", by_alias=True))
        state_path = data_dir / "state.json"
        state = _load(state_path, State) or State()
        now = datetime.now(UTC).replace(microsecond=0)
        state.last_run["events"] = now
        state.last_usage["events"] = result.usage
        _write_json(state_path, state.model_dump(mode="json"))
        print(f"crawl: wrote {events_path} ({len(merged.events)} events)")
    return result
