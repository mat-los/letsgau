# Prompts

Versioned AI prompts, editable without code changes. Every change needs a live
test: `python -m letsgau run --job <job> --dry-run` locally, the manual *Crawl
check* workflow (discovery into a throwaway `data/`, then the crawl as a dry
run), or, while testing a small area, a real *Run* after merging. The check is
manual because it uses about as much of the plan as a real run and its results
are thrown away.

Prompts are Jinja2 templates. The answer format is not described in the prompt
but enforced by a JSON schema (`crawl.answer_schema`).

| File | Step | Phase |
| --- | --- | --- |
| `discovery.md` | find and rate sources per batch of municipalities and focus pass (Opus) | 7 ✓ |
| `events.md` | crawl events (Sonnet); rendered per call: `pages` (source texts, no web), `sources` sweep, `flyers`, `instagram`, `recheck`, open `search`, or combined `all` | 2 ✓ |
| `merge.md` | duplicate tie-break and topic filter (Haiku), one call per run | 3 ✓ |
