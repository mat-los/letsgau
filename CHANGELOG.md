# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
- Whitelist: Reaktor (reaktor-sog.de) and the RCE event print list for Schongau.
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
