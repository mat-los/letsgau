# ADR-003: AI runner, structured output and the Phase 2 merge

- Status: accepted
- Date: 2026-11-08

## Context

The event crawl needs web search and page fetches by Claude and a reliable JSON
answer. It runs in two modes: Claude Code with a subscription token
(`CLAUDE_CODE_OAUTH_TOKEN`, default) or the Claude API (`ANTHROPIC_API_KEY`).
The subscription token may only be used by the `claude` CLI.

## Decision

- **Subscription:** `claude -p --output-format json --json-schema <schema>`
  with `--tools WebSearch,WebFetch`, `--permission-mode dontAsk`,
  `--no-session-persistence`, `--strict-mcp-config`, run in a temporary
  directory (no repository `CLAUDE.md`). Blacklisted domains become
  `WebFetch(domain:…)` deny rules (WebSearch cannot be restricted by domain).
  `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` are removed from the CLI's
  environment because they would take precedence over the OAuth token. `--bare`
  is not used: it ignores the OAuth token.
- **API:** `web_search_20260209` (`max_uses`, `blocked_domains`) and
  `web_fetch_20260209` (`blocked_domains`) plus a strict client tool
  `submit_result` whose input is the answer. A tool instead of
  `output_config.format` keeps the answer compatible with server-tool results
  and citations; `pause_turn` is resumed; `fallbacks: "default"` handles
  refusals.
- **One schema** (`crawl.answer_schema`) for both modes: every field required,
  nulls allowed, tags as an enum from `config.yaml`. Pydantic validates again,
  then the config rules (window, place, tags, sources) are checked in Python.
- **Merge in Phase 2** is id-based (start date + slug of title and place) so the
  events stage already writes a usable `events.json`; Phase 3 adds fuzzy
  matching, the Haiku tie-break and the status lifecycle.

## Consequences

The prompt describes the task, not the format. Search limits are hard in API
mode and advisory in subscription mode. Fixtures for tests are recorded with
`LETSGAU_RECORD_DIR` from real runs (*Crawl check* workflow).
