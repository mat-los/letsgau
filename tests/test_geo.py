"""Tests for the geo stage. ORS and Overpass responses are replayed from tests/fixtures/geo."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from letsgau import geo
from letsgau.cli import load_dotenv, main
from letsgau.config import Config, load_config
from letsgau.models import PlaceOrigin, PlacesFile, State

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "geo"
CONFIG_PATH = REPO_ROOT / "instance" / "config.yaml"
NOW = datetime(2026, 11, 8, 4, 0, tzinfo=UTC)
KEY = "test-ors-key"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeHttp:
    """Replays recorded responses and records every request."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, dict[str, str]]] = []
        recorded = _fixture("ors_matrix_schongau.json")
        locations = recorded["request"]["locations"]
        response = recorded["response"]
        # Destination index i in the response belongs to locations[i + 1].
        self.matrix = {
            tuple(loc): (response["durations"][0][i], response["distances"][0][i])
            for i, loc in enumerate(locations[1:])
        }
        self.error: dict[str, Any] | None = None

    def post_json(self, url: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        self.calls.append((url, body, headers))
        if self.error is not None:
            return self.error
        if url.endswith("/v2/isochrones/driving-car"):
            return _fixture("ors_isochrone_schongau_30min.json")
        if url.endswith("/v2/matrix/driving-car"):
            dests = [body["locations"][i] for i in body["destinations"]]
            rows = [self.matrix.get(tuple(loc), (None, None)) for loc in dests]
            return {
                "durations": [[r[0] for r in rows]],
                "distances": [[r[1] for r in rows]],
                "metadata": {"service": "matrix"},
            }
        raise AssertionError(f"unexpected URL {url}")

    def post_form(self, url: str, data: dict[str, str]) -> dict[str, Any]:
        self.calls.append((url, data, {}))
        query = data["data"]
        if '"name"=' in query:
            response = _fixture("overpass_include.json")
            response["elements"] = [
                e for e in response["elements"] if f'"name"="{e["tags"]["name"]}"' in query
            ]
            return response
        return _fixture("overpass_area_schongau.json")

    def urls(self) -> list[str]:
        return [url for url, _, _ in self.calls]


@pytest.fixture
def http() -> FakeHttp:
    return FakeHttp()


@pytest.fixture
def clients(http: FakeHttp) -> geo.Clients:
    return geo.Clients(http=http, ors_api_key=KEY)


def make_config(**region: Any) -> Config:
    """Instance config with the Phase 1 acceptance setup: Schongau, 30 minutes."""
    config = load_config(CONFIG_PATH)
    values = {"drive_time_minutes": 30, "include_places": [], "exclude_places": [], **region}
    return config.model_copy(update={"region": config.region.model_copy(update=values)})


def run_geo(
    config: Config, clients: geo.Clients | None, tmp_path: Path, **kw: Any
) -> geo.GeoResult:
    return geo.compute(
        config, clients, data_dir=tmp_path / "data", geo_dir=tmp_path / "geo", now=NOW, **kw
    )


def names(result: geo.GeoResult) -> list[str]:
    assert result.places is not None
    return [p.name for p in result.places.places]


# --- fingerprint and geometry -------------------------------------------------


def test_fingerprint_depends_on_center_and_drive_time_only() -> None:
    base = make_config().region
    fp = geo.fingerprint(base)
    assert fp.startswith("sha256:")
    assert geo.fingerprint(make_config().region) == fp
    assert geo.fingerprint(base.model_copy(update={"drive_time_minutes": 31})) != fp
    moved = base.model_copy(update={"center": base.center.model_copy(update={"lat": 47.7})})
    assert geo.fingerprint(moved) != fp
    assert geo.fingerprint(base.model_copy(update={"include_places": ["Weilheim"]})) == fp


def test_point_in_polygon_with_hole_and_multipolygon() -> None:
    outer = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
    hole = [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]
    other = [[20, 20], [22, 20], [22, 22], [20, 22], [20, 20]]
    polygons = geo._polygons({"type": "MultiPolygon", "coordinates": [[outer, hole], [other]]})
    assert geo.point_in_polygons(1, 1, polygons)
    assert not geo.point_in_polygons(5, 5, polygons)  # in the hole
    assert geo.point_in_polygons(21, 21, polygons)
    assert not geo.point_in_polygons(15, 15, polygons)
    assert geo.bbox(polygons) == (0, 0, 22, 22)


def test_area_query_lists_place_types_and_bbox() -> None:
    query = geo.area_query(["city", "town", "village"], (47.65, 10.69, 47.97, 11.1))
    assert 'node["place"~"^(city|town|village)$"]["name"]' in query
    assert "(47.65000,10.69000,47.97000,11.10000)" in query
    assert query.startswith("[out:json]")


def test_include_query_escapes_quotes() -> None:
    query = geo.include_query(['Sankt "X"'], (1, 2, 3, 4))
    assert '["name"="Sankt \\"X\\""]' in query


# --- acceptance #4: plausible place list -------------------------------------


def test_schongau_30_minutes_writes_places_json(
    clients: geo.Clients, http: FakeHttp, tmp_path: Path
) -> None:
    result = run_geo(make_config(), clients, tmp_path)

    assert result.recomputed and result.fingerprint_changed
    assert result.warnings == []

    # Requests go to the current HeiGIT host with the key in the Authorization header.
    iso_url, iso_body, iso_headers = http.calls[0]
    assert iso_url == "https://api.heigit.org/openrouteservice/v2/isochrones/driving-car"
    assert iso_headers == {"Authorization": KEY}
    assert iso_body == {"locations": [[10.896, 47.811]], "range": [1800], "range_type": "time"}
    assert http.urls()[1] == geo.OVERPASS_URLS[0]
    matrix_url, matrix_body, _ = http.calls[2]
    assert matrix_url.endswith("/v2/matrix/driving-car")
    assert matrix_body["locations"][0] == [10.896, 47.811]
    assert matrix_body["sources"] == [0]
    assert len(http.calls) == 3

    places_file = PlacesFile.model_validate_json(
        (tmp_path / "data" / "places.json").read_text(encoding="utf-8")
    )
    assert places_file == result.places
    assert places_file.fingerprint == geo.fingerprint(make_config().region)
    assert places_file.generated_at == NOW
    listed = names(result)
    assert listed[0] == "Schongau"
    assert {"Peiting", "Altenstadt", "Peißenberg", "Steingaden"} <= set(listed)
    # Inside the bounding box but outside the isochrone (Prem: 30.2 min, Raisting: 40 min).
    assert "Prem" not in listed
    assert "Raisting" not in listed
    assert "Weilheim in Oberbayern" in listed  # 29.5 min
    assert len(listed) == 19
    times = [p.drive_time_minutes for p in places_file.places]
    assert all(t is not None and 0 < t <= 30 for t in times)
    assert times == sorted(times)
    peiting = next(p for p in places_file.places if p.name == "Peiting")
    assert (peiting.drive_time_minutes, peiting.distance_km, peiting.osm_id) == (
        9.6,
        4.2,
        "node/240040702",
    )
    assert all(p.origin is PlaceOrigin.AREA for p in places_file.places)

    iso = json.loads((tmp_path / "geo" / "isochrone.geojson").read_text(encoding="utf-8"))
    assert iso["type"] == "FeatureCollection"
    state = State.model_validate_json((tmp_path / "data" / "state.json").read_text("utf-8"))
    assert state.geo_fingerprint == places_file.fingerprint
    assert state.force_discovery is True
    assert state.last_run["geo"] == NOW


# --- acceptance #5: recompute only on change ---------------------------------


def test_second_run_without_change_does_nothing_and_needs_no_key(
    clients: geo.Clients, http: FakeHttp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_geo(make_config(), clients, tmp_path)
    before = (tmp_path / "data" / "places.json").read_text(encoding="utf-8")
    calls = len(http.calls)

    monkeypatch.delenv("ORS_API_KEY", raising=False)
    result = run_geo(make_config(), None, tmp_path)

    assert not result.recomputed
    assert len(http.calls) == calls
    assert (tmp_path / "data" / "places.json").read_text(encoding="utf-8") == before


@pytest.mark.parametrize(
    "change",
    [{"drive_time_minutes": 25}, {"center": {"lat": 47.80, "lon": 10.90}}],
    ids=["drive_time", "center"],
)
def test_changing_drive_time_or_center_recomputes(
    change: dict[str, Any], clients: geo.Clients, http: FakeHttp, tmp_path: Path
) -> None:
    run_geo(make_config(), clients, tmp_path)
    state_path = tmp_path / "data" / "state.json"
    state = State.model_validate_json(state_path.read_text("utf-8"))
    state.force_discovery = False  # as if discovery had run
    state_path.write_text(state.model_dump_json(), encoding="utf-8")
    http.calls.clear()

    config = make_config()
    if "center" in change:
        center = config.region.center.model_copy(update=change["center"])
        change = {"center": center}
    result = run_geo(make_config(**change), clients, tmp_path)

    assert result.recomputed and result.fingerprint_changed
    assert http.urls()[0].endswith("/v2/isochrones/driving-car")
    new_state = State.model_validate_json(state_path.read_text("utf-8"))
    assert new_state.force_discovery is True
    assert new_state.geo_fingerprint == geo.fingerprint(make_config(**change).region)


def test_force_recomputes(clients: geo.Clients, http: FakeHttp, tmp_path: Path) -> None:
    run_geo(make_config(), clients, tmp_path)
    http.calls.clear()
    result = run_geo(make_config(), clients, tmp_path, force=True)
    assert result.recomputed
    assert not result.fingerprint_changed
    assert http.urls()[0].endswith("/isochrones/driving-car")


def test_missing_isochrone_file_is_refetched(
    clients: geo.Clients, http: FakeHttp, tmp_path: Path
) -> None:
    run_geo(make_config(), clients, tmp_path)
    (tmp_path / "geo" / "isochrone.geojson").unlink()
    http.calls.clear()
    result = run_geo(make_config(), clients, tmp_path)
    assert result.recomputed and not result.fingerprint_changed
    assert (tmp_path / "geo" / "isochrone.geojson").exists()


# --- acceptance #6: include_places and exclude_places ------------------------


def test_include_places_adds_landsberg_and_keeps_weilheim(
    clients: geo.Clients, http: FakeHttp, tmp_path: Path
) -> None:
    include = ["Landsberg am Lech", "Weilheim in Oberbayern"]
    result = run_geo(make_config(include_places=include), clients, tmp_path)

    assert result.warnings == []
    assert result.places is not None
    by_name = {p.name: p for p in result.places.places}
    # Landsberg (36 min) is outside the isochrone and gets looked up by name.
    landsberg = by_name["Landsberg am Lech"]
    assert (landsberg.origin, landsberg.place_type) == (PlaceOrigin.INCLUDE, "town")
    assert landsberg.drive_time_minutes == 36.2
    assert result.places.places[-1] is landsberg
    # Weilheim (29.5 min) is already inside, so it is not looked up again.
    assert by_name["Weilheim in Oberbayern"].origin is PlaceOrigin.AREA
    include_query = http.calls[2][1]["data"]
    assert '"name"="Landsberg am Lech"' in include_query
    assert "Weilheim" not in include_query
    assert result.places.include_places == include


def test_exclude_places_removes_place(clients: geo.Clients, tmp_path: Path) -> None:
    result = run_geo(make_config(exclude_places=["Peiting", "peißenberg"]), clients, tmp_path)
    listed = names(result)
    assert "Peiting" not in listed
    assert "Peißenberg" not in listed  # case-insensitive
    assert len(listed) == 17
    assert result.warnings == []


def test_include_and_exclude_together(clients: geo.Clients, tmp_path: Path) -> None:
    config = make_config(include_places=["Landsberg am Lech"], exclude_places=["Peiting"])
    listed = names(run_geo(config, clients, tmp_path))
    assert "Landsberg am Lech" in listed
    assert "Peiting" not in listed


def test_changing_filters_reuses_isochrone_and_does_not_force_discovery(
    clients: geo.Clients, http: FakeHttp, tmp_path: Path
) -> None:
    run_geo(make_config(), clients, tmp_path)
    state_path = tmp_path / "data" / "state.json"
    state = State.model_validate_json(state_path.read_text("utf-8"))
    state.force_discovery = False
    state_path.write_text(state.model_dump_json(), encoding="utf-8")
    http.calls.clear()

    result = run_geo(make_config(exclude_places=["Peiting"]), clients, tmp_path)

    assert result.recomputed and not result.fingerprint_changed
    assert not any("/isochrones/" in url for url in http.urls())
    assert "Peiting" not in names(result)
    assert State.model_validate_json(state_path.read_text("utf-8")).force_discovery is False


def test_unknown_include_and_exclude_names_warn(clients: geo.Clients, tmp_path: Path) -> None:
    config = make_config(include_places=["Atlantis"], exclude_places=["Nowhere"])
    result = run_geo(config, clients, tmp_path)
    assert len(names(result)) == 19
    assert any("'Atlantis' not found" in w for w in result.warnings)
    assert any("'Nowhere' is not in the place list" in w for w in result.warnings)


# --- other behaviour -----------------------------------------------------------


def test_dry_run_writes_nothing(clients: geo.Clients, tmp_path: Path) -> None:
    result = run_geo(make_config(), clients, tmp_path, dry_run=True)
    assert len(names(result)) == 19
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "geo").exists()


def test_matrix_is_chunked(
    clients: geo.Clients, http: FakeHttp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(geo, "MATRIX_CHUNK", 5)
    result = run_geo(make_config(), clients, tmp_path)
    assert sum("/matrix/" in url for url in http.urls()) == 4  # 19 places / 5
    assert all(p.drive_time_minutes is not None for p in result.places.places)  # type: ignore[union-attr]


def test_unroutable_place_gets_none_and_warning(
    clients: geo.Clients, http: FakeHttp, tmp_path: Path
) -> None:
    http.matrix.pop((10.8, 47.7))  # Lechbruck am See
    result = run_geo(make_config(), clients, tmp_path)
    assert result.places is not None
    assert result.places.places[-1].name == "Lechbruck am See"
    assert result.places.places[-1].drive_time_minutes is None
    assert any("Lechbruck" in w for w in result.warnings)


def test_ors_error_raises(clients: geo.Clients, http: FakeHttp, tmp_path: Path) -> None:
    http.error = {"error": {"code": 3004, "message": "Parameter 'range' is out of range"}}
    with pytest.raises(geo.GeoError, match="3004"):
        run_geo(make_config(), clients, tmp_path)


def test_missing_key_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORS_API_KEY", raising=False)
    with pytest.raises(geo.GeoError, match="ORS_API_KEY"):
        run_geo(make_config(), None, tmp_path)


def test_clients_from_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORS_API_KEY", " abc ")
    monkeypatch.setenv("ORS_BASE_URL", "http://localhost:8080/ors/")
    monkeypatch.delenv("OVERPASS_URL", raising=False)
    clients = geo.Clients.from_env(http=FakeHttp())
    assert clients.ors_api_key == "abc"
    assert clients.ors_base_url == "http://localhost:8080/ors"
    assert clients.overpass_urls == geo.OVERPASS_URLS
    monkeypatch.setenv("OVERPASS_URL", "https://a/api/interpreter, https://b/api/interpreter")
    assert geo.Clients.from_env(http=FakeHttp()).overpass_urls == (
        "https://a/api/interpreter",
        "https://b/api/interpreter",
    )


# --- CLI -------------------------------------------------------------------------


def test_cli_geo_without_key_fails_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ORS_API_KEY", raising=False)
    assert main(["--config", str(CONFIG_PATH), "run", "--job", "geo"]) == 1
    assert "ORS_API_KEY" in capsys.readouterr().err


def test_cli_geo_runs_with_fake_clients(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    http: FakeHttp,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ORS_API_KEY", KEY)
    monkeypatch.setattr(geo, "UrllibHttp", lambda: http)
    args = ["--config", str(CONFIG_PATH), "run", "--job", "geo"]

    assert main(args) == 0
    out = capsys.readouterr().out
    assert "discovery will be forced" in out
    assert (tmp_path / "data" / "places.json").exists()

    assert main(args) == 0
    assert "unchanged" in capsys.readouterr().out

    assert main([*args, "--force"]) == 0
    assert "places within 5 min" in capsys.readouterr().out


def test_load_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    env.write_text('# comment\nORS_API_KEY="from-file"\nEMPTY=\nexport OTHER=1\n', "utf-8")
    monkeypatch.delenv("ORS_API_KEY", raising=False)
    monkeypatch.delenv("EMPTY", raising=False)
    monkeypatch.setenv("OTHER", "kept")
    load_dotenv(env)

    assert os.environ["ORS_API_KEY"] == "from-file"
    assert "EMPTY" not in os.environ
    assert os.environ["OTHER"] == "kept"


# --- HTTP client (local server, no internet) -------------------------------------


def test_urllib_http_retries_and_reports_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.delenv(var, raising=False)
    seen: list[tuple[str, str, bytes]] = []
    replies = [(429, b"{}"), (200, b'{"ok": true}'), (400, b'{"error": "bad"}')]

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"]))
            seen.append((self.path, self.headers.get("Authorization", ""), body))
            status, payload = replies.pop(0)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/v2/matrix/driving-car"
        sleeps: list[float] = []
        client = geo.UrllibHttp(retries=1, sleep=sleeps.append)
        assert client.post_json(url, {"a": 1}, {"Authorization": "k"}) == {"ok": True}
        assert sleeps == [5]
        assert seen[-1] == ("/v2/matrix/driving-car", "k", b'{"a": 1}')
        with pytest.raises(geo.GeoError, match="HTTP 400") as info:
            client.post_json(url + "?api_key=secret", {}, {})
        assert "secret" not in str(info.value)
    finally:
        server.shutdown()


def test_overpass_falls_back_to_next_instance(
    clients: geo.Clients, http: FakeHttp, tmp_path: Path
) -> None:
    original = http.post_form

    def flaky(url: str, data: dict[str, str]) -> dict[str, Any]:
        if url == geo.OVERPASS_URLS[0]:
            http.calls.append((url, data, {}))
            raise geo.GeoError("POST overpass-api.de failed: timed out")
        return original(url, data)

    http.post_form = flaky  # type: ignore[method-assign]
    result = run_geo(make_config(), clients, tmp_path)
    assert len(names(result)) == 19
    assert http.urls()[1:3] == list(geo.OVERPASS_URLS[:2])


def test_overpass_all_instances_fail(clients: geo.Clients, tmp_path: Path) -> None:
    def broken(url: str, data: dict[str, str]) -> dict[str, Any]:
        return {"remark": "runtime error: timeout"}

    clients.http.post_form = broken  # type: ignore[method-assign]
    with pytest.raises(geo.GeoError, match="all Overpass instances failed"):
        run_geo(make_config(), clients, tmp_path)
