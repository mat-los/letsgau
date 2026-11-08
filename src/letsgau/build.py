"""Site build stage: Jinja2 renders day pages (DE/EN), JSON, ICS, llms.txt, sitemap.

Aborts if placeholders are left in the instance or ``site.owner`` does not
match ``GITHUB_REPOSITORY_OWNER``.

Not implemented yet: planned for Phase 4 (see docs/CONCEPT.md, "Umsetzungsplan").
"""

from __future__ import annotations

from letsgau.config import Config

PHASE = 4


def run(config: Config, *, dry_run: bool = False, limit_places: int | None = None) -> None:
    """Run this stage. Raises :class:`NotImplementedError` until Phase 4."""
    raise NotImplementedError(f"{__name__} is planned for Phase {PHASE}")
