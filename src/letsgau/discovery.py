"""Discovery stage (default model: Opus): find and rate event sources per municipality.

* Municipalities from ``data/places.json`` are processed in batches of
  ``limits.places_per_request``; each batch is one AI call with web search
  (``prompts/discovery.md``) that returns rated sources and blacklist candidates.
* New sources are added to ``data/sources.json`` with ``origin: discovery``;
  known ones get the new rating. Whitelist entries are synced in with
  ``origin: whitelist``; blacklisted domains are never added and existing
  entries become ``status: blacklisted``.
* ``data/suggested.md`` lists whitelist candidates (many hits or a top rating)
  and blacklist candidates as lines to copy into the YAML files. It lives on the
  ``data`` branch with the other generated files (the bot never commits to
  ``main``).

The events stage uses the active, well-rated sources of each batch (see
:func:`known_sources_for`) and counts hits per source (:func:`record_hits`).
Discovery runs monthly by default and is forced after a geo change.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import ValidationError

from letsgau.ai_runner import AiError, AiRequest, Runner, make_runner
from letsgau.config import Config
from letsgau.models import (
    Event,
    PlacesFile,
    Source,
    SourceKind,
    SourceOrigin,
    SourcesFile,
    SourceStatus,
    Usage,
)

DATA_DIR = Path("data")
PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
MIN_RATING_FOR_CRAWL = 3
MAX_KNOWN_PER_BATCH = 12
WHITELIST_HITS = 3
"""Hits from which an active source is suggested for the whitelist."""


class DiscoveryError(Exception):
    """Missing inputs or no usable result."""


# --- helpers --------------------------------------------------------------------------------


def normalize_url(url: str) -> str:
    """Comparable form: lower-case host without ``www.``, no trailing slash, no fragment."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower().removeprefix("www.")
    path = parts.path.rstrip("/")
    query = f"?{parts.query}" if parts.query else ""
    return f"{host}{path}{query}"


def domain_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def is_blocked(url: str, blocked: Iterable[str]) -> bool:
    host = domain_of(url)
    return any(host == d or host.endswith("." + d) for d in blocked)


def load_sources(data_dir: Path = DATA_DIR) -> SourcesFile | None:
    path = data_dir / "sources.json"
    if not path.exists():
        return None
    return SourcesFile.model_validate_json(path.read_text(encoding="utf-8"))


def save_sources(sources: SourcesFile, data_dir: Path = DATA_DIR) -> None:
    path = data_dir / "sources.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(sources.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


# --- AI answer ------------------------------------------------------------------------------


def answer_schema() -> dict[str, Any]:
    def obj(properties: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }

    source = obj(
        {
            "url": {"type": "string"},
            "name": {"type": "string"},
            "kind": {"type": "string", "enum": [k.value for k in SourceKind]},
            "places": {"type": "array", "items": {"type": "string"}},
            "has_current_events": {"type": "boolean"},
            "dates_as_text": {"type": "boolean"},
            "update_frequency": {
                "type": "string",
                "enum": ["weekly", "monthly", "rare", "unknown"],
            },
            "rating": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
            "note": {"type": "string"},
        }
    )
    candidate = obj({"domain": {"type": "string"}, "reason": {"type": "string"}})
    return obj(
        {
            "sources": {"type": "array", "items": source},
            "blacklist_candidates": {"type": "array", "items": candidate},
            "notes": {"type": "string"},
        }
    )


def render_prompt(
    config: Config,
    groups: Sequence[Any],
    known: Sequence[Source],
    blocked: Sequence[str],
    max_searches: int,
) -> str:
    env = Environment(
        loader=FileSystemLoader(PROMPTS_DIR),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )
    return env.get_template("discovery.md").render(
        region=config.region,
        groups=groups,
        known=known,
        blocked_domains=blocked,
        max_searches=max_searches,
    )


# --- merging ---------------------------------------------------------------------------------


