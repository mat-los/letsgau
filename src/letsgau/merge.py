"""Merge stage: combine crawl results with ``data/events.json``.

Steps (docs/CONCEPT.md, "Pipeline", step 5):

1. **Deterministic matching.** A crawled event matches a stored one if the ids are
   equal, or if the dates overlap, the place is in the same municipality (or both
   name the same venue on a shared source page), the start times on a shared day
   (``start_time`` or that day's ``opening_hours``, when both are known) are at
   most two hours apart and the
   normalized titles are similar (:func:`title_similarity`). Clear matches are
   merged: the stored id stays, texts/schedule/status come from the crawl,
   sources are appended.
2. **AI tie-break.** Unclear pairs (similarity between :data:`UNCLEAR` and
   :data:`CLEAR`) are decided by the merge model (default Haiku) with yes/no. The
   same call checks every new and stored event against ``excluded_topics``;
   matches are dropped (new) or removed (stored) and logged.
3. **Status.** Cancellations and postponements come from the crawl. Events in
   the deep-check window that were not found again although their municipality
   was crawled get ``misses + 1``; at ``events.unconfirmed_after_misses`` they
   become ``unconfirmed``. Found again, they are ``scheduled`` with ``misses = 0``.
4. **Cleanup.** Events that ended before yesterday are deleted.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any

from letsgau.build import hours_today
from letsgau.models import Event, EventsFile, EventSource, EventStatus, OpeningHours, Schedule
from letsgau.util import PROMPTS_DIR, UMLAUTS, logger, prompt_env

CLEAR = 0.85
"""Title similarity from which a pair is merged without asking the AI."""
UNCLEAR = 0.5
"""Title similarity from which a pair is sent to the AI tie-break."""
SERIES_SLOT = 0.4
"""Title similarity from which a single event on one day of a series at the same start time
goes to the AI tie-break (see :func:`same_slot`)."""
MAX_TIME_GAP_MINUTES = 120

_STOPWORDS = frozenset(
    {
        *("der", "die", "das", "den", "dem", "des", "und", "in", "im", "am", "an", "auf"),
        *("mit", "zum", "zur", "von", "vom", "fuer", "bei", "ein", "eine", "einer"),
        *("the", "a", "of", "and", "at", "on", "for", "with", "to", "by", "vs", "gegen"),
        # weekday words: "Markt (freitags)" and "Markt (samstags)" are one series; the
        # dates decide whether two entries overlap (see days_overlap)
        *("montag", "dienstag", "mittwoch", "donnerstag", "freitag", "samstag", "sonntag"),
        *("montags", "dienstags", "mittwochs", "donnerstags", "freitags", "samstags"),
        *("sonntags", "sonnabend", "sonnabends", "wochentags", "werktags", "wochenende"),
        *("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"),
        *("mondays", "tuesdays", "wednesdays", "thursdays", "fridays", "saturdays"),
        *("sundays", "weekdays", "weekend", "weekends"),
        # generic prefixes of a title ("Film: …", "Kino: …"): the rest decides
        *("film", "kino", "cinema", "movie"),
    }
)


# --- similarity ---------------------------------------------------------------------


def title_tokens(title: str, places: Iterable[str] = ()) -> list[str]:
    """Normalized title words without stop words, years and place names."""
    text = unicodedata.normalize("NFKD", title.lower().translate(UMLAUTS))
    text = text.encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", text)
    place_words = {
        w for p in places for w in re.findall(r"[a-z0-9]+", p.lower().translate(UMLAUTS))
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


def _all_days(s: Schedule) -> set[date]:
    """Every day of a schedule: its ``dates``, or each day of the range."""
    if s.dates:
        return set(s.dates)
    return {s.start_date + timedelta(days=i) for i in range((s.last_date - s.start_date).days + 1)}


def _shared_days(a: Event, b: Event) -> set[date]:
    if a.schedule.start_date > b.schedule.last_date or b.schedule.start_date > a.schedule.last_date:
        return set()
    return _all_days(a.schedule) & _all_days(b.schedule)


def _start_on(event: Event, day: date) -> int | None:
    """Start time on ``day`` in minutes: that day's ``opening_hours`` entry if it is a start time
    (``from`` equals ``to``: a screening, a guided tour on three dates at 18:00 and 19:00),
    else ``start_time``. Opening-hour ranges (a market open 14:00 to 18:00) are no start time."""
    if event.schedule.all_day:
        return None
    hours = hours_today(event, day)
    if hours and ":" in hours and len(hours) == 5:  # "20:00", not a range
        return _minutes(hours)
    return _minutes(event.schedule.start_time)


def _times_on_shared_days(a: Event, b: Event) -> list[tuple[date, int, int]]:
    """(day, start of ``a``, start of ``b``) on each shared day where both starts are known."""
    result = []
    for day in sorted(_shared_days(a, b)):
        ta, tb = _start_on(a, day), _start_on(b, day)
        if ta is not None and tb is not None:
            result.append((day, ta, tb))
    return result


def _venue_key(name: str | None) -> str:
    text = unicodedata.normalize("NFKD", (name or "").lower().translate(UMLAUTS))
    return "".join(re.findall(r"[a-z0-9]+", text.encode("ascii", "ignore").decode()))


def same_place(new: Event, old: Event, municipality_of: Mapping[str, str] | None = None) -> bool:
    """Same municipality, or the same named venue on a shared source page.

    The second case catches an event that one listing files under the wrong municipality
    (a venue in Schongau on the Altenstadt page of a regional calendar). Both conditions are
    needed: a venue name alone is not unique ("Marienplatz", "Pfarrsaal" exist in several
    places)."""
    m = municipality_of or {}
    place_a = m.get(new.venue.place, new.venue.place).casefold()
    place_b = m.get(old.venue.place, old.venue.place).casefold()
    if place_a == place_b:
        return True
    venue = _venue_key(new.venue.name)
    return (
        bool(venue)
        and venue == _venue_key(old.venue.name)
        and bool({s.url for s in new.sources} & {s.url for s in old.sources})
    )


def _active_days(s: Schedule) -> set[date] | None:
    """The single days of a schedule with ``dates``; ``None`` for a continuous range."""
    return set(s.dates) if s.dates else None


def days_overlap(a: Schedule, b: Schedule) -> bool:
    """True if both schedules have at least one actual day in common."""
    if a.start_date > b.last_date or b.start_date > a.last_date:
        return False
    da, db = _active_days(a), _active_days(b)
    if da is None and db is None:
        return True
    if da is not None and db is not None:
        return bool(da & db)
    days, other = (da, b) if da is not None else (db, a)
    return any(other.start_date <= d <= other.last_date for d in days or ())


def covers(a: Schedule, b: Schedule) -> bool:
    """True if every day of ``b`` is a day of ``a`` and ``a`` has more days."""

    return _all_days(b) < _all_days(a)


def candidate_score(
    new: Event, old: Event, municipality_of: Mapping[str, str] | None = None
) -> float:
    """Similarity of two events, 0 if date, place, time or role rule them out.

    A program item (``parent_id`` set) never matches a main event and vice versa.
    """
    if (new.parent_id is None) != (old.parent_id is None):
        return 0.0
    if not same_place(new, old, municipality_of):
        return 0.0
    if not days_overlap(new.schedule, old.schedule):
        return 0.0
    times = _times_on_shared_days(new, old)
    if times and min(abs(ta - tb) for _, ta, tb in times) > MAX_TIME_GAP_MINUTES:
        return 0.0
    m = municipality_of or {}
    place_a = m.get(new.venue.place, new.venue.place).casefold()
    place_b = m.get(old.venue.place, old.venue.place).casefold()
    # venue names count like place names: "Lagerhaus-Kino: Heimsuchung" = "Heimsuchung"
    places = {new.venue.place, old.venue.place, place_a, place_b}
    places |= {v for v in (new.venue.name, old.venue.name) if v}
    return title_similarity(new.title.de, old.title.de, places)


def clear_by_source(
    new: Event, old: Event, municipality_of: Mapping[str, str] | None = None
) -> bool:
    """Same slot (day, start time, municipality), a shared source page and a title score of at
    least :data:`UNCLEAR`: the same page describes one event twice under different titles
    ("Gospelchor-Probe 'Get Together'" / "Gospelchor-Projekt: Probe"). Merged without the
    AI, so duplicates do not survive a failed tie-break."""
    if not same_slot(new, old, municipality_of):
        return False
    if not {s.url for s in new.sources} & {s.url for s in old.sources}:
        return False
    return candidate_score(new, old, municipality_of) >= UNCLEAR


def same_slot(new: Event, old: Event, municipality_of: Mapping[str, str] | None = None) -> bool:
    """Same start time (both known) on a shared day, same place, same role.

    Such pairs go to the AI tie-break even when their titles share few words
    ("Fibromyalgiesymposium in Schongau" vs. "Symposium Fibromyalgie"). The start time of a
    day may come from ``opening_hours`` (see :func:`_start_on`). On the first day of both any
    title qualifies; on a later shared day (a single event on one date of a series:
    "Clubabend" on 18 and 25 Nov vs. "Bilderschau" on 25 Nov) the titles need a score of at
    least :data:`SERIES_SLOT`, or every weekly series would pair with every event at its time.
    """
    if (new.parent_id is None) != (old.parent_id is None):
        return False
    if not same_place(new, old, municipality_of):
        return False
    same_time = [day for day, ta, tb in _times_on_shared_days(new, old) if ta == tb]
    if not same_time:
        return False
    if new.schedule.start_date == old.schedule.start_date in same_time:
        return True
    return candidate_score(new, old, municipality_of) >= SERIES_SLOT


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


def merge_hours(old: Schedule, new: Schedule) -> list[OpeningHours] | None:
    """The stored opening hours, updated by the fresh ones: an entry of ``new`` replaces the
    entry of ``old`` for the same days; entries for other days are added."""
    if not new.opening_hours:
        return old.opening_hours
    fresh = {h.days for h in new.opening_hours}
    kept = [h for h in old.opening_hours or [] if h.days not in fresh]
    return kept + list(new.opening_hours)


MAX_TAGS = 3


def merge_tags(new: Event, old: Event) -> list[str]:
    """The fresh tags first (their primary tag stays primary), then stored ones they lack, at
    most :data:`MAX_TAGS`: a duplicate found under two categories keeps both."""
    tags = list(new.tags)
    for tag in old.tags:
        if tag not in tags and len(tags) < MAX_TAGS:
            tags.append(tag)
    return tags


def combine(old: Event, new: Event) -> Event:
    """Update a stored event with a fresh crawl result; the stored id stays."""
    status = new.status
    if status is EventStatus.UNCONFIRMED:  # never set by the crawl, keep it consistent
        status = EventStatus.SCHEDULED
    texts: dict[str, Any] = {}
    schedule = new.schedule
    if covers(old.schedule, new.schedule):
        # only part of a known multi-day event or series (one day, or the Saturdays of a
        # Friday/Saturday market): keep the full schedule and its texts
        schedule = old.schedule.model_copy(
            update={"opening_hours": merge_hours(old.schedule, new.schedule)}
        )
        texts = {"title": old.title, "description": old.description}
    return new.model_copy(
        update={
            **texts,
            "id": old.id,
            "status": status,
            "schedule": schedule,
            "tags": merge_tags(new, old),
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
    env = prompt_env(PROMPTS_DIR)
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
    excluded_title: Callable[[str], str | None] | None = None,
) -> EventsFile:
    """Return the new ``events.json`` content.

    ``excluded_title`` returns the matching title pattern of an event title, if any.
    ``covered_places`` are the places (and municipalities) crawled successfully in
    this run; only stored events there can be "missed". ``None`` disables misses.
    """
    report = report if report is not None else MergeReport()
    stored = {e.id: e for e in (existing.events if existing else [])}
    # stored events whose title matches ``excluded_title_patterns`` (also a pattern added
    # later) are removed without asking the AI; new ones are dropped by crawl.to_event
    for key, event in list(stored.items()):
        pattern = excluded_title(event.title.de) if excluded_title else None
        if pattern is not None:
            report.excluded[key] = f"title pattern {pattern!r}"
            _log(f"removed {event.title.de!r}: title pattern {pattern!r}")
            del stored[key]
    crawled = list(crawled)

    # 0. Duplicates already in storage: clear ones (similar title, shared days, same place)
    #    are merged directly, same-slot ones are asked in the same AI call.
    stored_pairs: list[Pair] = []
    clear_stored: list[Pair] = []
    open_events = [
        e for e in stored.values() if e.status not in (EventStatus.CANCELLED, EventStatus.POSTPONED)
    ]
    for i, old in enumerate(open_events):
        for new in open_events[i + 1 :]:
            score = candidate_score(new, old, municipality_of)
            if score >= CLEAR or clear_by_source(new, old, municipality_of):
                clear_stored.append(Pair(0, new, old, score))
            elif score >= UNCLEAR or same_slot(new, old, municipality_of):
                stored_pairs.append(Pair(len(stored_pairs) + 1, new, old, score))

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
        by_source = [o for o in pool.values() if clear_by_source(event, o, municipality_of)]
        if by_source and (best_id is None or best < CLEAR):
            best_id = max(by_source, key=lambda o: candidate_score(event, o, municipality_of)).id
            best = CLEAR
        if best_id is not None and best >= CLEAR:
            target[i] = best_id
        else:
            target[i] = None
            number = len(stored_pairs) + len(pairs) + 1
            if best_id is not None and best >= UNCLEAR:
                pairs.append(Pair(number, event, pool[best_id], best))
            else:
                slot = [o for o in pool.values() if same_slot(event, o, municipality_of)]
                if slot:
                    other = max(slot, key=lambda o: candidate_score(event, o, municipality_of))
                    score = candidate_score(event, other, municipality_of)
                    pairs.append(Pair(number, event, other, score))
        if target[i] is None:
            pool[event.id] = event

    # 2. One AI call: unclear pairs and the topic check of new events.
    new_events = [e for i, e in enumerate(crawled) if target[i] is None]
    # stored events are checked too, so a topic added to excluded_topics later also removes
    # what is already in the calendar (cheap: one line per event for the merge model)
    decisions = decide([*stored_pairs, *pairs], [*new_events, *open_events])
    alias: dict[str, str] = {}  # removed stored duplicate -> kept stored event
    for pair in [*clear_stored, *stored_pairs]:
        if pair.number and pair.number not in decisions.same:
            continue
        if {pair.new.id, pair.old.id} & alias.keys():
            continue
        keep, drop = sorted((pair.old, pair.new), key=lambda e: (e.first_seen, -len(e.sources)))
        stored[keep.id] = combine(stored[keep.id], stored[drop.id])
        del stored[drop.id]
        alias[drop.id] = keep.id
        report.merged_fuzzy.append((drop.id, keep.id))
    if alias:
        for key, event in stored.items():
            if event.parent_id in alias or event.postponed_to in alias:
                stored[key] = event.model_copy(
                    update={
                        "parent_id": alias.get(event.parent_id, event.parent_id),
                        "postponed_to": alias.get(event.postponed_to, event.postponed_to),
                    }
                )
        for i, t in target.items():
            if t in alias:
                target[i] = alias[t]
    for pair in pairs:
        if pair.number in decisions.same:
            index = next(i for i, e in enumerate(crawled) if e is pair.new)
            target[index] = alias.get(pair.old.id, pair.old.id)
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

    for key in [k for k in stored if k in decisions.excluded and k in events]:
        report.excluded[key] = decisions.excluded[key]
        _log(f"removed {events[key].title.de!r}: excluded topic {decisions.excluded[key]!r}")
        del events[key]
        found.discard(key)

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


_log = logger("merge")
