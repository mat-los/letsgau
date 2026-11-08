# Crawl fixtures

AI answers replayed by `tests/test_crawl.py` and `tests/test_ai_runner.py`; the
tests never call an AI.

| File | What |
| --- | --- |
| `claude_cli_recorded_schongau.json` | **Recorded**: real output of `claude -p --output-format json --json-schema …` from the *Crawl check* workflow (2026-11-08, Schongau, `claude-sonnet-5-5`). Trimmed to the fields the runner reads plus usage. |
| `claude_cli_result.json` | Hand-written CLI output in the same shape, with ten candidates that exercise the validation rules (window, place, tags, blacklist, program item, cancelled, postponed). |
| `api_messages.json` | Hand-written Claude API messages (`pause_turn`, then the `submit_result` tool call) in the documented format; no API key was available for a recording. |

Record new answers with `LETSGAU_RECORD_DIR=dir python -m letsgau run --job events --dry-run`
or from the artifact / log of the *Crawl check* workflow.
