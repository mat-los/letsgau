"""Merge stage: combine crawl results with ``data/events.json``.

Steps (docs/CONCEPT.md, "Pipeline", step 5):

1. **Deterministic matching.** A crawled event matches a stored one if the ids are
   equal, or if the dates overlap, the place is in the same municipality, the
   start times (when both are known) are at most two hours apart and the
   normalized titles are similar (:func:`title_similarity`). Clear matches are
   merged: the stored id stays, texts/schedule/status come from the crawl,
   sources are appended.
2. **AI tie-break.** Unclear pairs (similarity between :data:`UNCLEAR` and
   :data:`CLEAR`) are decided by the merge model (default Haiku) with yes/no. The
   same call checks every new event against ``excluded_topics``; matches are
   dropped and logged.
3. **Status.** Cancellations and postponements come from the crawl. Events in
   the deep-check window that were not found again although their municipality
   was crawled get ``misses + 1``; at ``events.unconfirmed_after_misses`` they
   become ``unconfirmed``. Found again, they are ``scheduled`` with ``misses = 0``.
4. **Cleanup.** Events that ended before yesterday are deleted.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from letsgau.models import Event, EventsFile, EventSource, EventStatus

CLEAR = 0.85
"""Title similarity from which a pair is merged without asking the AI."""
UNCLEAR = 0.5
"""Title similarity from which a pair is sent to the AI tie-break."""
MAX_TIME_GAP_MINUTES = 120
PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

_STOPWORDS = frozenset(
    {
        *("der", "die", "das", "den", "dem", "des", "und", "in", "im", "am", "an", "auf"),
        *("mit", "zum", "zur", "von", "vom", "fuer", "bei", "ein", "eine", "einer"),
        *("the", "a", "of", "and", "at", "on", "for", "with", "to", "by", "vs", "gegen"),
    }
)
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


# --- similarity ---------------------------------------------------------------------


def title_tokens(title: str, places: Iterable[str] = ()) -> list[str]:
    """Normalized title words without stop words, years and place names."""
    text = unicodedata.normalize("NFKD", title.lower().translate(_UMLAUTS))
    text = text.encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", text)
    place_words = {
        w for p in places for w in re.findall(r"[a-z0-9]+", p.lower().translate(_UMLAUTS))
    }
    return [
        w
        for w in words
        if w not in _STOPWORDS
        and not re.fullmatch(r"(19|20)\d\d", w)
        and not any(w.startswith(pw) for pw in place_words if len(pw) > 3)
    ]


def title_similarity(a: str, b: str, places: Iterable[str] = ()) -> float:
    """0..1; sequence ratio or word overlap, whichever is higher."""
    places = list(places)
    ta, tb = title_tokens(a, places), title_tokens(b, places)
    if not ta or not tb:
        ta, tb = title_tokens(a), title_tokens(b)
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0
    ratio = SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio()
    sa, sb = set(ta), set(tb)
    jaccard = len(sa & sb) / len(sa | sb)
    score = max(ratio, jaccard)
    small, large = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    if small <= large:  # one title contains the other, e.g. "Weihnachtszauber"
        distinctive = len(small) >= 2 or len(next(iter(small))) >= 8
        score = max(score, 0.9 if distinctive else 0.7)
    return round(score, 3)


def _minutes(value: str | None) -> int | None:
    if not value:
        return None
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def candidate_score(
    new: Event, old: Event, municipality_of: Mapping[str, str] | None = None
) -> float:
    """Similarity of two events, 0 if date, place, time or role rule them out.

    A program item (``parent_id`` set) never matches a main event and vice versa.
    """
    if (new.parent_id is None) != (old.parent_id is None):
        return 0.0
    m = municipality_of or {}
    place_a = m.get(new.venue.place, new.venue.place).casefold()
    place_b = m.get(old.venue.place, old.venue.place).casefold()
    if place_a != place_b:
        return 0.0
    a, b = new.schedule, old.schedule
    if a.start_date > b.last_date or b.start_date > a.last_date:
        return 0.0
    ta, tb = _minutes(a.start_time), _minutes(b.start_time)
    if ta is not None and tb is not None and abs(ta - tb) > MAX_TIME_GAP_MINUTES:
        return 0.0
    places = {new.venue.place, old.venue.place, place_a, place_b}
    return title_similarity(new.title.de, old.title.de, places)


# --- combining ----------------------------------------------------------------------


def merge_sources(old: list[EventSource], new: list[EventSource]) -> list[EventSource]:
    """Union by URL; the stored primary source stays primary."""
    by_url = {s.url: s for s in old}
    for source in new:
        if source.url in by_url:
            kept = by_url[source.url]
            by_url[source.url] = kept.model_copy(
                update={"last_seen": max(kept.last_seen, source.last_seen)}
            )
        else:
            by_url[source.url] = source.model_copy(update={"primary": False})
    merged = list(by_url.values())
    if sum(s.primary for s in merged) != 1:
        merged = [s.model_copy(update={"primary": i == 0}) for i, s in enumerate(merged)]
    return merged


def combine(old: Event, new: Event) -> Event:
    """Update a stored event with a fresh crawl result; the stored id stays."""
    status = new.status
    if status is EventStatus.UNCONFIRMED:  # never set by the crawl, keep it consistent
        status = EventStatus.SCHEDULED
    schedule = new.schedule
    a, b = old.schedule, new.schedule
    if (
        a.last_date > a.start_date
        and a.start_date <= b.start_date
        and b.last_date <= a.last_date
        and b.last_date - b.start_date < a.last_date - a.start_date
    ):
        schedule = a  # one day of a known multi-day event: keep the full range
    return new.model_copy(
        update={
            "id": old.id,
            "status": status,
            "schedule": schedule,
            "sources": merge_sources(old.sources, new.sources),
            "first_seen": min(old.first_seen, new.first_seen),
            "last_verified": max(old.last_verified, new.last_verified),
            "misses": 0,
        }
    )


# --- AI tie-break and topic check -----------------------------------------------------


@dataclass
class Pair:
    number: int
    new: Event
    old: Event
    score: float


@dataclass
class Decisions:
    same: set[int] = field(default_factory=set)
    """Numbers of the pairs that are the same event."""
    excluded: dict[str, str] = field(default_factory=dict)
    """Event id -> matching excluded topic."""


Decide = Callable[[Sequence[Pair], Sequence[Event]], Decisions]
"""AI step: (unclear pairs, new events) -> decisions."""


def decision_schema() -> dict[str, Any]:
    pair = {
        "type": "object",
        "properties": {"pair": {"type": "integer"}, "same_event": {"type": "boolean"}},
        "required": ["pair", "same_event"],
        "additionalProperties": False,
    }
    excluded = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "topic": {"type": "string"}},
        "required": ["id", "topic"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "pairs": {"type": "array", "items": pair},
            "excluded": {"type": "array", "items": excluded},
        },
        "required": ["pairs", "excluded"],
        "additionalProperties": False,
    }


def render_prompt(pairs: Sequence[Pair], new_events: Sequence[Event], topics: Sequence[str]) -> str:
    env = Environment(
        loader=FileSystemLoader(PROMPTS_DIR),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )
    return env.get_template("merge.md").render(pairs=pairs, new_events=new_events, topics=topics)


def ai_decider(runner: Any, model: str, topics: Sequence[str]) -> Decide:
    """A :data:`Decide` that asks the merge model in one call per run."""
    from letsgau.ai_runner import AiError, AiRequest

    def decide(pairs: Sequence[Pair], new_events: Sequence[Event]) -> Decisions:
        check = list(new_events) if topics else []
        if not pairs and not check:
            return Decisions()
        request = AiRequest(
            step="merge",
            model=model,
            prompt=render_prompt(pairs, check, topics),
            schema=decision_schema(),
            web=False,
        )
        try:
            data = runner.run(request).data
        except AiError as exc:  # keep the run going: unclear pairs stay separate
            _log(f"AI tie-break failed, unclear pairs are kept separate: {exc}")
            return Decisions()
        known_ids = {e.id for e in check}
        return Decisions(
            same={int(p["pair"]) for p in data.get("pairs", []) if p.get("same_event")},
            excluded={
                x["id"]: x.get("topic", "")
                for x in data.get("excluded", [])
                if x.get("id") in known_ids
            },
        )

    return decide


def no_ai(_pairs: Sequence[Pair], _new: Sequence[Event]) -> Decisions:
    """Decider without AI: unclear pairs stay separate, no topic check."""
    return Decisions()


# --- the merge --------------------------------------------------------------------------


@dataclass
class MergeReport:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    merged_fuzzy: list[tuple[str, str]] = field(default_factory=list)
    """(crawled id, stored id) merged by similarity or AI."""
    excluded: dict[str, str] = field(default_factory=dict)
    cancelled: list[str] = field(default_factory=list)
    postponed: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)
    unconfirmed: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)


def apply(
    existing: EventsFile | None,
    crawled: Iterable[Event],
    *,
    today: date,
    covered_places: Iterable[str] | None = None,
    municipality_of: Mapping[str, str] | None = None,
    deep_check_days: int = 7,
    unconfirmed_after: int = 2,
    decide: Decide = no_ai,
    report: MergeReport | None = None,
) -> EventsFile:
    """Return the new ``events.json`` content.

    ``covered_places`` are the places (and municipalities) crawled successfully in
    this run; only stored events there can be "missed". ``None`` disables misses.
    """
    report = report if report is not None else MergeReport()
    stored = {e.id: e for e in (existing.events if existing else [])}
    crawled = list(crawled)

    # 1. Matching against stored events and earlier crawled events.
    target: dict[int, str | None] = {}  # crawled index -> id it merges into
    pairs: list[Pair] = []
    pool: dict[str, Event] = dict(stored)
    for i, event in enumerate(crawled):
        if event.id in pool:
            target[i] = event.id
            continue
        best_id, best = None, 0.0
        for other in pool.values():
            score = candidate_score(event, other, municipality_of)
            if score > best:
                best_id, best = other.id, score
        if best_id is not None and best >= CLEAR:
            target[i] = best_id
        else:
            target[i] = None
            if best_id is not None and best >= UNCLEAR:
                pairs.append(Pair(len(pairs) + 1, event, pool[best_id], best))
        if target[i] is None:
            pool[event.id] = event

    # 2. One AI call: unclear pairs and the topic check of new events.
    new_events = [e for i, e in enumerate(crawled) if target[i] is None]
    decisions = decide(pairs, new_events)
    for pair in pairs:
        if pair.number in decisions.same:
            index = next(i for i, e in enumerate(crawled) if e is pair.new)
            target[index] = pair.old.id
    remap = {crawled[i].id: t for i, t in target.items() if t is not None and t != crawled[i].id}
    report.merged_fuzzy.extend(remap.items())

    # 3. Apply, remapping parent and postponement links to stored ids.
    events = dict(stored)
    found: set[str] = set()
    for i, event in enumerate(crawled):
        if target[i] is None and event.id in decisions.excluded:
            report.excluded[event.id] = decisions.excluded[event.id]
            _log(f"dropped {event.title.de!r}: excluded topic {decisions.excluded[event.id]!r}")
            continue
        event = event.model_copy(
            update={
                "parent_id": remap.get(event.parent_id, event.parent_id),
                "postponed_to": remap.get(event.postponed_to, event.postponed_to),
            }
        )
        key = target[i] or event.id
        if key in events:
            events[key] = combine(events[key], event)
            if key in stored and key not in report.updated:
                report.updated.append(key)
        else:
            events[key] = event
            report.added.append(key)
        found.add(key)

    for key in found:
        status = events[key].status
        if status is EventStatus.CANCELLED:
            report.cancelled.append(key)
        elif status is EventStatus.POSTPONED:
            report.postponed.append(key)

    # Status of stored events that were not found again.
    if covered_places is not None:
        covered = {p.casefold() for p in covered_places}
        deep_end = today + timedelta(days=deep_check_days)
        for key, event in list(events.items()):
            in_window = event.schedule.start_date <= deep_end and event.schedule.last_date >= today
            if (
                key in found
                or key not in stored
                or not in_window
                or event.venue.place.casefold() not in covered
                or event.status in (EventStatus.CANCELLED, EventStatus.POSTPONED)
            ):
                continue
            misses = event.misses + 1
            status = event.status
            if misses >= unconfirmed_after and status is EventStatus.SCHEDULED:
                status = EventStatus.UNCONFIRMED
                report.unconfirmed.append(key)
            events[key] = event.model_copy(update={"misses": misses, "status": status})
            report.missed.append(key)

    # 4. Cleanup.
    yesterday = today - timedelta(days=1)
    kept = []
    for event in events.values():
        if event.schedule.last_date >= yesterday:
            kept.append(event)
        else:
            report.deleted.append(event.id)
    ids = {e.id for e in kept}
    kept = [
        e if e.parent_id is None or e.parent_id in ids else e.model_copy(update={"parent_id": None})
        for e in kept
    ]
    kept.sort(key=lambda e: (e.schedule.start_date, e.schedule.start_time or "", e.id))
    return EventsFile(updated_at=datetime.now(UTC).replace(microsecond=0), events=kept)


def _log(message: str) -> None:
    print(f"merge: {message}", file=sys.stderr, flush=True)
