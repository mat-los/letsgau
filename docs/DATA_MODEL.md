# Data model

Four JSON files in `data/` (on the `data` branch) hold the entire state. Each
event is stored exactly once, even if it spans several days or appears on
several pages. Past events are deleted during merge; Git history is the record.

| File | Content |
| --- | --- |
| `data/places.json` | places in the area with coordinates and drive time, plus fingerprint of center and drive time |
| `data/sources.json` | known sources with origin, status and hit statistics |
| `data/events.json` | all events from yesterday to `store_days` ahead (searched up to `crawl_days`) |
| `data/flyers.json` | flyer files already read by the AI, by SHA-256 of the file, with the events found on them |
| `data/state.json` | last run per job, geo fingerprint, usage of the last run |

Pydantic models live in `src/letsgau/models.py` and are completed in the
phases that write each file. Data models reject unknown keys.

## Flyer cache (`data/flyers.json`)

Written by the events stage for pages on the flyer list. `files` maps the
SHA-256 of each downloaded image/PDF to `page_url`, `file_url`, `kind`
(`image`/`pdf`), `version` (extraction version; older entries are read again),
`first_seen`, `last_seen` and `events`: the raw event candidates the AI read
from that file, all of them up to `store_days`. On later runs an unchanged file
is not sent to the AI again; its cached candidates are validated against the
current date and merged like freshly read ones. Entries whose file has not been
on its page for 30 days are dropped. Deleting the file only means every flyer
is read again once.

## Instagram (`data/instagram.json`)

Written by the events stage when `sources.instagram_file` is set; discovery
adds the accounts it finds.

- `accounts`: account name → `origin` (`whitelist` from the list, `mined` from
  links on pages the crawl read, or found by discovery), `places`, `status` (`new`, `active`,
  `not_business`, `not_found`, `error`), `first_seen`, `last_checked`,
  `last_error`. Accounts that cannot be read are tried again after 30 days.
- `posts`: media ID → `username`, `permalink`, `posted`, `version`,
  `first_seen`, `last_seen` and `events` (raw candidates, validated again on
  every use). A post is read by the AI once; posts older than
  `max_post_age_days` + 30 days are dropped.

Deleting the file only means the recent posts are read again once and mined
accounts are found again.

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
      "origin": "area", "municipality": "Schongau" }
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
| `places[].municipality` | municipality (OSM `admin_level=8`), e.g. `Peiting` for Herzogsägmühle; the place's own name if it lies in none (Phase 2, optional field; older files are refreshed once by the geo stage) |
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
| `last_usage` | map job name → AI usage of the last run: `requests`, `web_searches`, `web_fetches`, `input_tokens`, `output_tokens`, `cost_usd` (cost at API prices; in subscription mode the CLI's `total_cost_usd`, not billed), `duration_s` (Phase 2) |

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
| `schedule.opening_hours` | optional, per weekday (`mon-fri`) or per date (`2026-11-21`, a date wins over its weekday); the site shows today's hours, or the start time if `from` equals `to` (screenings) |
| `primary_tag` | exactly one; drives grouping by category |
| `tags` | 1–3 from the fixed list, includes `primary_tag` |
| `status` | `scheduled`, `postponed`, `cancelled` or `unconfirmed` |
| `sources` | all occurrences; exactly one is `primary`, preferably the organizer's page |
| `misses` | consecutive runs in which the event was not found in the deep-check window |

Status lifecycle: see [ARCHITECTURE.md](ARCHITECTURE.md#event-status-lifecycle).

Merge rules (Phase 3, `src/letsgau/merge.py`):

- **Same event:** equal `id`, or overlapping dates, same municipality, start
  times at most 2 h apart (when both are known) and similar titles (normalized:
  lower case, umlauts, without filler words, place names and years). Similarity
  ≥ 0.85 merges directly, 0.5–0.85 is decided by the merge model, and so is any
  pair with the same day, start time and municipality (also two stored events,
  so old duplicates get cleaned up). A program item never matches a main event.
- **On a match** the stored `id` and `first_seen` stay, texts, schedule, tags
  and status come from the crawl (a single day of a known multi-day event keeps
  the full range), sources are appended, `misses` is reset, `unconfirmed`
  becomes `scheduled`.
- **Not found again:** an event in the deep-check window (`deep_check_days`)
  whose municipality was crawled successfully gets `misses + 1`; at
  `unconfirmed_after_misses` it becomes `unconfirmed`. Cancelled and postponed
  events are not counted.
- **Topic check:** new events matching `excluded_topics` (judged by the merge
  model) are dropped and logged.
- **Cleanup:** events whose last day is before yesterday are deleted.

## Source (`data/sources.json`)

`{"schema_version": 1, "updated_at": "<UTC>", "sources": [...]}`, written by
discovery and updated by every events run (ADR-004).

| Field | Rule |
| --- | --- |
| `url`, `domain` | page that lists the events; `domain` without `www.` |
| `name`, `note` | short name and remark from discovery (e.g. "paginated with ?page=2") |
| `kind` | `municipality`, `club`, `venue`, `calendar`, `press`, `other` |
| `places` | municipalities the source covers |
| `origin` | `whitelist`, `discovery` or `crawl` (found incidentally during an event run) |
| `status` | `active` or `blacklisted`; the blacklist file always wins |
| `rating` | 1–5 from discovery (5: current dates as text, updated often), `null` if not rated |
| `update_frequency` | `weekly`, `monthly`, `rare`, `unknown` |
| `first_seen`, `last_checked` | first time known, last discovery check |
| `hits`, `last_hit` | events found on this source in total; basis for whitelist suggestions (≥ 3 hits) |

## Schema changes

Any change to these schemas requires a migration of the existing JSON files on
the `data` branch in the same pull request.
