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
phases that write each file.

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

The example is invented and only shows the shape.

| Field | Rule |
| --- | --- |
| `id` | stable: start date + slug of title and place; never changes on merge |
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
