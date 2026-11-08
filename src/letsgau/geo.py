"""Geo stage: isochrone (OpenRouteService), places in the area (Overpass), drive times.

Recomputed only when the fingerprint of ``region.center`` and
``region.drive_time_minutes`` changes; a change forces discovery.

Not implemented yet: planned for Phase 1 (see docs/CONCEPT.md, "Umsetzungsplan").
"""

from __future__ import annotations

from letsgau.config import Config

PHASE = 1


def run(config: Config, *, dry_run: bool = False, limit_places: int | None = None) -> None:
    """Run this stage. Raises :class:`NotImplementedError` until Phase 1."""
    raise NotImplementedError(f"{__name__} is planned for Phase {PHASE}")
