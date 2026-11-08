# Data model

Four JSON files in `data/` (on the `data` branch) hold the entire state. Each
event is stored exactly once, even if it spans several days or appears on
several pages. Past events are deleted during merge; Git history is the record.

| File | Content |
| --- | --- |
| `data/places.json` | places in the area with coordinates and drive time, plus fingerprint of center and drive time |
| `data/sources.json` | known sources with origin, status and hit statistics |
| `data/events.json` | all events from yesterday to `crawl_days` ahead |
| `data/state.json` | last run per job, geo fingerprint, usage of the last run |

Pydantic models live in `src/letsgau/models.py` and are completed in the
phases that write each file. Data models reject unknown keys.

## Places (`data/places.json`)

Written by the geo stage. Besides the place list it stores the inputs it was
computed from, so the next run can tell whether anything changed.

```json
{
  "schema_version": 1,
  "fingerprint": "sha256:3f1c0a9b2d4e5f60",
  "generated_at": "2026-11-08T04:00:00Z",
  "center": { "lat": 47.811, "lon": 10.896 },
  "drive_time_minutes": 30,
  "place_types": ["city", "town", "village"],
  "include_places": ["Landsberg am Lech"],
  "exclude_places": [],
  "places": [
    { "name": "Schongau", "osm_id": "node/240040710", "place_type": "town",
      "lat": 47.8116, "lon": 10.8966, "drive_time_minutes": 1.6, "distance_km": 0.6,
      "origin": "area" }
  ]
}
```

| Field | Rule |
| --- | --- |
| `fingerprint` | `sha256:` + first 16 hex chars of the hash of `center` (6 decimals) and `drive_time_minutes` |
| `place_types`, `include_places`, `exclude_places` | copy of the config; a change recomputes the list with the stored isochrone |
| `places[].osm_id` | OpenStreetMap element, e.g. `node/240040710` |
| `places[].drive_time_minutes`, `distance_km` | by car from the center (ORS matrix), 1 decimal; `null` if not routable |
| `places[].origin` | `area` (inside the isochrone) or `include` (from `include_places`) |
| order | by drive time, then name; places without drive time last |

The isochrone itself is stored unchanged as the ORS GeoJSON response in
`geo/isochrone.geojson`.

## State (`data/state.json`)

Phase 1 version; Phase 6 adds the run history it needs for the scheduler.

| Field | Rule |
| --- | --- |
| `schema_version` | `1` |
| `geo_fingerprint` | fingerprint of the last geo computation |
| `force_discovery` | set to `true` when the geo fingerprint changes; the discovery stage clears it (Phase 7) |
| `last_run` | map job name → UTC timestamp of the last successful run |
| `last_usage` | map job name → AI usage of the last run: `requests`, `web_searches`, `web_fetches`, `input_tokens`, `output_tokens`, `cost_usd` (estimate, 0 in subscription mode), `duration_s` (Phase 2) |

## Event

```json
{
  "id": "2026-11-28-weihnachtsmarkt-schongau",
  "parent_id": null,
  "title": { "de": "Schongauer Weihnachtsmarkt", "en": "Schongau Christmas Market" },
  "description": { "de": "Buden und Glühwein in der Altstadt.", "en": "Stalls and mulled wine in the old town." },
  "venue": { "name": "Marienplatz", "place": "Schongau", "address": null },
  "schedule": {
    "start_date": "2026-11-28", "end_date": "2026-12-23",
    "start_time": null, "end_time": null, "all_day": true,
    "dates": null,
    "opening_hours": [ { "days": "mon-fri", "from": "16:00", "to": "20:00" } ]
  },
  "primary_tag": "market-festival",
  "tags": ["market-festival", "food-drink"],
  "status": "scheduled",
  "postponed_to": null,
  "sources": [ { "url": "https://...", "primary": true, "first_seen": "2026-11-09", "last_seen": "2026-11-12" } ],
  "first_seen": "2026-11-09", "last_verified": "2026-11-12", "misses": 0
}
```

The example is invented and only shows the shape. The file itself is
`{"schema_version": 1, "updated_at": "<UTC>", "events": [...]}`, sorted by
start date, start time and id.

| Field | Rule |
| --- | --- |
| `id` | stable: start date + slug of the German title and the place (umlauts as `ae`/`oe`/`ue`/`ss`; the place is left out if the title already contains it), e.g. `2026-11-28-weihnachtsmarkt-schongau`; never changes on merge |
| `parent_id` | set for program items (e.g. a band at a medieval market); venue is inherited from the parent |
| `title`, `description` | always DE and EN; proper names unchanged; description ≤ `description_max_chars`, own words |
| `schedule.start_time` | `null` if unknown; never guessed |
| `schedule.dates` | list of single days if not continuous (e.g. only Advent weekends) |
| `schedule.opening_hours` | optional, per weekday; the site shows today's hours |
| `primary_tag` | exactly one; drives grouping by category |
| `tags` | 1–3 from the fixed list, includes `primary_tag` |
| `status` | `scheduled`, `postponed`, `cancelled` or `unconfirmed` |
| `sources` | all occurrences; exactly one is `primary`, preferably the organizer's page |
| `misses` | consecutive runs in which the event was not found in the deep-check window |

Status lifecycle: see [ARCHITECTURE.md](ARCHITECTURE.md#event-status-lifecycle).

## Source

| Field | Rule |
| --- | --- |
| `url`, `domain` | entry page of the source |
| `kind` | `municipality`, `club`, `venue`, `calendar`, `press`, `other` |
| `places` | places the source is relevant for |
| `origin` | `whitelist`, `discovery` or `crawl` (found incidentally during an event run) |
| `status` | `active` or `blacklisted`; the blacklist file always wins |
| `hits`, `last_hit` | how many events recently came from here; basis for whitelist suggestions |

## Schema changes

Any change to these schemas requires a migration of the existing JSON files on
the `data` branch in the same pull request.
