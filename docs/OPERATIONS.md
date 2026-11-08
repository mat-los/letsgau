# Operations

> Phase 0: only `check` works. Running stages, the workflow and cost reporting
> are added in later phases; this page grows with them.

## Validate the configuration

```bash
python -m letsgau check
```

## Run jobs locally

```bash
python -m letsgau run --job events --dry-run --limit-places 3
```

`--job` accepts `auto` (only due jobs), `geo`, `discovery`, `events`, `build`
or `all`. `--dry-run` writes nothing to `data/` or `site/`.

## Run on GitHub (Phase 6)

*Actions → Run → Run workflow*, choose the job. The button is also available in
the GitHub mobile app. A `concurrency` group prevents overlapping runs.

## Secrets

| Secret | Needed for |
| --- | --- |
| `CLAUDE_CODE_OAUTH_TOKEN` | `ai.auth: subscription` (create with `claude setup-token`) |
| `ANTHROPIC_API_KEY` | `ai.auth: api_key` |
| `ORS_API_KEY` | geo stage (OpenRouteService) |

Locally, put them into `.env` (see `.env.example`). In API mode, set a monthly
spend limit in the Anthropic Console.

## Costs

Every run will write tokens, searches and estimated costs to the GitHub job
summary (Phase 6).
