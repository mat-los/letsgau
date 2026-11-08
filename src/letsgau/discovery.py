"""Discovery stage (default model: Opus): find and rate event sources per place.

Adds new sources with ``origin: discovery`` to ``data/sources.json`` and
writes ``instance/sources/suggested.md``.

Not implemented yet: planned for Phase 7 (see docs/CONCEPT.md, "Umsetzungsplan").
"""

from __future__ import annotations

from letsgau.config import Config

PHASE = 7


def run(config: Config, *, dry_run: bool = False, limit_places: int | None = None) -> None:
    """Run this stage. Raises :class:`NotImplementedError` until Phase 7."""
    raise NotImplementedError(f"{__name__} is planned for Phase {PHASE}")
