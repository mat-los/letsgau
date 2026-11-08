# Prompts

Versioned AI prompts, editable without code changes. Every change must be
tested with `python -m letsgau run --job <job> --dry-run` before merging.

| File | Step | Phase |
| --- | --- | --- |
| `discovery.md` | find and rate sources (Opus) | 7 |
| `events.md` | crawl events (Sonnet) | 2 |
| `merge.md` | duplicate tie-break and topic filter (Haiku) | 3 |
