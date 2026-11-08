# Operations

> Phase 1: `check` and the `geo` stage work. The other stages, the workflow and
> cost reporting are added in later phases; this page grows with them.

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
- Requests per recomputation: 1 isochrone, 1–2 Overpass queries, 1 matrix
  request per 500 places.

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

## Run on GitHub (Phase 6)

*Actions → Run → Run workflow*, choose the job. The button is also available in
the GitHub mobile app. A `concurrency` group prevents overlapping runs.

## Secrets

| Secret | Needed for |
| --- | --- |
| `CLAUDE_CODE_OAUTH_TOKEN` | `ai.auth: subscription` (create with `claude setup-token`) |
| `ANTHROPIC_API_KEY` | `ai.auth: api_key` |
| `ORS_API_KEY` | geo stage (OpenRouteService); free key from [account.heigit.org](https://account.heigit.org) |

Locally, put them into `.env` (see `.env.example`). In API mode, set a monthly
spend limit in the Anthropic Console.

## Costs

Every run will write tokens, searches and estimated costs to the GitHub job
summary (Phase 6).
