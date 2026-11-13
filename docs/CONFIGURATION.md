# Configuration

Everything an operator wants to change lives in `instance/config.yaml`. The
file is validated on every start (`python -m letsgau check`). Unknown keys and
invalid values abort with file, line and field:

```text
error: invalid configuration in instance/config.yaml
  instance/config.yaml:16: region.municipalitys: unknown field (typo?)
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
| `contact_email` | string, optional | address shown on the pages (imprint; about, missing event and privacy only without a form service). At least one of `contact_email` and `feedback_form_endpoint` must be set |
| `feedback_form_endpoint` | URL or `""` | form service; empty = `mailto:` fallback |
| `indexing` | bool | `true` (default): search engines and AI crawlers are welcome. `false`: every page gets `<meta name="robots" content="noindex">`, robots.txt no longer lists the sitemap or invites AI crawlers (crawling stays allowed so the `noindex` is seen), and the build warns. Use it until the site is ready to go public |
| `goatcounter` | string, optional | GoatCounter site code (`<code>.goatcounter.com`, lower-case letters, digits, `-`). Set: every page loads GoatCounter's script, which counts page views without cookies or personal data, and the privacy page describes it. Empty (default): no counting, and the privacy page says so |
| `timezone` | IANA name | time zone of the region (default `Europe/Berlin`); the website's "today" and the dates in `events.ics` use it |

## `region`

| Key | Type | Meaning |
| --- | --- | --- |
| `label` | string | name of the region, used in page titles |
| `municipalities` | list | at least one `{ name, postcode }`; each needs a name, a postcode (4–5 digits) or both; a change triggers geo + discovery |

Notes for the geo stage (see [OPERATIONS.md](OPERATIONS.md#geo-stage)):

- With a postcode, the places come from the OpenStreetMap postcode area; the
  name (if given) then keeps only the places of that municipality, which helps
  when a postcode covers several municipalities.
- Without a postcode, the name must match the OSM municipality name
  (`admin_level=8`, case is ignored). If several municipalities share the name,
  the stage aborts and asks for the postcode.
- Districts (villages, hamlets, suburbs) are found automatically; the coverage
  sentence on the website lists the municipalities in config order.

```yaml
region:
  label: "Schongau"
  municipalities:
    - { name: "Schongau", postcode: "86956" }
    - { name: "Altenstadt", postcode: "86972" }
```

## `window`

| Key | Meaning |
| --- | --- |
| `display_days` | days ahead shown on the website (default 21) |
| `crawl_days` | days ahead searched (default 28); must be ≥ `display_days` |
| `deep_check_days` | days that get thorough re-checks (default 7); ≤ `crawl_days` |
| `store_days` | events up to this many days ahead are kept when the AI sees them anyway, e.g. on a flyer or a calendar page it reads (default 180, ≥ `crawl_days`). Nothing is searched or paged beyond `crawl_days`, and the prompt lists known events only up to `crawl_days`. Kept events appear on the website once they are within `display_days`; the ICS/JSON feeds contain all of them |

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
| `max_web_searches_per_run` | cost brake per run and stage; spread evenly over the remaining AI calls (at least 3 each, at most the call's own limit), calls are skipped once it is used up |
| `places_per_request` | municipalities per event-crawl batch; the places of a municipality (e.g. Peiting with Herzogsägmühle and Birkland) are always crawled together |
| `max_runtime_minutes` | no new AI call starts after this time (counted per stage) |

In API mode the search limit per batch is enforced (`max_uses`); in
subscription mode Claude Code has no such switch, so the limit is given in the
prompt and the reported searches count against the budget.

## `depth`

How thorough discovery and the event crawl are. More calls find more sources
and events but use more of the plan (or cost more in API mode). All keys are
optional; the defaults are the thorough settings.

| Key | Default | Meaning |
| --- | --- | --- |
| `discovery_focus_passes` | `true` | three discovery calls per batch (official calendars, clubs, venues), each with a query checklist and a coverage report; `false` = one general call |
| `discovery_places_per_request` | `2` | municipalities per discovery call |
| `discovery_searches_per_request` | `20` | search limit per discovery call |
| `crawl_sources_per_request` | `4` | known sources per *source sweep* call of the event crawl; every batch then ends with one *open search* call. `0` = one combined call per batch |
| `open_search` | `alternate` | when sweep mode runs the open search: `every_run`, `alternate` (every other run: Monday-to-Wednesday and Thursday-to-Sunday runs take turns, so with `weekly:mon,thu` once a week) or `off`. A query checklist run found few events the known sources did not already list |
| `crawl_searches_per_request` | `13` | search budget of the open search per batch, split into calls of about 13 searches (or the limit of the combined call). Each run starts the query checklist at a different point, so over a few runs every query is run even if a call stops early |

## `events`

| Key | Meaning |
| --- | --- |
| `unconfirmed_after_misses` | consecutive runs without a hit before `unconfirmed` |
| `description_max_chars` | maximum description length; longer descriptions are cut at a word boundary |

## `sources`

Paths of `whitelist_file`, `blacklist_file` and (optional) `flyers_file`,
`instagram_file` and `instagram_candidates_file`.

```yaml
# whitelist.yaml – always checked explicitly on every event run
- url: "https://www.schongau.de/veranstaltungen"
  note: "City of Schongau"
  places: ["Schongau"]        # optional: checked in the batch with these places;
                              # without it, in the first batch (regional sources)
