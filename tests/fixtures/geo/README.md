# Geo fixtures

Responses from OpenRouteService (isochrones, matrix) and the Overpass API that
`tests/test_geo.py` replays, so the tests never touch the network.

| File | Endpoint |
| --- | --- |
| `ors_isochrone_schongau_30min.json` | `POST {ORS_BASE_URL}/v2/isochrones/driving-car`, 30 min around Schongau |
| `overpass_area_schongau.json` | Overpass: place nodes in the isochrone's bounding box |
| `overpass_include.json` | Overpass: lookup of `Weilheim in Oberbayern` and `Landsberg am Lech` |
| `ors_matrix_schongau.json` | `POST {ORS_BASE_URL}/v2/matrix/driving-car`, stored as `{request, response}` |

The two ORS files are live recordings from `api.heigit.org/openrouteservice`
(2026-11-08, 30 min around 47.811, 10.896). The isochrone is the real polygon;
the matrix holds the real drive times to the places in the Overpass fixtures.

The two Overpass files were written by hand in the documented response format,
because Overpass was not reachable from the development container. Their
coordinates are approximately right. `Weilheim in Oberbayern` is in the area
fixture because it lies inside the real isochrone (29.5 min); `Prem` (30.2 min)
and `Raisting` (40 min) are inside the bounding box but outside the isochrone.

To re-record everything, run (needs
`ORS_API_KEY` and network access):

```bash
python tests/fixtures/geo/record.py
```

The script runs the geo stage for 30 minutes around Schongau with
`include_places: [Weilheim in Oberbayern, Landsberg am Lech]` and overwrites the
files above. Check the diff and the tests afterwards: the test expectations name
places from the fixtures.
