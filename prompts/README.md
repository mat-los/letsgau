# Prompts

Versioned AI prompts, editable without code changes. Every change must be
tested with `python -m letsgau run --job <job> --dry-run` before merging; for
`events.md` the *Crawl check* workflow does that on the pull request.

Prompts are Jinja2 templates. The answer format is not described in the prompt
but enforced by a JSON schema (`crawl.answer_schema`).

| File | Step | Phase |
| --- | --- | --- |
| `discovery.md` | find and rate sources (Opus) | 7 |
| `events.md` | crawl events (Sonnet); Jinja2 template rendered per batch | 2 ✓ |
| `merge.md` | duplicate tie-break and topic filter (Haiku), one call per run | 3 ✓ |