@dataclass
class DiscoveryResult:
    sources: SourcesFile
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    blacklist_candidates: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)


def sync_lists(
    sources: dict[str, Source],
    whitelist: Sequence[Any],
    blocked: Sequence[str],
    today: date,
) -> None:
    """Add whitelist entries (``origin: whitelist``) and mark blacklisted domains."""
    for entry in whitelist:
        key = normalize_url(entry.url)
        if key in sources:
            sources[key] = sources[key].model_copy(
                update={"origin": SourceOrigin.WHITELIST, "status": SourceStatus.ACTIVE}
            )
        else:
            sources[key] = Source(
                url=entry.url,
                domain=domain_of(entry.url),
                name=entry.note,
                places=list(entry.places),
                origin=SourceOrigin.WHITELIST,
                first_seen=today,
            )
    for key, source in list(sources.items()):
        if is_blocked(source.url, blocked):
            sources[key] = source.model_copy(update={"status": SourceStatus.BLACKLISTED})


def merge_answer(
    sources: dict[str, Source],
    data: Mapping[str, Any],
    *,
    batch_places: Sequence[str],
    resolve: Any,
    blocked: Sequence[str],
    today: date,
    result: DiscoveryResult,
) -> None:
    """Add or update the sources of one AI answer."""
    for item in data.get("sources") or []:
        url = str(item.get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            result.warnings.append(f"invalid URL skipped: {url!r}")
            continue
        if is_blocked(url, blocked):
            continue
        places = [p for p in (resolve(n) for n in item.get("places") or []) if p] or list(
            batch_places
        )
        key = normalize_url(url)
        try:
            kind = SourceKind(item.get("kind", "other"))
        except ValueError:
            kind = SourceKind.OTHER
        rating = item.get("rating")
        update = {
            "name": str(item.get("name") or ""),
            "kind": kind,
            "rating": rating if rating in (1, 2, 3, 4, 5) else None,
            "update_frequency": item.get("update_frequency") or "unknown",
            "note": str(item.get("note") or ""),
            "last_checked": today,
        }
        try:
            if key in sources:
                old = sources[key]
                merged = sorted(set(old.places) | set(places))
                sources[key] = old.model_copy(update={**update, "places": merged})
                Source.model_validate(sources[key].model_dump())
                result.updated.append(url)
            else:
                sources[key] = Source(
                    url=url,
                    domain=domain_of(url),
                    places=sorted(set(places)),
                    origin=SourceOrigin.DISCOVERY,
                    first_seen=today,
                    **update,
                )
                result.added.append(url)
        except ValidationError as exc:
            result.warnings.append(f"source {url!r} rejected: {exc.errors()[0]['msg']}")
    for candidate in data.get("blacklist_candidates") or []:
        domain = str(candidate.get("domain", "")).lower().removeprefix("www.")
        if domain and domain not in blocked:
            result.blacklist_candidates[domain] = str(candidate.get("reason", ""))


# --- events stage helpers ---------------------------------------------------------------------


def known_sources_for(
    sources: SourcesFile | None, municipalities: Iterable[str], exclude_urls: Iterable[str] = ()
) -> list[Source]:
    """Active, well-rated sources of these municipalities (for the crawl prompt)."""
    if sources is None:
        return []
    wanted = {m.casefold() for m in municipalities}
    skip = {normalize_url(u) for u in exclude_urls}
    picked = [
        s
        for s in sources.sources
        if s.status is SourceStatus.ACTIVE
        and s.origin is not SourceOrigin.WHITELIST
        and normalize_url(s.url) not in skip
        and {p.casefold() for p in s.places} & wanted
        and ((s.rating or 0) >= MIN_RATING_FOR_CRAWL or s.hits >= 2)
    ]
    picked.sort(key=lambda s: (-(s.rating or 0), -s.hits, s.url))
    return picked[:MAX_KNOWN_PER_BATCH]


def record_hits(
    sources: SourcesFile | None,
    events: Sequence[Event],
    municipality_of: Mapping[str, str],
    blocked: Sequence[str],
    today: date,
) -> SourcesFile:
    """Count events per source; unknown primary sources are added as ``origin: crawl``."""
    by_key = {normalize_url(s.url): s for s in (sources.sources if sources else [])}

    def find(url: str) -> str | None:
        key = normalize_url(url)
        if key in by_key:
            return key
        # longest known source URL that is a prefix of this event URL
        matches = [k for k in by_key if key.startswith(k + "/") or key.startswith(k + "?")]
        return max(matches, key=len) if matches else None

    for event in events:
        counted: set[str] = set()
        for src in event.sources:
            key = find(src.url)
            if key is None and src.primary and not is_blocked(src.url, blocked):
                parts = urlsplit(src.url)
                root = f"{parts.scheme}://{parts.netloc}/"
                key = normalize_url(root)
                if key not in by_key:
                    place = municipality_of.get(event.venue.place, event.venue.place)
                    by_key[key] = Source(
                        url=root,
                        domain=domain_of(root),
                        places=[place],
                        origin=SourceOrigin.CRAWL,
                        first_seen=today,
                    )
            if key is None or key in counted:
                continue
            counted.add(key)
            source = by_key[key]
            by_key[key] = source.model_copy(update={"hits": source.hits + 1, "last_hit": today})
    return SourcesFile(
        updated_at=datetime.now(UTC).replace(microsecond=0),
        sources=sorted(by_key.values(), key=lambda s: (s.domain, s.url)),
    )


# --- suggestions ------------------------------------------------------------------------------


def suggestions_markdown(
    sources: SourcesFile, whitelist_urls: Iterable[str], candidates: Mapping[str, str], today: date
) -> str:
    on_whitelist = {normalize_url(u) for u in whitelist_urls}
    good = [
        s
        for s in sources.sources
        if s.status is SourceStatus.ACTIVE
        and normalize_url(s.url) not in on_whitelist
        and (s.hits >= WHITELIST_HITS or (s.rating or 0) >= 4)
    ]
    good.sort(key=lambda s: (-s.hits, -(s.rating or 0), s.url))
    bad = dict(candidates)
    for s in sources.sources:
        if s.rating == 1 and s.status is SourceStatus.ACTIVE:
            bad.setdefault(s.domain, s.note or "rated 1 by discovery")
    lines = [
        "# Suggested sources",
        "",
        f"Generated by the discovery job on {today.isoformat()}. Copy a block into",
        "`instance/sources/whitelist.yaml` or `instance/sources/blacklist.yaml` to",
        "accept a suggestion.",
        "",
        "## Whitelist candidates",
        "",
    ]
    if good:
        lines.append("```yaml")
        for s in good:
            info = f"{s.hits} hits" + (f", rated {s.rating}/5" if s.rating else "")
            note = f"{s.name} ({info})".strip()
            places = ", ".join(json.dumps(p, ensure_ascii=False) for p in s.places)
            lines += [
                f"- url: {json.dumps(s.url)}",
                f"  note: {json.dumps(note, ensure_ascii=False)}",
                f"  places: [{places}]",
            ]
        lines.append("```")
    else:
        lines.append("_None yet._")
    lines += ["", "## Blacklist candidates", ""]
    if bad:
        lines.append("```yaml")
        for domain, reason in sorted(bad.items()):
            lines += [
                f"- domain: {json.dumps(domain)}",
                f"  reason: {json.dumps(reason, ensure_ascii=False)}",
            ]
        lines.append("```")
    else:
        lines.append("_None._")
    return "\n".join(lines) + "\n"


# --- stage ------------------------------------------------------------------------------------


def run(
    config: Config,
    *,
    dry_run: bool = False,
    limit_places: int | None = None,
    runner: Runner | None = None,
    data_dir: Path = DATA_DIR,
    today: date | None = None,
) -> DiscoveryResult:
    """CLI entry point for ``--job discovery``."""
    from letsgau import crawl  # late import: crawl uses this module too

    today = today or date.today()
    places_path = data_dir / "places.json"
    if not places_path.exists():
        raise DiscoveryError("data/places.json is missing; run `letsgau run --job geo` first")
    places = PlacesFile.model_validate_json(places_path.read_text(encoding="utf-8"))
    groups = crawl.group_places(places.places)
    if limit_places is not None:
        groups = groups[:limit_places]
    index = crawl.PlaceIndex(places.places)
    municipality_of = {p.name: p.municipality or p.name for p in places.places}

    def to_municipality(name: str) -> str | None:
        place = index.resolve(name)
        return municipality_of.get(place, place) if place else None

    whitelist = crawl.expand_whitelist(
        crawl._load_list(Path(config.sources.whitelist_file), crawl.WhitelistEntry),
        [g.name for g in crawl.group_places(places.places)],
    )
    blacklist = crawl._load_list(Path(config.sources.blacklist_file), crawl.BlacklistEntry)
    blocked = sorted({e.domain for e in blacklist})
    existing = load_sources(data_dir)
    sources = {normalize_url(s.url): s for s in (existing.sources if existing else [])}
    result = DiscoveryResult(sources=existing or SourcesFile(updated_at=datetime.now(UTC)))
    sync_lists(sources, whitelist, blocked, today)

    runner = runner or make_runner(config)
    size = config.limits.places_per_request
    batches = [groups[i : i + size] for i in range(0, len(groups), size)]
    budget = config.limits.max_web_searches_per_run
    for position, batch in enumerate(batches):
        remaining = budget - result.usage.web_searches
        if remaining < 3:
            result.warnings.append(f"batch {position + 1}: skipped, search budget used up")
            continue
        names = [g.name for g in batch]
        known = [
            s
            for s in sources.values()
            if {p.casefold() for p in s.places} & {n.casefold() for n in names}
        ]
        per_batch = max(3, min(40, remaining // (len(batches) - position)))
        request = AiRequest(
            step=f"discovery batch {position + 1}/{len(batches)}",
            model=config.models.discovery,
            prompt=render_prompt(config, batch, known, blocked, per_batch),
            schema=answer_schema(),
            blocked_domains=blocked,
            max_searches=per_batch,
        )
        try:
            answer = runner.run(request)
        except AiError as exc:
            result.warnings.append(f"batch {position + 1}: {exc}")
            continue
        result.usage = result.usage.add(answer.usage)
        merge_answer(
            sources,
            answer.data,
            batch_places=names,
            resolve=to_municipality,
            blocked=blocked,
            today=today,
            result=result,
        )
        note = str(answer.data.get("notes") or "").strip()
        if note:
            print(f"discovery: note from batch {position + 1}: {note}")
    if batches and not result.usage.requests:
        raise DiscoveryError("every discovery batch failed: " + "; ".join(result.warnings))

    result.sources = SourcesFile(
        updated_at=datetime.now(UTC).replace(microsecond=0),
        sources=sorted(sources.values(), key=lambda s: (s.domain, s.url)),
    )
    for warning in result.warnings:
        print(f"warning: discovery: {warning}", file=sys.stderr)
    active = sum(s.status is SourceStatus.ACTIVE for s in result.sources.sources)
    print(
        f"discovery: {len(result.added)} new, {len(result.updated)} re-rated sources, "
        f"{active} active in total, {len(result.blacklist_candidates)} blacklist candidates; "
        f"{result.usage.web_searches} searches" + ("; dry run, nothing written" if dry_run else "")
    )
    if not dry_run:
        save_sources(result.sources, data_dir)
        (data_dir / "suggested.md").write_text(
            suggestions_markdown(
                result.sources, [w.url for w in whitelist], result.blacklist_candidates, today
            ),
            encoding="utf-8",
        )
    return result
