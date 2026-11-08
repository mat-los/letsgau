"""Geo stage: isochrone (OpenRouteService), places in the area (Overpass), drive times.

Steps (see docs/CONCEPT.md, "Pipeline", step 2):

1. Fingerprint = hash of ``region.center`` and ``region.drive_time_minutes``. If it
   matches ``data/places.json`` and the place filters (``place_types``,
   ``include_places``, ``exclude_places``) are unchanged, nothing is done.
2. A changed fingerprint fetches a new isochrone (ORS, profile ``driving-car``) and
   stores it as ``geo/isochrone.geojson``; ``state.json`` gets ``force_discovery``.
   If only the filters changed, the stored isochrone is reused.
3. Overpass returns all ``place`` nodes of the configured types in the isochrone's
   bounding box; a point-in-polygon test keeps those inside the isochrone.
   ``include_places`` are looked up by name nearby, ``exclude_places`` removed.
4. ORS matrix gives the drive time from the center to every place.

OpenRouteService is hosted by HeiGIT. The old host ``api.openrouteservice.org`` was
deprecated in 2026 in favour of ``api.heigit.org/openrouteservice``; the key is sent
in the ``Authorization`` header. Both can be overridden with the ``ORS_BASE_URL``
and ``OVERPASS_URL`` environment variables; ``OVERPASS_URL`` may list several
instances separated by commas, which are tried in order.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from letsgau import __version__
from letsgau.config import Config, RegionConfig
from letsgau.models import Place, PlaceOrigin, PlacesFile, State

ORS_BASE_URL = "https://api.heigit.org/openrouteservice"
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)
"""Public Overpass instances, tried in order until one answers."""
ORS_PROFILE = "driving-car"
USER_AGENT = f"letsgau/{__version__} (+https://github.com/mat-los/letsgau)"

MATRIX_CHUNK = 500
"""Destinations per ORS matrix request (one source each), well below plan limits."""
INCLUDE_SEARCH_MIN_KM = 50.0
"""Minimum radius around the center in which ``include_places`` are looked up."""

DATA_DIR = Path("data")
GEO_DIR = Path("geo")

Json = dict[str, Any]


class GeoError(Exception):
    """Raised for missing credentials or failed/invalid API responses."""


class Http(Protocol):
    """Minimal HTTP interface so tests can replay recorded responses."""

    def post_json(self, url: str, body: Json, headers: dict[str, str]) -> Json: ...

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

    def post_json(self, url: str, body: Json, headers: dict[str, str]) -> Json:
        all_headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json, application/geo+json",
            **headers,
        }
        return self._request(url, json.dumps(body).encode("utf-8"), all_headers)

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


def _log(message: str) -> None:
    print(f"geo: {message}", file=sys.stderr, flush=True)


# --- fingerprint -------------------------------------------------------------


def fingerprint(region: RegionConfig) -> str:
    """Stable hash of center and drive time; a change triggers geo + discovery."""
    payload = {
        "lat": round(region.center.lat, 6),
        "lon": round(region.center.lon, 6),
        "drive_time_minutes": region.drive_time_minutes,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return f"sha256:{digest[:16]}"


def _filters_unchanged(region: RegionConfig, places: PlacesFile) -> bool:
    return (
        places.place_types == list(region.place_types)
        and places.include_places == list(region.include_places)
        and places.exclude_places == list(region.exclude_places)
    )


# --- geometry ----------------------------------------------------------------

Ring = Sequence[Sequence[float]]  # [[lon, lat], ...]


def _polygons(geometry: Json) -> list[list[Ring]]:
    """Return a list of polygons (each a list of rings) from a GeoJSON geometry."""
    kind = geometry.get("type")
    if kind == "Polygon":
        return [geometry["coordinates"]]
    if kind == "MultiPolygon":
        return list(geometry["coordinates"])
    raise GeoError(f"unsupported isochrone geometry type: {kind!r}")


def isochrone_polygons(isochrone: Json) -> list[list[Ring]]:
    """All polygons of all features of an ORS isochrone FeatureCollection."""
    features = isochrone.get("features") or []
    if not features:
        raise GeoError("isochrone response contains no features")
    result: list[list[Ring]] = []
    for feature in features:
        result.extend(_polygons(feature["geometry"]))
    return result


def _in_ring(lon: float, lat: float, ring: Ring) -> bool:
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        if (y1 > lat) != (y2 > lat):
            x_cross = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < x_cross:
                inside = not inside
    return inside


def point_in_polygons(lon: float, lat: float, polygons: Iterable[list[Ring]]) -> bool:
    """True if the point lies in any polygon (outer ring minus holes)."""
    for rings in polygons:
        if not rings or not _in_ring(lon, lat, rings[0]):
            continue
        if not any(_in_ring(lon, lat, hole) for hole in rings[1:]):
            return True
    return False


def bbox(polygons: Iterable[list[Ring]]) -> tuple[float, float, float, float]:
    """(south, west, north, east) of all outer rings."""
    lons: list[float] = []
    lats: list[float] = []
    for rings in polygons:
        lons.extend(p[0] for p in rings[0])
        lats.extend(p[1] for p in rings[0])
    return min(lats), min(lons), max(lats), max(lons)


def _bbox_around(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    dlat = radius_km / 111.32
    dlon = radius_km / (111.32 * max(math.cos(math.radians(lat)), 0.01))
    return lat - dlat, lon - dlon, lat + dlat, lon + dlon


# --- API calls ----------------------------------------------------------------


@dataclass
class Clients:
    """Everything that talks to the network; replaced by fakes in tests."""

    http: Http
    ors_api_key: str
    ors_base_url: str = ORS_BASE_URL
    overpass_urls: tuple[str, ...] = OVERPASS_URLS

    @classmethod
    def from_env(cls, http: Http | None = None) -> Clients:
        key = os.environ.get("ORS_API_KEY", "").strip()
        if not key:
            raise GeoError(
                "ORS_API_KEY is not set; create a free key at https://account.heigit.org "
                "and put it into .env (local) or the repository secrets (GitHub)"
            )
        return cls(
            http=http or UrllibHttp(),
            ors_api_key=key,
            ors_base_url=os.environ.get("ORS_BASE_URL", ORS_BASE_URL).rstrip("/"),
            overpass_urls=_url_list(os.environ.get("OVERPASS_URL", "")) or OVERPASS_URLS,
        )

    def _ors(self, endpoint: str, body: Json) -> Json:
        url = f"{self.ors_base_url}/v2/{endpoint}/{ORS_PROFILE}"
        response = self.http.post_json(url, body, {"Authorization": self.ors_api_key})
        if "error" in response:
            raise GeoError(f"ORS {endpoint} error: {response['error']}")
        return response

    def isochrone(self, lat: float, lon: float, minutes: int) -> Json:
        """Area reachable by car within ``minutes`` (GeoJSON FeatureCollection)."""
        body = {"locations": [[lon, lat]], "range": [minutes * 60], "range_type": "time"}
        return self._ors("isochrones", body)

    def drive_times(
        self, center: tuple[float, float], points: Sequence[tuple[float, float]]
    ) -> list[tuple[float | None, float | None]]:
        """(seconds, metres) from ``center`` to each point; points are (lat, lon)."""
        result: list[tuple[float | None, float | None]] = []
        for start in range(0, len(points), MATRIX_CHUNK):
            chunk = points[start : start + MATRIX_CHUNK]
            locations = [[center[1], center[0]]] + [[lon, lat] for lat, lon in chunk]
            body = {
                "locations": locations,
                "sources": [0],
                "destinations": list(range(1, len(locations))),
                "metrics": ["duration", "distance"],
                "units": "m",
            }
            response = self._ors("matrix", body)
            try:
                durations = response["durations"][0]
                distances = response.get("distances", [[None] * len(chunk)])[0]
            except (KeyError, IndexError, TypeError) as exc:
                raise GeoError("ORS matrix response has no durations") from exc
            if len(durations) != len(chunk):
                raise GeoError(
                    f"ORS matrix returned {len(durations)} durations for {len(chunk)} places"
                )
            result.extend(zip(durations, distances, strict=True))
        return result

    def overpass(self, query: str) -> list[Json]:
        """Run a query on the first Overpass instance that answers."""
        errors: list[str] = []
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
        raise GeoError("all Overpass instances failed:\n  " + "\n  ".join(errors))


def _url_list(value: str) -> tuple[str, ...]:
    return tuple(u.strip() for u in value.split(",") if u.strip())


def _type_regex(place_types: Sequence[str]) -> str:
    return "^(" + "|".join(place_types) + ")$"


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def area_query(place_types: Sequence[str], box: tuple[float, float, float, float]) -> str:
    """Overpass QL: all place nodes of the given types in a bounding box."""
    s, w, n, e = box
    return (
        f"[out:json][timeout:90];\n"
        f'node["place"~"{_type_regex(place_types)}"]["name"]({s:.5f},{w:.5f},{n:.5f},{e:.5f});\n'
        f"out;"
    )


def include_query(names: Sequence[str], box: tuple[float, float, float, float]) -> str:
    """Overpass QL: place nodes (any type) with one of the given names in a bounding box."""
    s, w, n, e = box
    lines = "".join(
        f'  node["place"]["name"={_quote(name)}]({s:.5f},{w:.5f},{n:.5f},{e:.5f});\n'
        for name in names
    )
    return f"[out:json][timeout:90];\n(\n{lines});\nout;"


def _to_place(element: Json, origin: PlaceOrigin) -> Place:
    tags = element.get("tags", {})
    return Place(
        name=tags["name"],
        osm_id=f"{element.get('type', 'node')}/{element['id']}",
        place_type=tags.get("place", "unknown"),
        lat=element["lat"],
        lon=element["lon"],
        origin=origin,
    )


def _distance2(place: Place, lat: float, lon: float) -> float:
    return (place.lat - lat) ** 2 + (place.lon - lon) ** 2


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
    geo_dir: Path = GEO_DIR,
    dry_run: bool = False,
    force: bool = False,
    now: datetime | None = None,
) -> GeoResult:
    """Recompute places if needed and write ``places.json``, ``state.json`` and the isochrone.

    ``clients`` defaults to :meth:`Clients.from_env` and is only created when
    something has to be recomputed, so an unchanged run needs no API key.
    """
    region = config.region
    fp = fingerprint(region)
    places_path = data_dir / "places.json"
    state_path = data_dir / "state.json"
    iso_path = geo_dir / "isochrone.geojson"

    previous = _load(places_path, PlacesFile)
    state = _load(state_path, State) or State()
    fingerprint_changed = previous is None or previous.fingerprint != fp
    need_isochrone = force or fingerprint_changed or not iso_path.exists()

    if not need_isochrone and _filters_unchanged(region, previous):
        return GeoResult(False, False, previous, [])

    clients = clients or Clients.from_env()
    warnings: list[str] = []
    lat, lon = region.center.lat, region.center.lon

    if need_isochrone:
        isochrone = clients.isochrone(lat, lon, region.drive_time_minutes)
    else:
        isochrone = json.loads(iso_path.read_text(encoding="utf-8"))
    polygons = isochrone_polygons(isochrone)

    # Places inside the isochrone.
    found: dict[str, Place] = {}
    for element in clients.overpass(area_query(region.place_types, bbox(polygons))):
        if "name" not in element.get("tags", {}):
            continue
        place = _to_place(element, PlaceOrigin.AREA)
        if place.place_type in region.place_types and point_in_polygons(
            place.lon, place.lat, polygons
        ):
            found.setdefault(place.name.casefold(), place)

    # include_places: look up by name near the center, closest match wins.
    missing = [n for n in region.include_places if n.casefold() not in found]
    if missing:
        radius = max(INCLUDE_SEARCH_MIN_KM, region.drive_time_minutes * 2.0)
        candidates: dict[str, Place] = {}
        for element in clients.overpass(include_query(missing, _bbox_around(lat, lon, radius))):
            place = _to_place(element, PlaceOrigin.INCLUDE)
            key = place.name.casefold()
            if key not in candidates or _distance2(place, lat, lon) < _distance2(
                candidates[key], lat, lon
            ):
                candidates[key] = place
        for name in missing:
            if name.casefold() in candidates:
                found[name.casefold()] = candidates[name.casefold()]
            else:
                warnings.append(
                    f"include_places: {name!r} not found in OpenStreetMap within {radius:.0f} km"
                )

    # exclude_places.
    for name in region.exclude_places:
        if found.pop(name.casefold(), None) is None:
            warnings.append(f"exclude_places: {name!r} is not in the place list")

    # Drive times.
    places = list(found.values())
    times = clients.drive_times((lat, lon), [(p.lat, p.lon) for p in places]) if places else []
    for place, (seconds, metres) in zip(places, times, strict=True):
        place.drive_time_minutes = None if seconds is None else round(seconds / 60, 1)
        place.distance_km = None if metres is None else round(metres / 1000, 1)
        if seconds is None:
            warnings.append(f"no drive time for {place.name!r} (not routable)")
    places.sort(key=lambda p: (p.drive_time_minutes is None, p.drive_time_minutes or 0, p.name))

    now = now or datetime.now(UTC).replace(microsecond=0)
    result = PlacesFile(
        fingerprint=fp,
        generated_at=now,
        center={"lat": lat, "lon": lon},
        drive_time_minutes=region.drive_time_minutes,
        place_types=list(region.place_types),
        include_places=list(region.include_places),
        exclude_places=list(region.exclude_places),
        places=places,
    )

    if not dry_run:
        if need_isochrone:
            _write_json(iso_path, isochrone)
        _write_json(places_path, result.model_dump(mode="json"))
        state.geo_fingerprint = fp
        if fingerprint_changed:
            state.force_discovery = True
        state.last_run["geo"] = now
        _write_json(state_path, state.model_dump(mode="json"))

    return GeoResult(True, fingerprint_changed, result, warnings)


def _load[T: (PlacesFile, State)](path: Path, model: type[T]) -> T | None:
    if not path.exists():
        return None
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise GeoError(f"{path} is invalid: {exc}") from exc


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(
    config: Config,
    *,
    dry_run: bool = False,
    limit_places: int | None = None,
    force: bool = False,
    clients: Clients | None = None,
) -> GeoResult:
    """CLI entry point. ``limit_places`` is ignored: the place list is always complete."""
    region = config.region
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
    print(
        f"geo: {len(result.places.places)} places within {region.drive_time_minutes} min "
        f"of {region.label} ({result.places.fingerprint})"
        + ("; discovery will be forced" if result.fingerprint_changed else "")
        + ("; dry run, nothing written" if dry_run else "")
    )
    for place in result.places.places:
        minutes = "?" if place.drive_time_minutes is None else f"{place.drive_time_minutes:g}"
        print(f"  {minutes:>5} min  {place.name} ({place.place_type}, {place.origin})")
    return result
