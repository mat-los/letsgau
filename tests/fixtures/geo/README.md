# Geo fixtures

Responses from OpenRouteService (isochrones, matrix) and the Overpass API that
`tests/test_geo.py` replays, so the tests never touch the network.

| File | Endpoint |
| --- | --- |
| `ors_isochrone_schongau_30min.json` | `POST {ORS_BASE_URL}/v2/isochrones/driving-car`, 30 min around Schongau |
| `overpass_area_schongau.json` | Overpass: place nodes in the isochrone's bounding box |
| `overpass_include.json` | Overpass: lookup of `Weilheim in Oberbayern` and `Landsberg am Lech` |
| `ors_matrix_schongau.json` | `POST {ORS_BASE_URL}/v2/matrix/driving-car`, stored as `{request, response}` |

The first versions were written by hand in the documented response formats,
because the development container had neither network access to the APIs nor an
ORS key. The coordinates are approximately right, the drive times are estimates,
and the polygon is simplified. To replace them with real recordings, run (needs
`ORS_API_KEY` and network access):

```bash
python tests/fixtures/geo/record.py
```

The script runs the geo stage for 30 minutes around Schongau with
`include_places: [Weilheim in Oberbayern, Landsberg am Lech]` and overwrites the
files above. Check the diff and the tests afterwards: the test expectations name
places from the fixtures.
