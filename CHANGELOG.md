# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `window.store_days` (default 180): events the AI sees anyway up to this far
  ahead are kept instead of dropped; searching still stops at `crawl_days`,
  the website still shows `display_days`, the feeds contain all events.
- Flyer cache `data/flyers.json`: flyer files are fingerprinted (SHA-256) and
  read by the AI only once; unchanged files reuse their stored events.
- Merge: events with the same day, start time and place always go to the AI
  duplicate check (also when the titles share few words), including pairs
  already stored, which cleans up existing duplicates.
- A manual `events` run also rebuilds the site; the deploy job is skipped
  instead of failing when no site was built.
- Flyer pages: `instance/sources/flyers.yaml` (`sources.flyers_file`) lists pages
  whose programme is only in posters, brochure images or PDFs. The crawl
  downloads their images/PDFs (robots.txt respected, logos and icons skipped)
  and one AI call per page reads them without web access. Starts with Reaktor,
  Brauhaus, the parish newsletter and the Schlossbergler club calendar.
- New website look: green header band with brand icon, date cards and search,
  one colour per category (chips, card stripe, tags; mapped from the configured
  tags), events as cards with place pin, section icons, nicer pager, footer and
  content pages, refined dark mode. Days without events are dimmed in the date bar.
- `depth` config section for more thorough runs. Discovery makes three focused
  passes per batch (official calendars, clubs, venues), each with a German
  query checklist per municipality and a coverage report per category, which
  ends up in the *Coverage* section of `data/suggested.md`. The event crawl
  splits each batch into source sweeps that read every known source completely
  (no cap of 12) and one open search with a query checklist per month, topic
  and district.
- The *Crawl check* workflow also runs discovery (into a throwaway `data/`).
- `site.indexing` switch: `false` puts `noindex` on every page and stops inviting
  crawlers in robots.txt, to keep a site out of search results until it is ready.
- Geo retries the whole Overpass instance list up to three times (waiting 60 s,
  then 120 s) when every public instance is at capacity.
- Phase 7 discovery: Opus finds and rates sources per batch of
  municipalities (`prompts/discovery.md`), `data/sources.json` with origin,
  rating, update frequency and hits, whitelist synced in, blacklist enforced,
  `data/suggested.md` with whitelist and blacklist candidates (ADR-004); the
  events stage uses known good sources per batch and counts hits; forced after
  a geo change.
- Phase 6 scheduler and workflow: `--job auto` runs only due jobs (catch-up
  of missed runs, frequency changes apply at once, discovery forced after a
  geo change, build after every events run), a failing stage does not stop the
  build; `last_run` in `state.json`; job summary with event changes and AI
  usage. Workflow `run.yml`: daily cron, "Run workflow" button with job choice
  and dry run, concurrency group, `data` branch worktree and bot commit, Pages
  deploy.
- Phase 5 AI readability: schema.org `Event` JSON-LD on every day page
  (program items as `subEvent`), `llms.txt`, public `data/events.json`,
  iCalendar feeds per language, `sitemap.xml` with hreflang, `robots.txt`
  allowing AI crawlers, Open Graph and description per day.
- Phase 8 content and wrap-up: content pages from `instance/content/`
  (about, "Why is my event not here?", imprint, privacy) in DE and EN with
  values filled from the config and `[TODO]` markers listed by `letsgau check`;
  feedback page (form service or `mailto:` fallback, honeypot, link to the issue
  forms); changelog page; `CNAME` from `base_url`; `letsgau init` creates a
  fresh instance from `examples/instance/` (interactive or with options).
- Phase 4 website (`letsgau run --job build`): day pages DE/EN for
  `display_days` with coverage sentence, quick links, date bar with counts,
  sections (all day collapsed, morning, afternoon, evening), expandable events
  with sources, opening hours, program items and status; tag chips with
  include/exclude, search, grouping by time/category/place, swipe, sliding
  header, settings in `localStorage`; everything readable without JavaScript.
  New option `site.timezone`. Workflow `site-check.yml` (Lighthouse, curl).
