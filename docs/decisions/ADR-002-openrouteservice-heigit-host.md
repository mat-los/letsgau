# ADR-002: OpenRouteService via the HeiGIT host, Overpass for places

- Status: accepted
- Date: 2026-11-08

## Context

The geo stage needs a drive-time isochrone, the places inside it and the drive
time to each place. The concept names OpenRouteService (ORS) and the Overpass
API. In 2026 HeiGIT deprecated the old ORS host `api.openrouteservice.org`
(reduced quota from August, shutdown announced for autumn 2026) in favour of
`api.heigit.org`, and moved key management to `account.heigit.org`.

## Decision

- ORS calls go to `https://api.heigit.org/openrouteservice/v2/{isochrones,matrix}/driving-car`
  with the key in the `Authorization` header. The base URL can be overridden
  with `ORS_BASE_URL` (e.g. a self-hosted ORS, whose paths are the same).
- Places come from Overpass (`OVERPASS_URL`): place nodes in the isochrone's
  bounding box, then a point-in-polygon test in Python. This keeps the query
  short regardless of the polygon's vertex count.
- HTTP uses the standard library (`urllib`), no extra dependency; tests replay
  recorded responses through a small `Http` interface.

## Consequences

If HeiGIT moves the API again, only `ORS_BASE_URL` (or the default in
`geo.py`) changes. Overpass is a free community service without a key; heavy use
should be avoided, which fits the "recompute only on change" rule.
