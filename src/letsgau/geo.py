"""Geo stage: the configured municipalities and their places (OpenStreetMap, Overpass).

The area is a list of municipalities (``region.municipalities``), each given by name and/or
postcode, e.g. ``Schongau 86956`` and ``Altenstadt 86972``:

1. Fingerprint = hash of the municipality list. If it matches ``data/places.json``, nothing
   is done; a change sets ``force_discovery`` in ``state.json``.
2. For each municipality, Overpass returns the place nodes (town, village, hamlet, suburb,
   ...) inside its boundary, with the municipality each one lies in:
   - with a postcode: the places in that postcode area (``boundary=postal_code``), kept if
     they lie in a municipality of the given name (with only a postcode: all of them);
   - with only a name: the places in the municipality boundary (``admin_level=8``) of that
     name; several municipalities of that name are an error (add the postcode).
3. ``data/places.json`` lists the places in the order of the config, each municipality's
   main place (the one with its name) first. Districts (Ortsteile) map event places such as
   "Schwabniederhofen" to their municipality.

No API key is needed. ``OVERPASS_URL`` may list Overpass instances separated by commas,
which are tried in order.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from letsgau import __version__
from letsgau.config import Config, Municipality, RegionConfig
from letsgau.models import Place, PlaceOrigin, PlacesFile, State
from letsgau.util import DATA_DIR, load_json, logger, write_json

OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)
"""Public Overpass instances, tried in order until one answers."""
USER_AGENT = f"letsgau/{__version__} (+https://github.com/mat-los/letsgau)"
PLACE_TYPES = ("city", "town", "village", "hamlet", "suburb", "quarter", "neighbourhood")
"""OSM place types taken as places (and districts) of a municipality."""


Json = dict[str, Any]


class GeoError(Exception):
    """Raised for failed or invalid Overpass responses and ambiguous municipalities."""


class Http(Protocol):
    """Minimal HTTP interface so tests can replay recorded responses."""

    def post_form(self, url: str, data: dict[str, str]) -> Json: ...


class UrllibHttp:
    """Standard-library HTTP client with a small retry for 429/502/503/504."""

    RETRY_STATUS = frozenset({429, 502, 503, 504})

    def __init__(
        self,
        retries: int = 2,
        timeout: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.retries = retries
        self.timeout = timeout
        self.sleep = sleep

    def post_form(self, url: str, data: dict[str, str]) -> Json:
        payload = urllib.parse.urlencode(data).encode("utf-8")
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        return self._request(url, payload, headers)

    def _request(self, url: str, payload: bytes, headers: dict[str, str]) -> Json:
        headers = {"User-Agent": USER_AGENT, **headers}
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            started = time.monotonic()
            _log(f"POST {_redact(url)} ({len(payload)} B, attempt {attempt + 1})")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = response.read()
                    _log(f"  -> {response.status}, {len(body)} B in {_since(started)}")
                    return json.loads(body.decode("utf-8"))
            except urllib.error.HTTPError as exc:
                _log(f"  -> HTTP {exc.code} in {_since(started)}")
                if exc.code in self.RETRY_STATUS and attempt < self.retries:
                    self.sleep(5 * 2**attempt)
                    continue
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                raise GeoError(f"POST {_redact(url)} failed: HTTP {exc.code}: {detail}") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                _log(f"  -> {type(exc).__name__}: {exc} after {_since(started)}")
                if attempt < self.retries:
                    self.sleep(5 * 2**attempt)
                    continue
                raise GeoError(f"POST {_redact(url)} failed: {exc}") from exc
            except json.JSONDecodeError as exc:
                raise GeoError(f"POST {_redact(url)} returned invalid JSON") from exc
        raise GeoError(f"POST {_redact(url)} failed")  # pragma: no cover


def _redact(url: str) -> str:
    return url.split("?", 1)[0]


def _since(started: float) -> str:
    return f"{time.monotonic() - started:.1f}s"


# --- fingerprint -------------------------------------------------------------


def _key(m: Municipality) -> str:
    return " ".join(x for x in (m.name.strip(), m.postcode) if x)


def _fingerprint_key(m: Municipality) -> str:
    """Like :func:`_key`, plus the separate districts (absent ones keep old fingerprints)."""
    return _key(m) + (f" | {', '.join(m.separate)}" if m.separate else "")


def fingerprint(region: RegionConfig) -> str:
    """Stable hash of the municipality list; a change triggers geo + discovery."""
    payload = [_fingerprint_key(m) for m in region.municipalities]
    digest = hashlib.sha256(json.dumps(payload).encode()).hexdigest()
    return f"sha256:{digest[:16]}"


# --- Overpass ----------------------------------------------------------------


@dataclass
class Clients:
    """Everything that talks to the network; replaced by fakes in tests."""

    http: Http
    overpass_urls: tuple[str, ...] = OVERPASS_URLS
    # Public Overpass instances are often all at capacity for a few minutes; when every
    # instance fails, wait and go through the list again (60 s, then 120 s).
    overpass_rounds: int = 3
    sleep: Callable[[float], None] = time.sleep

    @classmethod
    def from_env(cls, http: Http | None = None) -> Clients:
        return cls(
            http=http or UrllibHttp(),
            overpass_urls=_url_list(os.environ.get("OVERPASS_URL", "")) or OVERPASS_URLS,
        )

    def overpass(self, query: str) -> list[Json]:
        """Run a query on the first Overpass instance that answers, in up to ``overpass_rounds``."""
        errors: list[str] = []
        for round_ in range(self.overpass_rounds):
            if round_:
                wait = 60 * 2 ** (round_ - 1)
                _log(f"all Overpass instances failed, round {round_ + 1} in {wait} s")
                self.sleep(wait)
            errors = []
            for url in self.overpass_urls:
                try:
                    response = self.http.post_form(url, {"data": query})
                except GeoError as exc:
                    errors.append(str(exc))
                    _log(f"Overpass instance failed, trying the next one: {exc}")
                    continue
                if "elements" in response:
                    return response["elements"]
                errors.append(f"{url}: no elements: {response.get('remark', '')}")
        raise GeoError(
            f"all Overpass instances failed ({self.overpass_rounds} rounds):\n  "
            + "\n  ".join(errors)
        )


def _url_list(value: str) -> tuple[str, ...]:
    return tuple(u.strip() for u in value.split(",") if u.strip())


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


MUNICIPALITY_FILTER = '["boundary"="administrative"]["admin_level"="8"]["name"]'
"""OSM municipalities (in Germany: Gemeinde, Markt, Stadt)."""

_TYPES = "^(" + "|".join(PLACE_TYPES) + ")$"

_WITH_MUNICIPALITY = (
    "foreach.places->.p(\n"
    "  .p out;\n"
    "  .p is_in->.a;\n"
    f"  area.a{MUNICIPALITY_FILTER};\n"
    "  out tags;\n"
    ");"
)
"""Outputs each place node followed by the municipality area(s) it lies in."""


def municipality_query(m: Municipality) -> str:
    """Overpass QL: the place nodes of one municipality, each followed by its municipality.

    With only a name, the municipality areas of that name are output first (``out ids``),
    so an ambiguous name can be detected.
    """
    head = "[out:json][timeout:180];\n"
    if m.postcode:
        scope = f'area["boundary"="postal_code"]["postal_code"={_quote(m.postcode)}]->.scope;\n'
    else:
        scope = (
            f'area["boundary"="administrative"]["admin_level"="8"]["name"={_quote(m.name.strip())}]'
            "->.scope;\n.scope out ids;\n"
        )
    places = f'node(area.scope)["place"~"{_TYPES}"]["name"]->.places;\n'
    return head + scope + places + _WITH_MUNICIPALITY


def parse_places(elements: Sequence[Json]) -> tuple[list[Place], int]:
    """Place nodes from an Overpass answer, and the number of leading municipality areas.

    An ``area`` after a node is the municipality that node lies in; areas before the first
    node are the scope areas of a name-only query.
    """
    places: list[Place] = []
    scopes = 0
    for element in elements:
        kind = element.get("type")
        tags = element.get("tags", {})
        if kind == "node" and "name" in tags:
            places.append(_to_place(element))
        elif kind == "area":
            if not places:
                scopes += 1
            elif places[-1].municipality is None and "name" in tags:
                places[-1].municipality = tags["name"]
    return places, scopes


def _to_place(element: Json) -> Place:
    tags = element.get("tags", {})
    return Place(
        name=tags["name"],
        osm_id=f"{element.get('type', 'node')}/{element['id']}",
        place_type=tags.get("place", "unknown"),
        lat=element["lat"],
        lon=element["lon"],
        origin=PlaceOrigin.MUNICIPALITY,
    )


def municipality_places(clients: Clients, m: Municipality) -> list[Place]:
    """The places of one configured municipality, its main place first."""
    places, scopes = parse_places(clients.overpass(municipality_query(m)))
    name = m.name.strip().casefold()
    if not m.postcode and scopes > 1:
        raise GeoError(
            f"{m.name!r}: {scopes} municipalities of this name in OpenStreetMap; "
            "add its postcode in region.municipalities"
        )
    if name:
        places = [p for p in places if (p.municipality or "").casefold() == name]
    places = [p for p in places if p.municipality]
    places.sort(key=lambda p: (p.name.casefold() != (p.municipality or "").casefold(), p.name))
    # separate districts become an area of their own (after sorting: they keep their position)
    separate = {s.strip().casefold() for s in m.separate}
    return [
        p.model_copy(update={"municipality": p.name}) if p.name.casefold() in separate else p
        for p in places
    ]


# --- stage --------------------------------------------------------------------


@dataclass
class GeoResult:
    """What happened in a geo run."""

    recomputed: bool
    fingerprint_changed: bool
    places: PlacesFile | None
    warnings: list[str]


def compute(
    config: Config,
    clients: Clients | None = None,
    *,
    data_dir: Path = DATA_DIR,
    dry_run: bool = False,
    force: bool = False,
    now: datetime | None = None,
) -> GeoResult:
    """Recompute the places if the municipality list changed; write ``places.json``.

    A run on a file from the drive-time era recomputes once; discovery is forced only if
    the set of municipalities differs from the stored one.
    """
    region = config.region
    fp = fingerprint(region)
    places_path = data_dir / "places.json"
    state_path = data_dir / "state.json"

    previous = load_json(places_path, PlacesFile, GeoError)
    state = load_json(state_path, State, GeoError) or State()
    if previous is not None and previous.fingerprint == fp and not force:
        return GeoResult(False, False, previous, [])

    clients = clients or Clients.from_env()
    warnings: list[str] = []
    places: list[Place] = []
    seen: set[str] = set()
    for m in region.municipalities:
        found = municipality_places(clients, m)
        if not found:
            warnings.append(f"{_key(m)!r}: no places found in OpenStreetMap")
        names = {p.name.casefold() for p in found}
        for district in m.separate:
            if district.strip().casefold() not in names:
                warnings.append(f"{_key(m)!r}: separate district {district!r} not found")
        for place in found:
            if place.osm_id not in seen:
                seen.add(place.osm_id)
                places.append(place)
    if not places:
        raise GeoError("no places found for any municipality in region.municipalities")

    before = {p.municipality or p.name for p in previous.places} if previous else set()
    after = {p.municipality or p.name for p in places}
    changed = before != after

    now = now or datetime.now(UTC).replace(microsecond=0)
    result = PlacesFile(
        fingerprint=fp,
        generated_at=now,
        municipalities=[_key(m) for m in region.municipalities],
        places=places,
    )
    if not dry_run:
        write_json(places_path, result.model_dump(mode="json", exclude_none=True))
        state.geo_fingerprint = fp
        if changed:
            state.force_discovery = True
        state.last_run["geo"] = now
        write_json(state_path, state.model_dump(mode="json"))
    return GeoResult(True, changed, result, warnings)


def run(
    config: Config,
    *,
    dry_run: bool = False,
    limit_places: int | None = None,
    force: bool = False,
    clients: Clients | None = None,
) -> GeoResult:
    """CLI entry point. ``limit_places`` is ignored: the place list is always complete."""
    result = compute(config, clients, dry_run=dry_run, force=force)
    assert result.places is not None
    if not result.recomputed:
        print(
            f"geo: unchanged ({result.places.fingerprint}), "
            f"{len(result.places.places)} places, skipped"
        )
        return result
    for warning in result.warnings:
        print(f"warning: geo: {warning}", file=sys.stderr)
    municipalities: dict[str, list[str]] = {}
    for place in result.places.places:
        municipalities.setdefault(place.municipality or place.name, []).append(place.name)
    print(
        f"geo: {len(municipalities)} municipalities, {len(result.places.places)} places "
        f"({result.places.fingerprint})"
        + ("; discovery will be forced" if result.fingerprint_changed else "")
        + ("; dry run, nothing written" if dry_run else "")
    )
    for name, names in municipalities.items():
        print(f"  {name}: {', '.join(names)}")
    return result


_log = logger("geo")
