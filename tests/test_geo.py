"""Tests for the geo stage: municipalities by name and/or postcode (Overpass, faked)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from letsgau import geo
from letsgau.config import Config, Municipality, load_config
from letsgau.models import PlacesFile, State

REPO_ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 11, 13, 1, 0, tzinfo=UTC)


def node(osm_id: int, name: str, place: str = "village") -> dict[str, Any]:
    return {
        "type": "node",
        "id": osm_id,
        "lat": 47.8,
        "lon": 10.9,
        "tags": {"name": name, "place": place},
    }


def area(name: str) -> dict[str, Any]:
    return {"type": "area", "id": 3600000000, "tags": {"name": name, "admin_level": "8"}}


ANSWERS = {
    # postcode 86956: Schongau and a hamlet of the neighbouring municipality
    '"postal_code"="86956"': [
        node(1, "Schongau", "town"),
        area("Schongau"),
        node(2, "Lechfeld", "hamlet"),
        area("Peiting"),
    ],
    # postcode 86972: Altenstadt with a district, the district listed first
    '"postal_code"="86972"': [
        node(3, "Schwabniederhofen"),
        area("Altenstadt"),
        node(4, "Altenstadt"),
        area("Altenstadt"),
    ],
}


class Http:
    """Fake Overpass: answers by the postcode or name in the query."""

    def __init__(self, answers: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.answers = ANSWERS if answers is None else answers
        self.queries: list[str] = []

    def post_form(self, url: str, data: dict[str, str]) -> dict[str, Any]:
        query = data["data"]
        self.queries.append(query)
        for key, elements in self.answers.items():
            if key in query:
                return {"elements": elements}
        return {"elements": []}


@pytest.fixture
def config() -> Config:
    return load_config(REPO_ROOT / "instance" / "config.yaml")


def clients(http: Http | None = None) -> geo.Clients:
    return geo.Clients(http=http or Http(), sleep=lambda _s: None)


def test_queries_by_postcode_or_by_name() -> None:
    by_postcode = geo.municipality_query(Municipality(name="Altenstadt", postcode="86972"))
    assert 'area["boundary"="postal_code"]["postal_code"="86972"]' in by_postcode
    assert "is_in" in by_postcode  # each place with its municipality
    by_name = geo.municipality_query(Municipality(name="Altenstadt"))
    assert '["admin_level"="8"]["name"="Altenstadt"]' in by_name and "out ids" in by_name


def test_places_of_the_municipalities_in_config_order(config: Config, tmp_path: Path) -> None:
    http = Http()
    result = geo.compute(config, clients(http), data_dir=tmp_path, now=NOW)
    places = result.places
    assert places is not None
    assert [(p.name, p.municipality) for p in places.places] == [
        ("Schongau", "Schongau"),
        ("Altenstadt", "Altenstadt"),  # main place first
        ("Schwabniederhofen", "Altenstadt"),
    ]
    assert places.municipalities == [
        "Schongau 86956",
        "Altenstadt 86972",
        "Peiting 86971",
        "Hohenfurch 86978",
    ]
    assert result.fingerprint_changed  # no file before: discovery is forced
    stored = PlacesFile.model_validate_json((tmp_path / "places.json").read_text("utf-8"))
    assert stored.places == places.places
    assert "drive_time_minutes" not in json.loads((tmp_path / "places.json").read_text("utf-8"))
    state = State.model_validate_json((tmp_path / "state.json").read_text("utf-8"))
    assert state.force_discovery and state.last_run["geo"] == NOW

    # unchanged: nothing is asked
    again = Http()
    assert not geo.compute(config, clients(again), data_dir=tmp_path, now=NOW).recomputed
    assert again.queries == []


def test_a_legacy_file_is_recomputed_without_forcing_discovery(
    config: Config, tmp_path: Path
) -> None:
    legacy = {
        "schema_version": 1,
        "fingerprint": "sha256:old",
        "generated_at": "2026-11-12T19:42:00Z",
        "center": {"lat": 47.811, "lon": 10.896},
        "drive_time_minutes": 5,
        "place_types": ["city", "town", "village"],
        "include_places": ["Altenstadt"],
        "places": [
            {
                "name": "Schongau",
                "osm_id": "node/1",
                "place_type": "town",
                "lat": 47.8,
                "lon": 10.9,
                "drive_time_minutes": 2.6,
                "origin": "area",
                "municipality": "Schongau",
            },
            {
                "name": "Altenstadt",
                "osm_id": "node/4",
                "place_type": "village",
                "lat": 47.8,
                "lon": 10.9,
                "drive_time_minutes": 7.8,
                "origin": "include",
                "municipality": "Altenstadt",
            },
        ],
    }
    (tmp_path / "places.json").write_text(json.dumps(legacy), encoding="utf-8")
    result = geo.compute(config, clients(), data_dir=tmp_path, now=NOW)
    assert result.recomputed and not result.fingerprint_changed  # same municipalities
    state = State.model_validate_json((tmp_path / "state.json").read_text("utf-8"))
    assert not state.force_discovery


def test_an_ambiguous_name_needs_the_postcode(config: Config, tmp_path: Path) -> None:
    region = config.region.model_copy(update={"municipalities": [Municipality(name="Altenstadt")]})
    config = config.model_copy(update={"region": region})
    answers = {
        '"name"="Altenstadt"': [
            area("Altenstadt"),
            area("Altenstadt"),
            node(4, "Altenstadt"),
            area("Altenstadt"),
        ]
    }
    with pytest.raises(geo.GeoError, match="add its postcode"):
        geo.compute(config, clients(Http(answers)), data_dir=tmp_path, now=NOW)
    single = {
        '"name"="Altenstadt"': [area("Altenstadt"), node(4, "Altenstadt"), area("Altenstadt")]
    }
    result = geo.compute(config, clients(Http(single)), data_dir=tmp_path, now=NOW)
    assert result.places is not None and [p.name for p in result.places.places] == ["Altenstadt"]


def test_postcode_only_takes_every_municipality_in_it(config: Config, tmp_path: Path) -> None:
    region = config.region.model_copy(update={"municipalities": [Municipality(postcode="86956")]})
    config = config.model_copy(update={"region": region})
    result = geo.compute(config, clients(), data_dir=tmp_path, now=NOW, dry_run=True)
    assert result.places is not None
    assert {p.municipality for p in result.places.places} == {"Schongau", "Peiting"}
    assert not (tmp_path / "places.json").exists()  # dry run


def test_nothing_found_is_an_error(config: Config, tmp_path: Path) -> None:
    with pytest.raises(geo.GeoError, match="no places found"):
        geo.compute(config, clients(Http({})), data_dir=tmp_path, now=NOW)


def test_fingerprint_follows_the_municipality_list(config: Config) -> None:
    fp = geo.fingerprint(config.region)
    swapped = config.region.model_copy(
        update={"municipalities": list(reversed(config.region.municipalities))}
    )
    assert geo.fingerprint(swapped) != fp
    assert geo.fingerprint(config.region) == fp