- Phase 3 merge and status: fuzzy matching of crawled and stored events (dates,
  municipality, start time, normalized titles), one Haiku call per run for
  unclear pairs and the topic check of new events (`prompts/merge.md`),
  `misses`/`unconfirmed` for events not found again in crawled municipalities,
  cancellations and postponements from the crawl, cleanup; merge summary in the
  log (also with `--dry-run`). AI steps can run without web tools.
- Whitelist URL templates with `{municipality_slug}` (one URL per municipality)
  and `letsgau sources [--check]` (instance whitelist updated accordingly).

- Phase 2 event crawl (`letsgau run --job events`): places in batches of
  `places_per_request`, one AI call per batch with `prompts/events.md`, answers
  forced into a JSON schema and validated with Pydantic (28-day window, places
  in the area, tags from the list, exactly one primary tag and source, no
  blacklisted source); invalid entries are dropped and logged.
- AI runner for both modes: Claude Code headless (`claude -p --json-schema`,
  only WebSearch/WebFetch, blacklist as WebFetch deny rules, API keys removed
  from its environment) and the Claude API (`web_search_20260209` /
  `web_fetch_20260209` with `blocked_domains`, strict `submit_result` tool).
- Search budget (`max_web_searches_per_run`) spread over the batches, runtime
  limit, whitelist check per batch, usage in `data/state.json`.
- Models `Event` and `EventsFile`; id-based merge into `data/events.json`
  (sources appended, past events deleted); fuzzy matching follows in Phase 3.
- `whitelist.yaml` entries take optional `places`.
- Places get their `municipality` (OSM `admin_level=8`) in the geo stage; the
  crawl batches by municipality (districts like Herzogsägmühle are searched
  with Peiting), `--limit-places` counts municipalities.
- Workflow `crawl-check.yml`: live dry run of the crawl on GitHub Actions with
  the `CLAUDE_CODE_OAUTH_TOKEN` secret; `LETSGAU_RECORD_DIR` saves raw answers.

- Phase 1 geo stage (`letsgau run --job geo`): OpenRouteService isochrone
  (`driving-car`) via the new HeiGIT host `api.heigit.org/openrouteservice`,
  places from the Overpass API, drive times from the ORS matrix; writes
  `data/places.json`, `data/state.json` and `geo/isochrone.geojson`.
- Geo fingerprint of center and drive time: recompute only on change, then
  `state.json` gets `force_discovery`; changing only the place filters reuses
  the stored isochrone. `--force` recomputes anyway.
- `include_places` (looked up by name in OpenStreetMap) and `exclude_places`.
- Pydantic models `Place`, `PlacesFile` and a first `State`.
- Workflow `geo-check.yml`: live geo check on GitHub Actions (manual or on
  changes to the workflow) with the `ORS_API_KEY` secret; prints the place list
  and uploads re-recorded fixtures.
- `.env` is loaded by `letsgau run`; tests replay ORS/Overpass fixtures
  (`tests/fixtures/geo/`) with a script to re-record them.

- Phase 0 scaffold: repository structure, `pyproject.toml`, Ruff, pytest and CI.
- `instance/config.yaml` loaded and validated with Pydantic; errors report
  file, line and field.
- CLI `python -m letsgau` with `check`, `run` and `init` (stages are stubs).
- First versions of `README.md`, `CLAUDE.md`, `docs/ARCHITECTURE.md`,
  `docs/CONFIGURATION.md`, `docs/DATA_MODEL.md`, `docs/OPERATIONS.md`.
- Specification `docs/CONCEPT.md` and task list `docs/ISSUES.md`.
- Issue forms for bug, feature request and source suggestion.

### Changed

- Day pages show one list instead of time-of-day sections: all-day, multi-day
  and events without a start time come first, then events by start time. The
  "Group by: Time" option is now "None"; category and place grouping stay.
- Event crawl prompt: no summary entries such as a cinema's whole programme;
  each film or event is reported on its own.
- In subscription mode the usage summary shows the cost at API prices (the
  CLI's `total_cost_usd`) instead of 0, as a measure of plan usage;
  discovery usage is saved in `state.json` too.
- `max_runtime_minutes` and the search budget apply per AI call (and stage),
  not per batch.
- The *Crawl check* workflow runs only on demand, no longer on pull requests that
  change a prompt: it uses about as much of the plan as a real run.