- url: "https://veranstaltungen.pfaffen-winkel.de/{municipality_slug}/"
  note: "regional calendar"   # template: one URL per municipality in the area

# blacklist.yaml – domain: never used as a source; url: page not read as a source
- domain: "example-spam.de"
  reason: "outdated dates"
- url: "https://other-frontend.example/calendar/"
  reason: "same event database as a calendar that is already read"

# flyers.yaml – pages whose programme is in images or PDFs (posters, brochures)
- url: "https://www.reaktor-sog.de/"
  note: "concerts only as poster images"
  places: ["Schongau"]
  max_files: 20               # optional, default 15, at most 40 images/PDFs per run
```

Only pages on the flyer list are read as images: the crawl downloads the page,
takes the large images (`<img>`, `srcset`, `og:image`) and linked images/PDFs in
page order (no logos, icons, SVG/GIF or images under 20 KB; robots.txt is
respected), and one AI call per page, without web access, reads them and
extracts the events. The URL may also point straight to an image or PDF. An
empty `flyers_file` or a missing file means no flyer pages (the crawl warns).

`{municipality_slug}` is replaced by the slug of each municipality (lower case,
umlauts as `ae`/`oe`/`ue`/`ss`, spaces as `-`), restricted to `places` if given.
`python -m letsgau sources --check` lists all URLs with their HTTP status.

A `domain` entry applies to the domain and all its subdomains. It is passed to
the AI (API: `blocked_domains`; Claude Code: WebFetch deny rules) and every
source from it is removed afterwards; an event without any other source is
dropped. A `url` entry is narrower: known sources whose URL starts with it
(compared without `www.`, scheme and trailing slash) are not read in the source
sweeps, so a second front end of the same event database does not cost tokens
twice. Links to such pages may still appear as additional sources of events.

`instagram_file` lists Instagram accounts read on every events run:

```yaml
- username: "cafe.example"    # without @; an instagram.com link works too
  places: ["Schongau"]        # optional: the batch with these places; without, the first
  note: "Café, live music"
```

Only business and creator accounts can be read (setup and secrets in
docs/OPERATIONS.md). An empty `instagram_file` switches Instagram off: no
reading and no collecting of account names (neither from pages the crawl
reads nor by discovery).

`instagram_candidates_file` lists **unconfirmed** accounts, usually written by
the scheduled lookup session (`prompts/instagram_session.md`):

```yaml
- username: "jeanscafe"
  name: "Jeanscafe"            # the place the name was attributed to
  places: ["Schongau"]
  website: ""                  # the place's own website, if known
  found_on: "https://restaurantguru.com/Jeanscafe-Schongau"
```

Each entry is checked against the account's profile (events and discovery
runs) and read only if it names the municipality or links the website; see
docs/OPERATIONS.md.

## `instagram`

| Key | Default | Meaning |
| --- | --- | --- |
| `posts_per_account` | `6` | latest posts fetched per account and run |
| `max_post_age_days` | `21` | older posts are not read |
| `max_accounts_per_run` | `40` | accounts read per run: the list first, then mined accounts (least recently read first) |
| `read_mined` | `true` | also read accounts found on pages the crawl reads or by discovery |
| `api_version` | `""` | Graph API version such as `v24.0`; empty uses the app's default |
| `prescreen` | `true` | before a post's images are read, a text-only call to `models.merge` drops posts whose caption is clearly not about an upcoming event (cached, not read again); when in doubt, a post is read |

## `excluded_topics`

Free-text topic blacklist. Matching events are never included (checked in the
crawl prompt and again in the merge step, where stored events are checked too:
a topic added later also removes events already in the calendar).

## `excluded_title_patterns`

List of regular expressions (Python syntax, case-insensitive), matched against
the German title. A matching event is dropped by the crawl's validation and
removed from the stored events by the merge, without asking the AI. Use it for
the obvious cases (`'\bJahreshauptversammlung\b'`, `'\bProbe\b'`); everything
else is left to `excluded_topics`. Write patterns in single quotes in YAML. An
invalid pattern is a configuration error. Default: none.

## `tags`

Fixed list of `{id, de, en}`. IDs are lowercase kebab-case and unique; the AI
must not invent new ones. Two IDs get an extra rule in the events prompt if
present: `religion` (events organised by a parish or church) and `seniors`
(events made for older people).
