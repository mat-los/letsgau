"""Discovery stage: find and rate event sources per municipality.

* Municipalities from ``data/places.json`` are processed in batches of
  ``depth.discovery_places_per_request``. With ``depth.discovery_focus_passes``
  each batch gets three AI calls with web search (``prompts/discovery.md``),
  one per focus (:data:`FOCUSES`: official calendars, clubs, venues), each with
  a fixed query checklist; later passes see the sources of earlier ones. Every
  call returns rated sources, blacklist candidates and a coverage report per
  category, which ends up in ``data/suggested.md``.
* New sources are added to ``data/sources.json`` with ``origin: discovery``;
  known ones get the new rating. Whitelist entries are synced in with
  ``origin: whitelist``; blacklisted domains are never added and existing
  entries become ``status: blacklisted``.
* With Instagram on (``sources.instagram_file``), every call also reports the Instagram
  accounts of local organisers it came across; the venues pass looks for those of cafés,
  bars, clubs and venues on purpose. New accounts go into ``data/instagram.json``
  (``origin: mined``) and are read by the next events run.
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
import os
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import ValidationError

from letsgau import instagram
from letsgau.ai_runner import AiError, AiRequest, Runner, make_runner
from letsgau.config import Config
from letsgau.models import (
    Event,
    InstagramAccount,
    InstagramFile,
    PlacesFile,
    RejectedCandidate,
    Source,
    SourceKind,
    SourceOrigin,
    SourcesFile,
    SourceStatus,
    State,
    Usage,
)

DATA_DIR = Path("data")
PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
MIN_RATING_FOR_CRAWL = 3
MAX_KNOWN_PER_BATCH = 12
RECHECK_SOURCES_DAYS = 90
"""Known sources rated longer ago than this (and without recent hits) are re-rated; the others
are only listed, so that the AI neither opens nor reports them again (saves tokens: the events
stage reads the good ones twice a week anyway)."""
WHITELIST_HITS = 3
"""Hits from which an active source is suggested for the whitelist."""


class DiscoveryError(Exception):
    """Missing inputs or no usable result."""


@dataclass(frozen=True)
class Focus:
    """One discovery pass: kinds of sources and the queries to run per municipality."""

    id: str
    title: str
    categories: tuple[str, ...]
    queries: tuple[str, ...]
    """German search queries; ``{m}`` is replaced by the municipality."""
    extra: str = ""


FOCUSES: tuple[Focus, ...] = (
    Focus(
        id="official",
        title="official and regional calendars",
        categories=(
            "event calendar of the municipality",
            "tourist office and regional tourism calendars",
            "regional event calendars and aggregators that list this municipality",
            "local newspapers and their event pages",
            "the municipality's directory of clubs and institutions (for the next passes)",
        ),
        queries=(
            "{m} Veranstaltungen",
            "{m} Veranstaltungskalender",
            "{m} Termine Veranstaltungen Gemeinde",
            "{m} Tourist Information Veranstaltungen",
            "{m} Veranstaltungen Wochenende",
            "{m} Vereine Verzeichnis",
            "{m} Lokalnachrichten Termine",
        ),
        extra=(
            "If the municipality has a directory of clubs (Vereinsverzeichnis) or institutions, "
            'report it with kind "municipality" and the note "club directory", even if it '
            "lists no events itself: the next pass works through it."
        ),
    ),
    Focus(
        id="clubs",
        title="clubs, parishes and organisations",
        categories=(
            "sports clubs (home matches, tournaments, club festivals)",
            "music: brass bands, choirs, music societies",
            "traditional costume, marksmen and folk clubs",
            "fire brigade and charitable organisations",
            "Catholic and Protestant parishes, free churches",
            "youth centre, youth and senior groups, family centres",
            "theatre groups, culture and history societies",
        ),
        queries=(
            "{m} Vereine",
            "{m} Sportverein Termine",
            "{m} TSV OR SV OR FC Spielplan",
            "{m} Musikverein OR Blaskapelle OR Stadtkapelle Termine",
            "{m} Chor Konzert",
            "{m} Trachtenverein OR Schützenverein Termine",
            "{m} Feuerwehr Termine",
            "{m} Pfarrei Pfarreiengemeinschaft Termine",
            "{m} evangelische Kirchengemeinde Veranstaltungen",
            "{m} Jugendzentrum OR Jugendtreff",
            "{m} Theaterverein OR Heimatverein",
            "{m} Kolping OR Frauenbund OR Seniorenclub",
        ),
        extra=(
            "Open the municipality's club directory if it exists (see the known sources) and "
            "check the website of every club that might hold public events. Report every club "
            "website you checked, also those without events (rating 1 or 2)."
        ),
    ),
    Focus(
        id="venues",
        title="venues, institutions and annual events",
        categories=(
            "cultural centres, stages, music clubs and concert venues",
            "cinema and theatre programmes",
            "museums, library, adult education centre (vhs), music school",
            "swimming pools, ice rinks and other leisure facilities with events",
            "inns, breweries, cafés, bars and farms that hold events",
            "organisers of annual events: Christmas market, carnival, folk and town festivals",
            "markets: weekly market, flea markets, craft markets",
        ),
        queries=(
            "{m} Kultur Programm",
            "{m} Kino Programm",
            "{m} Museum Führungen",
            "{m} Bücherei Veranstaltungen",
            "{m} vhs Kurse Vorträge",
            "{m} Musikschule Konzert",
            "{m} Hallenbad OR Eisstadion Veranstaltungen",
            "{m} Gasthof OR Brauerei Veranstaltungen",
            "{m} Café Bar Live Musik",
            "{m} Christkindlmarkt OR Weihnachtsmarkt",
            "{m} Volksfest OR Stadtfest OR Dorffest",
            "{m} Markt Flohmarkt Termine",
        ),
    ),
)
COVERAGE_STATUS = ("found", "nothing_found", "not_checked")


@dataclass
class Coverage:
    """One line of a coverage report: was a category of sources found for a municipality?"""

    municipality: str
    focus: str
    category: str
    status: str
    note: str = ""


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


SOCIAL_DOMAINS = (
    "instagram.com",
    "facebook.com",
    "fb.com",
    "fb.me",
    "tiktok.com",
    "threads.net",
    "x.com",
    "twitter.com",
    "youtube.com",
    "linktr.ee",
)
"""Social media: never a source in ``sources.json`` (Instagram accounts are kept in
``data/instagram.json`` instead), however an event or an AI answer links to it."""


def is_social(url: str) -> bool:
    return is_blocked(url, SOCIAL_DOMAINS)


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


def answer_schema(with_coverage: bool = False, with_instagram: bool = False) -> dict[str, Any]:
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
    properties: dict[str, Any] = {
        "sources": {"type": "array", "items": source},
        "blacklist_candidates": {"type": "array", "items": candidate},
        "notes": {"type": "string"},
    }
    if with_coverage:
        coverage = obj(
            {
                "municipality": {"type": "string"},
                "category": {"type": "string"},
                "status": {"type": "string", "enum": list(COVERAGE_STATUS)},
                "note": {"type": "string"},
            }
        )
        properties["coverage"] = {"type": "array", "items": coverage}
    if with_instagram:
        account = obj(
            {
                "username": {"type": "string"},
                "name": {"type": "string"},
                "places": {"type": "array", "items": {"type": "string"}},
            }
        )
        properties["instagram_accounts"] = {"type": "array", "items": account}
    return obj(properties)


LOOKUP = "instagram"
"""Marker of the Instagram lookup call that follows the passes of a batch."""
MAX_LOOKUP_VENUES = 40


def lookup_schema() -> dict[str, Any]:
    candidate = {
        "type": "object",
        "properties": {
            "username": {"type": "string"},
            "name": {"type": "string"},
            "places": {"type": "array", "items": {"type": "string"}},
            "website": {"type": "string"},
        },
        "required": ["username", "name", "places", "website"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "instagram_candidates": {"type": "array", "items": candidate},
            "notes": {"type": "string"},
        },
        "required": ["instagram_candidates", "notes"],
        "additionalProperties": False,
    }


def render_lookup_prompt(
    config: Config,
    groups: Sequence[Any],
    venues: Sequence[Source],
    known_accounts: Sequence[str],
    max_searches: int,
) -> str:
    """The prompt of the Instagram lookup call (``prompts/instagram_lookup.md``)."""
    env = Environment(
        loader=FileSystemLoader(PROMPTS_DIR),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )
    return env.get_template("instagram_lookup.md").render(
        region=config.region,
        groups=groups,
        venues=venues[:MAX_LOOKUP_VENUES],
        known_accounts=known_accounts,
        max_searches=max_searches,
        max_places=MAX_LOOKUP_VENUES,
    )


def render_prompt(
    config: Config,
    groups: Sequence[Any],
    known: Sequence[Source],
    blocked: Sequence[str],
    max_searches: int,
    focus: Focus | None = None,
    instagram: bool = False,
    recheck: Sequence[Source] = (),
) -> str:
    """The discovery prompt. ``known`` sources are only listed (not to be opened again),
    ``recheck`` ones are to be opened and rated anew (see :func:`is_fresh`)."""
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
        focus=focus,
        instagram=instagram,
        recheck=recheck,
    )


# --- merging ---------------------------------------------------------------------------------


@dataclass
class DiscoveryResult:
    sources: SourcesFile
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    blacklist_candidates: dict[str, str] = field(default_factory=dict)
    coverage: list[Coverage] = field(default_factory=list)
    instagram_added: list[str] = field(default_factory=list)
    instagram_rejected: int = 0
    instagram_cache: InstagramFile | None = None
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
    focus: str = "",
) -> None:
    """Add or update the sources of one AI answer."""
    for item in data.get("sources") or []:
        url = str(item.get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            result.warnings.append(f"invalid URL skipped: {url!r}")
            continue
        if is_blocked(url, blocked) or is_social(url):
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
    for line in data.get("coverage") or []:
        status = str(line.get("status", ""))
        if status not in COVERAGE_STATUS:
            continue
        name = str(line.get("municipality", ""))
        result.coverage.append(
            Coverage(
                municipality=resolve(name) or name,
                focus=focus,
                category=str(line.get("category", "")),
                status=status,
                note=str(line.get("note", "")),
            )
        )


# --- events stage helpers ---------------------------------------------------------------------


MAX_CANDIDATES_PER_RUN = 40
"""Candidate Instagram names checked against their profile per discovery run."""
CANDIDATE_RETRY_DAYS = 180


@dataclass
class Candidate:
    username: str
    name: str
    places: list[str]
    website: str


def collect_candidates(
    data: Mapping[str, Any],
    *,
    batch_places: Sequence[str],
    resolve: Any,
    key: str = "instagram_candidates",
) -> list[Candidate]:
    """The account names of one AI answer under ``key`` (not checked yet)."""
    found = []
    for item in data.get(key) or []:
        if not isinstance(item, Mapping):
            continue
        handle = instagram.normalize(str(item.get("username", "")))
        if not handle:
            continue
        places = [p for p in (resolve(n) for n in item.get("places") or []) if p]
        found.append(
            Candidate(
                username=handle,
                name=str(item.get("name") or ""),
                places=sorted(set(places)) or list(batch_places),
                website=str(item.get("website") or ""),
            )
        )
    return found


def verify_candidates(
    cache: InstagramFile,
    candidates: Sequence[Candidate],
    *,
    names_of: Callable[[str], list[str]],
    today: date,
    env: Mapping[str, str],
    get: instagram.Get,
    api_version: str = "",
    warnings: list[str],
) -> tuple[list[str], list[str]]:
    """Keep the candidates whose profile points to their place; returns the accepted and the
    rejected names.

    A candidate is accepted if it is a business or creator account whose name, biography or
    website names one of its places (``names_of`` gives a municipality's names, its districts
    included) or links the organiser's own website. Others go to ``cache.rejected`` and are
    not checked again for :data:`CANDIDATE_RETRY_DAYS`.
    """
    token, user_id = env.get(instagram.TOKEN_ENV, ""), env.get(instagram.USER_ENV, "")
    retry_after = today - timedelta(days=CANDIDATE_RETRY_DAYS)
    todo: dict[str, Candidate] = {}
    # mined accounts that were never read yet are checked too (e.g. found before this check
    # existed); a rejected one is removed from the accounts
    unread = {
        u: Candidate(username=u, name="", places=list(a.places), website="")
        for u, a in cache.accounts.items()
        if a.origin == "mined" and a.status == "new" and a.last_checked is None
    }
    todo.update(unread)
    for c in candidates:
        if c.username in unread:
            todo[c.username] = c  # the answer's website and name help the check
            continue
        old = cache.rejected.get(c.username)
        if c.username in cache.accounts or c.username in todo:
            continue
        if old is not None and old.checked > retry_after:
            continue
        todo[c.username] = c
    if not todo:
        return [], []
    if not token or not user_id:
        if not candidates:
            return [], []  # only unread accounts: nothing new to report
        warnings.append(
            f"instagram: {len(todo)} candidate account(s) not checked: "
            f"{instagram.TOKEN_ENV} or {instagram.USER_ENV} not set"
        )
        return [], []
    accepted: list[str] = []
    rejected: list[str] = []
    for checked, c in enumerate(todo.values()):
        if checked >= MAX_CANDIDATES_PER_RUN:
            warnings.append("instagram: candidate limit reached; the rest is checked next run")
            break
        try:
            profile = instagram.fetch_profile(
                c.username, user_id=user_id, token=token, api_version=api_version, get=get
            )
        except instagram.AccountError as exc:
            if exc.kind in ("auth", "permission", "rate_limit", "network"):
                warnings.append(f"instagram: candidates not checked further: {exc}")
                break
            cache.rejected[c.username] = RejectedCandidate(
                checked=today, reason=exc.kind, name=c.name
            )
            cache.accounts.pop(c.username, None)
            rejected.append(c.username)
            continue
        names = [n for place in c.places for n in names_of(place)]
        if instagram.belongs_to(profile, names, c.website):
            if c.username not in cache.accounts:
                cache.accounts[c.username] = InstagramAccount(
                    origin="mined", places=c.places, first_seen=today
                )
                accepted.append(c.username)
            cache.rejected.pop(c.username, None)
        else:
            cache.rejected[c.username] = RejectedCandidate(
                checked=today, reason="profile does not name the place", name=c.name
            )
            cache.accounts.pop(c.username, None)
            rejected.append(c.username)
    return accepted, rejected


def is_fresh(source: Source, today: date) -> bool:
    """True if a known source needs no new look: whitelisted, rated or hit recently."""
    recent = today - timedelta(days=RECHECK_SOURCES_DAYS)
    return (
        source.origin is SourceOrigin.WHITELIST
        or (source.last_checked is not None and source.last_checked >= recent)
        or (source.last_hit is not None and source.last_hit >= recent)
    )


def known_sources_for(
    sources: SourcesFile | None,
    municipalities: Iterable[str],
    exclude_urls: Iterable[str] = (),
    limit: int | None = MAX_KNOWN_PER_BATCH,
    skip_prefixes: Iterable[str] = (),
) -> list[Source]:
    """Active, well-rated sources of these municipalities (for the crawl prompt).

    ``skip_prefixes`` are URL entries of the blacklist: a source whose URL starts with
    one of them is not read (e.g. a second front end of the same event database).
    """
    if sources is None:
        return []
    wanted = {m.casefold() for m in municipalities}
    skip = {normalize_url(u) for u in exclude_urls}
    prefixes = [normalize_url(u) for u in skip_prefixes]
    picked = [
        s
        for s in sources.sources
        if s.status is SourceStatus.ACTIVE
        and s.origin is not SourceOrigin.WHITELIST
        and normalize_url(s.url) not in skip
        and not any(normalize_url(s.url).startswith(p) for p in prefixes)
        and {p.casefold() for p in s.places} & wanted
        and ((s.rating or 0) >= MIN_RATING_FOR_CRAWL or s.hits >= 2)
    ]
    picked.sort(key=lambda s: (-(s.rating or 0), -s.hits, s.url))
    return picked if limit is None else picked[:limit]


def record_hits(
    sources: SourcesFile | None,
    events: Sequence[Event],
    municipality_of: Mapping[str, str],
    blocked: Sequence[str],
    today: date,
) -> SourcesFile:
    """Count events per source; unknown primary sources are added as ``origin: crawl``."""
    by_key = {
        normalize_url(s.url): s
        for s in (sources.sources if sources else [])
        if not is_social(s.url)
    }

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
                if is_social(src.url):  # e.g. an Instagram post: no website to count
                    continue
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
    sources: SourcesFile,
    whitelist_urls: Iterable[str],
    candidates: Mapping[str, str],
    today: date,
    coverage: Sequence[Coverage] = (),
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
    if coverage:
        lines += [
            "",
            "## Coverage",
            "",
            "What the discovery passes searched for, per municipality. "
            "`nothing_found` and `not_checked` are the gaps worth a manual look.",
        ]
        mark = {
            "found": "found",
            "nothing_found": "**nothing found**",
            "not_checked": "**not checked**",
        }
        for municipality in dict.fromkeys(c.municipality for c in coverage):
            lines += ["", f"### {municipality}", "", "| Pass | Category | Result | Note |"]
            lines.append("| --- | --- | --- | --- |")
            for c in coverage:
                if c.municipality == municipality:
                    note = c.note.replace("|", "/").replace("\n", " ")
                    lines.append(f"| {c.focus} | {c.category} | {mark[c.status]} | {note} |")
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
    clock: Callable[[], float] = time.monotonic,
    graph_get: instagram.Get | None = None,
    env: Mapping[str, str] | None = None,
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
    names_in: dict[str, list[str]] = {}  # municipality -> its name and its districts' names
    for p in places.places:
        names_in.setdefault(p.municipality or p.name, []).append(p.name)

    def to_municipality(name: str) -> str | None:
        place = index.resolve(name)
        return municipality_of.get(place, place) if place else None

    whitelist = crawl.expand_whitelist(
        crawl._load_list(Path(config.sources.whitelist_file), crawl.WhitelistEntry),
        [g.name for g in crawl.group_places(places.places)],
    )
    blacklist = crawl._load_list(Path(config.sources.blacklist_file), crawl.BlacklistEntry)
    blocked = sorted({e.domain for e in blacklist if e.domain})
    existing = load_sources(data_dir)
    sources = {
        normalize_url(s.url): s
        for s in (existing.sources if existing else [])
        if not is_social(s.url)  # entries added before social media were filtered
    }
    result = DiscoveryResult(sources=existing or SourcesFile(updated_at=datetime.now(UTC)))
    sync_lists(sources, whitelist, blocked, today)
    insta_on = bool(config.sources.instagram_file)
    insta_path = data_dir / "instagram.json"
    if insta_on:
        result.instagram_cache = (
            InstagramFile.model_validate_json(insta_path.read_text(encoding="utf-8"))
            if insta_path.exists()
            else InstagramFile(updated_at=datetime.now(UTC).replace(microsecond=0))
        )

    runner = runner or make_runner(config)
    depth = config.depth
    size = depth.discovery_places_per_request
    batches = [groups[i : i + size] for i in range(0, len(groups), size)]
    focuses: Sequence[Focus | None] = FOCUSES if depth.discovery_focus_passes else (None,)
    candidates: list[Candidate] = []
    calls: list[tuple[int, Any, Focus | str | None]] = []
    for n, batch in enumerate(batches, 1):
        calls += [(n, batch, focus) for focus in focuses]
        if insta_on:
            calls.append((n, batch, LOOKUP))
    budget = config.limits.max_web_searches_per_run
    deadline = clock() + config.limits.max_runtime_minutes * 60
    for position, (number, batch, focus) in enumerate(calls):
        label = f"batch {number}" + (
            " (instagram)"
            if focus == LOOKUP
            else f" ({focus.id})"
            if isinstance(focus, Focus)
            else ""
        )
        remaining = budget - result.usage.web_searches
        if remaining < 3:
            result.warnings.append(f"{label}: skipped, search budget used up")
            continue
        if clock() > deadline:
            result.warnings.append(f"{label}: skipped, max_runtime_minutes reached")
            continue
        names = [g.name for g in batch]
        known = [
            s
            for s in sources.values()
            if {p.casefold() for p in s.places} & {n.casefold() for n in names}
        ]
        recheck = [s for s in known if not is_fresh(s, today)]
        known = [s for s in known if is_fresh(s, today)]
        per_call = max(
            3, min(depth.discovery_searches_per_request, remaining // (len(calls) - position))
        )
        if focus == LOOKUP:
            assert result.instagram_cache is not None
            cache = result.instagram_cache
            retry_after = today - timedelta(days=CANDIDATE_RETRY_DAYS)
            skip = set(cache.accounts) | {
                u for u, r in cache.rejected.items() if r.checked > retry_after
            }
            skip |= {c.username for c in candidates}
            venues = [s for s in known + recheck if s.kind in (SourceKind.VENUE, SourceKind.CLUB)]
            request = AiRequest(
                step=f"discovery {label}, call {position + 1}/{len(calls)}",
                model=config.models.discovery,
                prompt=render_lookup_prompt(config, batch, venues, sorted(skip), per_call),
                schema=lookup_schema(),
                blocked_domains=blocked,
                max_searches=per_call,
            )
            try:
                answer = runner.run(request)
            except AiError as exc:
                result.warnings.append(f"{label}: {exc}")
                continue
            result.usage = result.usage.add(answer.usage)
            found = collect_candidates(answer.data, batch_places=names, resolve=to_municipality)
            candidates += found
            print(
                f"discovery: {label}: {len(found)} candidate(s): "
                + (", ".join(c.username for c in found) or "none")
            )
            note = str(answer.data.get("notes") or "").strip()
            if note:
                print(f"discovery: note from {label}: {note}")
            continue
        assert not isinstance(focus, str)
        request = AiRequest(
            step=f"discovery {label}, call {position + 1}/{len(calls)}",
            model=config.models.discovery,
            prompt=render_prompt(
                config, batch, known, blocked, per_call, focus, insta_on, recheck=recheck
            ),
            schema=answer_schema(with_coverage=focus is not None, with_instagram=insta_on),
            blocked_domains=blocked,
            max_searches=per_call,
        )
        try:
            answer = runner.run(request)
        except AiError as exc:
            result.warnings.append(f"{label}: {exc}")
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
            focus=focus.id if focus else "",
        )
        if result.instagram_cache is not None:
            seen = collect_candidates(
                answer.data, batch_places=names, resolve=to_municipality, key="instagram_accounts"
            ) + collect_candidates(answer.data, batch_places=names, resolve=to_municipality)
            candidates += seen
            if seen:
                found = ", ".join(c.username for c in seen)
                print(f"discovery: {label}: Instagram accounts to check: {found}")
        note = str(answer.data.get("notes") or "").strip()
        if note:
            print(f"discovery: note from {label}: {note}")
    if result.instagram_cache is not None:
        accepted, rejected = verify_candidates(
            result.instagram_cache,
            candidates,
            names_of=lambda m: names_in.get(m, [m]),
            today=today,
            env=os.environ if env is None else env,
            get=graph_get or instagram.http_get,
            api_version=config.instagram.api_version,
            warnings=result.warnings,
        )
        result.instagram_added += accepted
        result.instagram_rejected = len(rejected)
        print(
            f"discovery: {len({c.username for c in candidates})} Instagram candidate(s); "
            f"confirmed by their profile: {', '.join(accepted) or 'none'}; "
            f"rejected: {', '.join(rejected) or 'none'}"
        )
    if calls and not result.usage.requests:
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
        f"{result.usage.web_searches} searches, ~${result.usage.cost_usd:.2f} at API prices"
        + ("; dry run, nothing written" if dry_run else "")
    )
    if result.instagram_cache is not None:
        print(f"discovery: {len(result.instagram_added)} new Instagram account(s)")
    gaps = [c for c in result.coverage if c.status != "found"]
    if result.coverage:
        print(
            f"discovery: coverage: {len(result.coverage) - len(gaps)} categories found, "
            f"{len(gaps)} gaps"
        )
        for c in gaps:
            print(f"  {c.status:13} {c.municipality} / {c.focus} / {c.category}: {c.note}")
    if not dry_run:
        save_sources(result.sources, data_dir)
        if result.instagram_cache is not None and (
            result.instagram_added or result.instagram_rejected
        ):
            cache = result.instagram_cache.model_copy(
                update={"updated_at": datetime.now(UTC).replace(microsecond=0)}
            )
            insta_path.write_text(
                json.dumps(cache.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        state_path = data_dir / "state.json"
        state = (
            State.model_validate_json(state_path.read_text(encoding="utf-8"))
            if state_path.exists()
            else State()
        )
        state.last_usage["discovery"] = result.usage
        state_path.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")
        (data_dir / "suggested.md").write_text(
            suggestions_markdown(
                result.sources,
                [w.url for w in whitelist],
                result.blacklist_candidates,
                today,
                result.coverage,
            ),
            encoding="utf-8",
        )
    return result
