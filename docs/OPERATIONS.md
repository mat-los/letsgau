# Operations

> Phase 2: `check`, `geo` and `events` work. Discovery, the website, the workflow
> and cost reporting are added in later phases; this page grows with them.

## Validate the configuration

```bash
python -m letsgau check
```

## Run jobs locally

```bash
python -m letsgau run --job events --dry-run --limit-places 3
```

`--job` accepts `auto` (only due jobs), `geo`, `discovery`, `events`, `build`
or `all`. `--dry-run` writes nothing to `data/`, `geo/` or `site/`.
`letsgau run` reads `.env` from the current directory (existing environment
variables win).

## Geo stage

```bash
python -m letsgau run --job geo --dry-run
```

```bash
python -m letsgau run --job geo
```

- Writes `data/places.json`, `data/state.json` and `geo/isochrone.geojson`
  (gitignored on `main`; the bot commits them to the `data` branch in Phase 6).
- Skips everything if center, drive time and place filters are unchanged; such a
  run needs no `ORS_API_KEY`. `--force` recomputes anyway.
- A changed center or drive time fetches a new isochrone and sets
  `force_discovery` in `state.json`. Changing only `place_types`,
  `include_places` or `exclude_places` reuses the stored isochrone.
- Warnings name `include_places` that were not found and `exclude_places` that
  were not in the list. Check the printed list for plausibility after a change.
- Each place gets its municipality (OSM `admin_level=8`) from the same Overpass
  query; the event crawl batches by municipality. A `places.json` without
  municipalities is refreshed once with the stored isochrone.
- Membership comes from the isochrone, drive times from the matrix; at the
  edge a place can show slightly more than `drive_time_minutes` (e.g. 31 min
  for 30). For Schongau, 30 minutes give about 105 places, 5 minutes only
  Schongau itself (live check 2026-11-08).
- Requests per recomputation: 1 isochrone, 1–2 Overpass queries, 1 matrix
  request per 500 places. Overpass queries take about 30–40 s.

### Live check on GitHub

*Actions → Geo check → Run workflow* (input: drive time, default 30) runs the
geo stage as a dry run with the `ORS_API_KEY` secret, prints the place list in
the job summary and uploads freshly recorded fixtures (`geo-fixtures`
artifact). It also runs on pull requests that change the workflow file.

### External services

| Service | Default URL | Override |
| --- | --- | --- |
| OpenRouteService (HeiGIT) | `https://api.heigit.org/openrouteservice` (`/v2/isochrones/driving-car`, `/v2/matrix/driving-car`) | `ORS_BASE_URL` |
| Overpass API | `https://overpass-api.de/api/interpreter`, then `overpass.private.coffee`, then `maps.mail.ru` (first that answers) | `OVERPASS_URL` (comma-separated list) |

The ORS key goes into the `Authorization` header. HeiGIT deprecated the old host
`api.openrouteservice.org` in 2026 and moved key management to
[account.heigit.org](https://account.heigit.org); existing keys work on the new
host. Every request is logged to stderr with status and duration (`geo: POST ...`).
Daily and per-minute quotas are shown in the HeiGIT dashboard; a daily
limit answers `403`, the per-minute limit `429` (retried with back-off). Very
long drive times may exceed the isochrone range allowed by the free plan; ORS
then returns an error that the stage prints.

## Event crawl

```bash
python -m letsgau run --job events --dry-run --limit-places 3
```

- Needs `data/places.json` (run `--job geo` first) and the secret of the
  configured `ai.auth` mode.
- Prints every event and writes `data/events.json` and the usage to
  `data/state.json` (not with `--dry-run`). `--limit-places N` crawls only the
  N nearest municipalities.
- Places are grouped by municipality (`municipality` in `places.json`); the
  prompt lists each municipality with its districts, and events keep the exact
  district as their place.
- Warnings name dropped events with the reason (schema, window, place, tags,
  source), failed batches and whitelist sources that were not checked.
- Subscription mode runs `claude -p` (Claude Code CLI, `npm install -g
  @anthropic-ai/claude-code`) in a temporary directory with only WebSearch and
  WebFetch. `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` are removed from its
  environment so it never bills an API key. Runs count against the plan's usage
  limits; the cost shown is 0.
- API mode needs `ANTHROPIC_API_KEY`; the cost shown is an estimate from token
  prices and $10 per 1,000 searches.
- `LETSGAU_RECORD_DIR=dir` saves every raw AI answer (for test fixtures).
- The merge runs in the same stage (also with `--dry-run`, which then only
  prints its summary): `merge: N new, N updated (N by similarity), N cancelled,
  N postponed, N now unconfirmed, N excluded by topic, N past deleted`.
  Unclear duplicates and the topic check of new events cost one call to the
  merge model (Haiku) per run; if that call fails, unclear pairs stay separate.

### Check the sources

```bash
python -m letsgau sources --check
```

Lists every whitelist URL (templates expanded for the municipalities in
`data/places.json`) with its HTTP status. The *Crawl check* workflow runs it for
the 30-minute area and shows the result in the job summary.

### Live check on GitHub

*Actions → Crawl check → Run workflow* (input: number of places, default 3)
computes the places for the instance config and runs the crawl as a dry run
with `ORS_API_KEY` and `CLAUDE_CODE_OAUTH_TOKEN`. It also runs on pull requests
that change `prompts/events.md`, so every prompt change gets a dry run.

## Build the website

```bash
python -m letsgau run --job build
```

- Renders `site/` from `data/events.json` (and `data/places.json` for grouping
  by distance): `/<lang>/` (today) and `/<lang>/<YYYY-MM-DD>/` for
  `display_days` days in every language, plus `/` (language redirect).
  "Today" is the date in `site.timezone`.
- Links are relative, so the folder works on a custom domain and on
  `<owner>.github.io/<repo>/`; canonical and hreflang use `site.base_url`.
- Aborts if `config.yaml` still has placeholders or, in GitHub Actions,
  `site.owner` differs from the repository owner.
- `--dry-run` builds into a temporary folder instead of `site/`.
- Preview locally: `python -m http.server --directory site` and open
  `http://localhost:8000/de/`.

The *Site check* workflow builds the fixture day with 100 events and runs
Lighthouse (mobile; performance and accessibility must be ≥ 90) and a `curl`
check that every event is in the HTML.

## Run on GitHub (Phase 6)

*Actions → Run → Run workflow*, choose the job. The button is also available in
the GitHub mobile app. A `concurrency` group prevents overlapping runs.

## Secrets

| Secret | Needed for |
| --- | --- |
| `CLAUDE_CODE_OAUTH_TOKEN` | `ai.auth: subscription` (create with `claude setup-token`); must not be combined with `ANTHROPIC_API_KEY` in the same environment |
| `ANTHROPIC_API_KEY` | `ai.auth: api_key` |
| `ORS_API_KEY` | geo stage (OpenRouteService); free key from [account.heigit.org](https://account.heigit.org) |

Locally, put them into `.env` (see `.env.example`). In API mode, set a monthly
spend limit in the Anthropic Console.

## Costs

Every run will write tokens, searches and estimated costs to the GitHub job
summary (Phase 6).
