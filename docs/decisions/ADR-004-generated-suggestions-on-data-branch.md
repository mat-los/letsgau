# ADR-004: Source suggestions on the data branch, sources.json for discovery

- Status: accepted
- Date: 2026-11-08

## Context

The concept has the discovery job write `sources/suggested.md`, which the
workflow commits. Everything under `instance/` lives on `main`, but the bot must
never commit to `main` (ADR-001, CLAUDE.md).

## Decision

- Discovery writes `data/suggested.md` next to `data/sources.json` on the
  `data` branch. `instance/sources/suggested.md` on `main` only explains where
  to find it. Accepting a suggestion stays a manual copy into
  `whitelist.yaml` / `blacklist.yaml` on `main`.
- `data/sources.json` holds every known source with origin (`whitelist`,
  `discovery`, `crawl`), status, rating, update frequency and hit statistics.
  The events stage gives each batch the active sources of its municipalities
  with rating ≥ 3 or at least 2 hits, and counts hits per source after every
  run; unknown primary sources are added with `origin: crawl`.

## Consequences

`main` stays free of bot commits. The suggestions are one click away on the
`data` branch; the planned crawler-report issue (backlog #29) can replace the
file later.
