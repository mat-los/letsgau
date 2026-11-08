"""Merge stage: deterministic matching, AI tie-break (Haiku), status updates, cleanup.

Matches candidates against ``data/events.json``, appends sources, updates
``misses``/``status`` and deletes events that ended before yesterday.

Not implemented yet: planned for Phase 3 (see docs/CONCEPT.md, "Umsetzungsplan").
"""

from __future__ import annotations

from letsgau.config import Config

PHASE = 3


def run(config: Config, *, dry_run: bool = False, limit_places: int | None = None) -> None:
    """Run this stage. Raises :class:`NotImplementedError` until Phase 3."""
    raise NotImplementedError(f"{__name__} is planned for Phase {PHASE}")
