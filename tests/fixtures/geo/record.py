"""Re-record the geo fixtures from the live OpenRouteService and Overpass APIs.

Usage (from the repository root, with ``ORS_API_KEY`` in the environment or ``.env``)::

    python tests/fixtures/geo/record.py

Runs the geo stage as a dry run for 30 minutes around Schongau with two
``include_places`` and writes every response next to this file.
"""

from __future__ import annotations

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
    load_dotenv(ROOT / ".env")
    config = load_config(ROOT / "instance" / "config.yaml")
    region = config.region.model_copy(
        update={
            "drive_time_minutes": 30,
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
    for place in result.places.places:
        print(f"  {place.drive_time_minutes} min  {place.name} ({place.place_type})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
