# Configuration

Everything an operator wants to change lives in `instance/config.yaml`. The
file is validated on every start (`python -m letsgau check`). Unknown keys and
invalid values abort with file, line and field:

```text
error: invalid configuration in instance/config.yaml
  instance/config.yaml:16: region.drive_time_minuts: unknown field (typo?)
  instance/config.yaml:28: jobs.events: invalid schedule 'weekly:mon,thr'; ...
```

Whitelist and blacklist are separate files next to it (`instance/sources/`).

## `site`

| Key | Type | Meaning |
| --- | --- | --- |
| `name` | string | site name in the header |
| `base_url` | URL | public URL; `CNAME` is generated from it (Phase 8) |
| `languages` | list of `de`, `en` | first entry is the default language |
| `owner` | string | must equal the GitHub repo owner, else the build aborts |
| `contact_email` | string | address for source suggestions and feedback |
| `feedback_form_endpoint` | URL or `""` | form service; empty = `mailto:` fallback |
| `indexing` | bool | `true` (default): search engines and AI crawlers are welcome. `false`: every page gets `<meta name="robots" content="noindex">`, robots.txt no longer lists the sitemap or invites AI crawlers (crawling stays allowed so the `noindex` is seen), and the build warns. Use it until the site is ready to go public |
| `timezone` | IANA name | time zone of the region (default `Europe/Berlin`); the website's "today" and the dates in `events.ics` use it |

## `region`

| Key | Type | Meaning |
| --- | --- | --- |
| `label` | string | name of the center, used in the coverage sentence |
| `center` | `{lat, lon}` | center point |
| `drive_time_minutes` | 1–180 | drive time by car; a change triggers geo + discovery |
| `place_types` | list | OSM place types: `city`, `town`, `village`, `hamlet`, `suburb` |
| `include_places` | list | places added even if outside the isochrone |
| `exclude_places` | list | places removed from the result |

Notes for the geo stage (see [OPERATIONS.md](OPERATIONS.md#geo-stage)):

- Only a change of `center` or `drive_time_minutes` fetches a new isochrone and
  forces discovery. Changing `place_types`, `include_places` or
  `exclude_places` recomputes the place list with the stored isochrone.
- `include_places` names must match the OpenStreetMap `name` exactly (case is
  ignored), e.g. `Landsberg am Lech`, `Weilheim in Oberbayern`. They are looked
  up within max(50 km, 2 km × drive time) of the center, any place type; with
  several hits the closest wins. Unknown names produce a warning.
- `exclude_places` compares names case-insensitively with the computed list.

## `window`

| Key | Meaning |
| --- | --- |
| `display_days` | days ahead shown on the website (default 21) |
| `crawl_days` | days ahead searched (default 28); must be ≥ `display_days` |
| `deep_check_days` | days that get thorough re-checks (default 7); ≤ `crawl_days` |

## `jobs`

Format per job: `off`, `daily`, `weekly:<day>[,<day>...]` (days `mon`..`sun`)
or `monthly:<1-31>`.

| Key | Default | Meaning |
| --- | --- | --- |
| `discovery` | `monthly:1` | find and rate sources |
| `events` | `weekly:mon,thu` | crawl + merge events |
| `build` | `daily` | rebuild the site so "today" is correct |

## `models`

Claude model IDs per step: `discovery`, `events`, `merge`.

## `ai`

| Key | Values | Meaning |
| --- | --- | --- |
| `auth` | `subscription`, `api_key` | `subscription`: Claude Code headless with `CLAUDE_CODE_OAUTH_TOKEN`; `api_key`: Claude API with `ANTHROPIC_API_KEY` |

## `limits`

| Key | Meaning |
| --- | --- |
| `max_web_searches_per_run` | cost brake per run; spread evenly over the remaining batches (3–40 per batch), batches are skipped once it is used up |
| `places_per_request` | municipalities per Claude request (batch size); the places of a municipality (e.g. Peiting with Herzogsägmühle and Birkland) are always crawled together |
| `max_runtime_minutes` | no new batch starts after this time |

In API mode the search limit per batch is enforced (`max_uses`); in
subscription mode Claude Code has no such switch, so the limit is given in the
prompt and the reported searches count against the budget.

## `events`

| Key | Meaning |
| --- | --- |
| `unconfirmed_after_misses` | consecutive runs without a hit before `unconfirmed` |
| `description_max_chars` | maximum description length; longer descriptions are cut at a word boundary |

## `sources`

Paths of `whitelist_file` and `blacklist_file`.

```yaml
# whitelist.yaml – always checked explicitly on every event run
- url: "https://www.schongau.de/veranstaltungen"
  note: "City of Schongau"
  places: ["Schongau"]        # optional: checked in the batch with these places;
                              # without it, in the first batch (regional sources)
- url: "https://veranstaltungen.pfaffen-winkel.de/{municipality_slug}/"
  note: "regional calendar"   # template: one URL per municipality in the area

# blacklist.yaml – never used as a source
- domain: "example-spam.de"
  reason: "outdated dates"
```

`{municipality_slug}` is replaced by the slug of each municipality (lower case,
umlauts as `ae`/`oe`/`ue`/`ss`, spaces as `-`), restricted to `places` if given.
`python -m letsgau sources --check` lists all URLs with their HTTP status.

The blacklist applies to the domain and all its subdomains. It is passed to the
AI (API: `blocked_domains`; Claude Code: WebFetch deny rules) and every source
from it is removed afterwards; an event without any other source is dropped.

## `excluded_topics`

Free-text topic blacklist. Matching events are never included (checked in the
crawl prompt and again in the merge step).

## `tags`

Fixed list of `{id, de, en}`. IDs are lowercase kebab-case and unique; the AI
must not invent new ones.
