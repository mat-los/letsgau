# Geo fixtures

Responses from OpenRouteService (isochrones, matrix) and the Overpass API that
`tests/test_geo.py` replays, so the tests never touch the network.

| File | Endpoint |
| --- | --- |
| `ors_isochrone_schongau_30min.json` | `POST {ORS_BASE_URL}/v2/isochrones/driving-car`, 30 min around Schongau |
| `overpass_area_schongau.json` | Overpass: place nodes in the isochrone's bounding box |
| `overpass_include.json` | Overpass: lookup of `Weilheim in Oberbayern` and `Landsberg am Lech` |
| `ors_matrix_schongau.json` | `POST {ORS_BASE_URL}/v2/matrix/driving-car`, stored as `{request, response}` |

All four files are real responses from 2026-11-08, 30 minutes around Schongau
(47.811, 10.896) with `include_places: [Weilheim in Oberbayern, Landsberg am Lech]`:

- ORS isochrone and matrix were recorded from `api.heigit.org/openrouteservice`.
- The Overpass responses come from overpass-api.de, captured by the *Geo check*
  workflow on GitHub Actions (Overpass was not reachable from the development
  container). Each place node is followed by its municipality (`area`,
  `admin_level=8`): 44 municipalities in the area, 45 with Landsberg. Weilheim lies inside the isochrone (29.7 min), so the include
  lookup only asked for Landsberg am Lech (34.2 min).

Result: 105 places in the area plus Landsberg. Overpass also returns places in
the bounding box that are outside the isochrone (e.g. Prem, Raisting, Buchloe).

To re-record everything, run (needs
`ORS_API_KEY` and network access):

```bash
python tests/fixtures/geo/record.py
```

The script runs the geo stage for 30 minutes around Schongau with
`include_places: [Weilheim in Oberbayern, Landsberg am Lech]` and overwrites the
files above. Check the diff and the tests afterwards: the test expectations name
places from the fixtures.
