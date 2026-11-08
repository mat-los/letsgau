"""Event crawl stage (default model: Sonnet).

Splits places into batches of ``limits.places_per_request``, asks Claude for
event candidates in the crawl window and validates them with Pydantic.

Not implemented yet: planned for Phase 2 (see docs/CONCEPT.md, "Umsetzungsplan").
"""

from __future__ import annotations

from letsgau.config import Config

PHASE = 2


def run(config: Config, *, dry_run: bool = False, limit_places: int | None = None) -> None:
    """Run this stage. Raises :class:`NotImplementedError` until Phase 2."""
    raise NotImplementedError(f"{__name__} is planned for Phase {PHASE}")
