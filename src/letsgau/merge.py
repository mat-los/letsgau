"""Merge stage: combine crawl results with ``data/events.json``.

Phase 2 (this version) is deterministic and id-based:

* an event with a known ``id`` (start date + slug of title and place) updates
  the stored one: texts, schedule, tags and status from the crawl, sources
  appended (``last_seen`` refreshed), ``first_seen`` kept, ``misses`` reset;
* new ids are added;
* events that ended before yesterday are deleted.

Phase 3 adds fuzzy matching of titles and dates, the AI tie-break (Haiku),
``misses``/``unconfirmed`` for events not found again, and the topic check.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta

from letsgau.models import Event, EventsFile, EventSource


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
    """Update a stored event with a fresh crawl result of the same id."""
    return new.model_copy(
        update={
            "sources": merge_sources(old.sources, new.sources),
            "first_seen": min(old.first_seen, new.first_seen),
            "last_verified": max(old.last_verified, new.last_verified),
            "misses": 0,
        }
    )


def apply(existing: EventsFile | None, crawled: Iterable[Event], *, today: date) -> EventsFile:
    """Return the new ``events.json`` content."""
    events = {e.id: e for e in (existing.events if existing else [])}
    for event in crawled:
        events[event.id] = combine(events[event.id], event) if event.id in events else event
    yesterday = today - timedelta(days=1)
    kept = [e for e in events.values() if e.schedule.last_date >= yesterday]
    ids = {e.id for e in kept}
    # Program items of a deleted parent lose their parent link.
    kept = [
        e if e.parent_id is None or e.parent_id in ids else e.model_copy(update={"parent_id": None})
        for e in kept
    ]
    kept.sort(key=lambda e: (e.schedule.start_date, e.schedule.start_time or "", e.id))
    return EventsFile(updated_at=datetime.now(UTC).replace(microsecond=0), events=kept)
