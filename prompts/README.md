# Prompts

Versioned AI prompts, editable without code changes. Every change needs a live
test: `python -m letsgau run --job <job> --dry-run` locally, the manual *Crawl
check* workflow (discovery into a throwaway `data/`, then the crawl as a dry
run), or, while testing a small area, a real *Run* after merging. The check is
manual because it uses about as much of the plan as a real run and its results
are thrown away.

Prompts are Jinja2 templates. The answer format is not described in the prompt
but enforced by a JSON schema (`crawl.answer_schema`).

The model of each step is set in `models` in `instance/config.yaml` (Schongau:
Sonnet for discovery and events, Haiku for the merge and the pre-screen).

| File | Step | Rendered by |
| --- | --- | --- |
| `discovery.md` | find and rate sources per batch of municipalities and focus pass (`models.discovery`) | `discovery.render_prompt` |
| `events.md` | crawl events (`models.events`); one mode per call: `pages` (source texts, no web), `sources` (agent sweep), `flyers`, `instagram`, `recheck`, open `search`, or combined `all` | `crawl.render_prompt` |
| `merge.md` | duplicate tie-break and topic check (`models.merge`), one call per run | `merge.render_prompt` |
| `instagram_screen.md` | Instagram pre-screen: drop posts whose caption is clearly no event before their images are read (`models.merge`) | `instagram.prescreen_posts` |
| `instagram_session.md` | not used by the pipeline: the task of the scheduled Claude Code session that looks up venue accounts and proposes them in `instance/sources/instagram_candidates.yaml` | (scheduled session) |
