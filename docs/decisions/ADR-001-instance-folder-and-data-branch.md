# ADR-001: Instance folder and data branch

- Status: accepted
- Date: 2026-11-08

## Context

The repository is both the engine and Matthias' Schongau instance. Forks must
not inherit his configuration, data or imprint.

## Decision

- Everything instance-specific lives under `instance/` (`config.yaml`,
  `sources/`, `content/`). Templates with placeholders live in
  `examples/instance/`; `letsgau init` copies them over `instance/`.
- Generated data (`data/`, `geo/`) is committed by the bot to a separate `data`
  branch, never to `main`. Forks only copy `main` by default.
- The build aborts if placeholders remain or `site.owner` differs from
  `GITHUB_REPOSITORY_OWNER`.

## Consequences

`main` history stays clean. The workflow must check out the `data` branch
alongside `main` (Phase 6). A split into engine and instance repositories is
deferred until others use the project seriously.
