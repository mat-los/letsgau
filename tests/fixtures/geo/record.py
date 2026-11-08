"""Re-record the geo fixtures from the live OpenRouteService and Overpass APIs.

Usage (from the repository root, with ``ORS_API_KEY`` in the environment or ``.env``)::

    python tests/fixtures/geo/record.py [--minutes 30]

Runs the geo stage as a dry run around the instance center (30 minutes by
default) with two ``include_places``, writes every response next to this file
and prints the place list. Also used by ``.github/workflows/geo-check.yml``.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from letsgau import geo
from letsgau.cli import load_dotenv
from letsgau.config import load_config

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


class RecordingHttp(geo.UrllibHttp):
    """Saves every response to a fixture file named after the request."""

    def post_json(self, url: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        response = super().post_json(url, body, headers)
        if "/isochrones/" in url:
            _save("ors_isochrone_schongau_30min.json", response)
        elif "/matrix/" in url:
            _save("ors_matrix_schongau.json", {"request": body, "response": response})
        return response

    def post_form(self, url: str, data: dict[str, str]) -> dict[str, Any]:
        response = super().post_form(url, data)
        is_include = '"name"=' in data["data"]
        _save("overpass_include.json" if is_include else "overpass_area_schongau.json", response)
        return response


def _save(name: str, data: dict[str, Any]) -> None:
    (HERE / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", "utf-8")
    print(f"wrote {name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--minutes", type=int, default=30, help="drive time (default: 30)")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    config = load_config(ROOT / "instance" / "config.yaml")
    region = config.region.model_copy(
        update={
            "drive_time_minutes": args.minutes,
            "include_places": ["Weilheim in Oberbayern", "Landsberg am Lech"],
            "exclude_places": [],
        }
    )
    config = config.model_copy(update={"region": region})
    with tempfile.TemporaryDirectory() as tmp:
        result = geo.compute(
            config,
            geo.Clients.from_env(http=RecordingHttp()),
            data_dir=Path(tmp) / "data",
            geo_dir=Path(tmp) / "geo",
            dry_run=True,
            force=True,
        )
    assert result.places is not None
    for warning in result.warnings:
        print(f"warning: {warning}")
    print(f"{len(result.places.places)} places within {args.minutes} min of {region.label}:")
    for place in result.places.places:
        minutes = "?" if place.drive_time_minutes is None else f"{place.drive_time_minutes:g}"
        print(f"  {minutes:>5} min  {place.name} ({place.place_type}, {place.origin})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
