# Prompts

Versioned AI prompts, editable without code changes. Every change must be
tested with `python -m letsgau run --job <job> --dry-run` before merging; for
`discovery.md` and `events.md` the *Crawl check* workflow does that on the pull
request (discovery into a throwaway `data/`, then the crawl as a dry run).

Prompts are Jinja2 templates. The answer format is not described in the prompt
but enforced by a JSON schema (`crawl.answer_schema`).

| File | Step | Phase |
| --- | --- | --- |
| `discovery.md` | find and rate sources per batch of municipalities and focus pass (Opus) | 7 ✓ |
| `events.md` | crawl events (Sonnet); rendered per call: `sources` sweep, open `search`, or combined `all` | 2 ✓ |
| `merge.md` | duplicate tie-break and topic filter (Haiku), one call per run | 3 ✓ |
