# Efficiency review, 2026-11-13

A review of code size, AI call efficiency, documentation and data quality.
Inputs: `main` at `e334856`, the `data` branch at `abc0f1b`, the logs of
*Run: events* #41 (2026-11-13 00:25, run id 43336977334) and *Run: discovery*
#40 (2026-11-13 00:01, run id 43333596230), and `data/state.json`.

## Status

| Item | State |
| --- | --- |
| F1 + F5, F2, F3, O5 | done in PR #104 (merged) |
| O1, O2, O6, F4 (light: `depth.open_search`, postcodes), F7 | done in the follow-up PR |
| F6, F8, F9, O3, O4 | open (judged low priority for now) |
| F4 full (count the events only the open search found) | open |
| `docs/CONCEPT.md` still says Opus for discovery | waiting for the owner's decision |

None of the done items has been live-tested yet (no Crawl check, no real run):
compare the first real runs with the baseline below.

Every item has an ID. Items in *OK* and *Needs fixing* end with
**Instructions for Claude Code**, written so that a later session can pick up
one item per pull request. The general rules in `CLAUDE.md` still apply:
English everywhere, a `CHANGELOG.md` line, `ruff` and `pytest` green, a
schema change needs a migration of the JSON files on `data`, and a prompt
change needs a live test (manual *Crawl check* workflow) before or right after
merging.

## Key numbers

| What | Value |
| --- | --- |
| Python code | 7,332 lines in `src/letsgau` (crawl 1,785, discovery 1,138, build 738, merge 629, ai_runner 502) |
| Tests | 239 tests, about 11 s, no AI calls |
| Events run #41 | 22 AI calls: 16 source sweeps, 4 re-checks, 1 open search (all Sonnet), Instagram pre-screen, merge (Haiku) |
| | 1.96 M input tokens, 234 k output tokens, 9 searches, **$2.01** API-equivalent, 743 s |
| | 159 candidates → **2 new**, 129 updated (68 of them only matched by title similarity), 7 excluded |
| Source sweeps | ≈ $1.60 of the $2.01; 5–11 turns each |
| Re-checks | 4 calls, $0.26 |
| Open search | 6 searches, $0.18, **0 new events** ("results were mostly other places such as Altenstadt in Hessen") |
| Discovery run #40 | 3 calls, 816 k input tokens, 24 searches, $0.80, 5 new sources; two of three passes stopped after 2 and 5 of 20 searches |
| Runs in 5 days | 15 events runs, 9 discovery runs (mostly manual tests) ≈ $35 API-equivalent |
| `sources.json` | 262 entries; **198 never had a hit**; 187 rated 1–2; the 15 most productive domains are the primary source of 88 % of all events |
| `events.json` | 137 events; Instagram is the only source of 17 (12 %), flyers of 1 |

At about $2 per events run for **two** municipalities, the planned 30-minute
area (about 44 municipalities) would cost around $40 per run, or $350 a month
at two runs a week. The plan's limits would stop it long before that. The
biggest lever is not re-reading unchanged pages with an agent (F1).

---

## Very good

- **G1. Architecture.** No server: GitHub Actions, JSON on a bot-only `data`
  branch, a static site on Pages. Job frequencies are one line in
  `config.yaml`, and the scheduler decides what is due.
- **G2. The AI runner.** `ai_runner.py` keeps both auth modes behind one
  `Runner` protocol. Answers are forced into a JSON schema, only
  WebSearch/WebFetch (or Read on a temp folder) are allowed, API keys are
  stripped in subscription mode, and the CLI runs outside the repo so
  `CLAUDE.md` is not loaded. Raw answers and CLI transcripts are recorded, and
  usage is summed per call into the job summary and `state.json`.
- **G3. Tests never call an AI.** Recorded fixtures, 239 tests in about 11 s.
- **G4. Validation of AI output in code.** `crawl.to_event` checks window,
  place, tags, primary tag and source, and the blacklist. The model cannot
  write arbitrary data.
- **G5. The flyer cache.** Files are keyed by SHA-256. In run #41 all 21 flyer
  files were "already read", so no AI call was made. This is the pattern F1
  should copy for HTML pages.
- **G6. Instagram via the Graph API**, with a Haiku caption pre-screen
  ($0.00). It is cheap and adds 12 % unique events.
- **G7. Re-checks skip what the sweeps confirmed** (8 of 20, 12 of 20, 16 of
  20 in run #41).
- **G8. The merge.** Deterministic first, then one Haiku call per run
  ($0.01). Status lifecycle and cleanup are clear.
- **G9. Configuration.** Strict Pydantic models with file, line and field in
  errors. Every config field appears in `docs/CONFIGURATION.md` (checked
  programmatically for this review).
- **G10. Process.** Conventional commits, a CHANGELOG per PR, ADRs for the
  big decisions.

---

## OK, could be better

### O1. `crawl.py` and `discovery.py` are too big and mix concerns

`crawl.py` (1,785 lines) also contains the flyer orchestration (about 130
lines, `_run_flyer_call`, cache handling) and the Instagram orchestration
(about 385 lines, `_check_listed_candidates` … `_run_instagram_call`).
`discovery.py` (1,138 lines) contains the Instagram lookup and the candidate
verification (about 230 lines). Small helpers are duplicated:

| Helper | Copies |
| --- | --- |
| `domain_of`, `is_blocked` | `crawl.py`, `discovery.py` |
| `_UMLAUTS` | `crawl.py`, `merge.py` |
| Jinja `Environment(FileSystemLoader(PROMPTS_DIR), StrictUndefined, …)` | 4 in `crawl`/`discovery`/`merge` |
| `_load`, `_write_json` | `crawl.py`, `geo.py` (and inline in `discovery.py`, `ai_runner.py`) |
| `_log` | 6 modules |
| `DATA_DIR` / `PROMPTS_DIR` constants | 5 / 3 modules |

**Suggestion:** move the flyer orchestration into `flyers.py` and all
Instagram orchestration (crawl and discovery parts) into `instagram.py`. Add a
small `util.py` with `domain_of`, `is_blocked`, `UMLAUTS`, `prompt_env()`,
`load_json(path, model)`, `write_json(path, data)`, `log(prefix)`,
`DATA_DIR`, `PROMPTS_DIR`. Expected: about −150 lines, and `crawl.py` under
1,200.

**Instructions for Claude Code:**
1. Pure refactor, no behaviour change: do not touch prompts, schemas or data.
2. Create `src/letsgau/util.py` (module docstring) with the helpers above.
   Replace all copies and keep the public names that tests import (for example
   `crawl.domain_of`) as re-exports, or update the tests.
3. Move `_run_flyer_call`, `_rekey`, `FLYER_CACHE_*` and `load_flyers` to
   `flyers.py`. Move `_check_listed_candidates`, `_mine_instagram`,
   `_prune_instagram`, `_prescreen_posts`, `_run_instagram_call`,
   `screen_schema` and `INSTAGRAM_*` to `instagram.py`. Move discovery's
   candidate code (`collect_candidates`, `listed_candidates`,
   `verify_candidates`, …) there too. Watch the import cycle: `discovery`
   imports `crawl`, so `instagram` must not import `crawl` at module level.
4. Acceptance: `ruff check . && ruff format --check . && pytest` green, the
   same test count, and `wc -l src/letsgau/crawl.py` < 1,250. Update the module
   table in `docs/ARCHITECTURE.md` and the file map in `CLAUDE.md`.

### O2. Paths that are switched off or not needed in production

- `instagram.ai_lookup` is `false`, and the docs say its search "rarely shows
  usernames". The lookup call (`LOOKUP`, `lookup_schema`,
  `render_lookup_prompt`, the branch in `discovery.run`) and
  `prompts/instagram_lookup.md` are dead weight. The scheduled session
  (`prompts/instagram_session.md`) replaced it.
- The `anthropic` SDK is a hard dependency and is installed on every
  workflow run, although `ai.auth` is `subscription`. `ApiRunner` (about 150
  lines) is a documented option, so keep it, but as an extra.
- `depth.crawl_sources_per_request: 0` (one combined call, the `else` branch
  of `prompts/events.md`) and `discovery_focus_passes: false` are only
  exercised by tests. They are meant for large areas, where F1 would change
  the design anyway.

**Suggestion:** delete the AI lookup call. Move `anthropic` to
`[project.optional-dependencies] api`. Decide after F1 whether the combined
crawl mode is still needed.

**Instructions for Claude Code:**
1. Remove `instagram.ai_lookup` from the config model, `instance/config.yaml`,
   `examples/instance/config.yaml` and `docs/CONFIGURATION.md`. With
   `extra="forbid"`, an old config would then fail, so mention this in the
   CHANGELOG under *Removed*. Remove the lookup code in `discovery.py`,
   `prompts/instagram_lookup.md` and its tests and fixtures. Update
   `docs/OPERATIONS.md` (*Discovery → Instagram*), `prompts/README.md` and
   the `CLAUDE.md` file map.
2. In `pyproject.toml`, move `anthropic` to an `api` extra. Import it lazily
   (already done in `ApiRunner.client`). If `ai.auth: api_key` is set and the
   import fails, raise `AiError("pip install 'letsgau[api]'")`. Workflows:
   `pip install -e .` for subscription; README: `pip install -e '.[api]'` for
   API mode. Tests that use the SDK types (`tests/test_ai_runner.py` imports
   `anthropic.types.beta`) need `[dev]` to include `anthropic`.
3. No prompt change is involved (the lookup prompt is deleted, not changed),
   so no live test is needed.

### O3. The merge re-checks every stored event against the topics on every run

This is cheap ($0.01) but takes 53 s and grows with the calendar.

**Suggestion:** check a stored event against the topics only when it is new,
or when `excluded_topics` changed. Store a fingerprint of the topic list in
`state.json` (`topics_fingerprint`). If the fingerprint is unchanged, send
only new events.

**Instructions for Claude Code:**
1. `models.State`: add `topics_fingerprint: str | None = None`. The default
   keeps old files valid; still document it in `docs/DATA_MODEL.md`.
2. In `crawl.run`, compute `sha256` of the joined `config.excluded_topics`.
   Pass `check_stored = fingerprint != state.topics_fingerprint` to
   `merge.apply`. When it is false, `decide` gets only `new_events`. Write the
   fingerprint after a successful merge, and never on a dry run.
3. Tests: a stored event is not sent when the fingerprint matches; all are
   sent after a topic change.

### O4. Discovery yields little per call, and its passes stop early

In run #40 the official pass used 5 of 20 searches and the clubs pass 2 of 20
("cut short"). Coverage reports "not_checked: out of effort budget" for 12
categories. The venues pass ran six `site:instagram.com` searches that the
tool ignored, although `docs/OPERATIONS.md` describes this as a feature.
Every call lists all 248 known sources.

**Suggestion:**
- Give each pass only the categories that `suggested.md` coverage reports as
  gaps (or not checked) in the last run, plus one rotating "fresh look"
  category. Stop a pass that has no gaps.
- Pass known sources as a compact domain list (`domain: rating`), not full
  entries.
- Remove the `site:instagram.com` queries from the venues checklist (the
  scheduled session covers Instagram).

**Instructions for Claude Code:**
1. This changes `prompts/discovery.md`, so it needs a live *Crawl check* run
   (its discovery step) before merging.
2. Store the last coverage per municipality and category in `sources.json`
   (new top-level `coverage` list; schema change → migrate the file on `data`
   and document it in `docs/DATA_MODEL.md`), or derive it from
   `suggested.md`'s inputs.
3. In `discovery.py`, build each pass's checklist only from categories with
   status `nothing_found`/`not_checked`, or never checked, or checked longer
   ago than `RECHECK_SOURCES_DAYS`. Skip a pass with an empty checklist (log
   it).
4. Render known sources as `domain (rating)` lines, grouped and deduplicated
   by domain.
5. Acceptance: on the current data a discovery run makes ≤ 2 calls for
   Schongau + Altenstadt; compare usage in the job summary with run #40.

### O5. Workflows: deprecated actions and an unpinned CLI

The logs warn that `actions/checkout@v4`, `setup-python@v5`, `setup-node@v4`
and `upload-artifact@v4` target Node 20 (deprecated). `npm install -g
@anthropic-ai/claude-code` installs whatever is latest. The pipeline depends
on its flags (`--json-schema`, `--tools`, `--setting-sources`,
`structured_output`, `modelUsage`), so a CLI release can break a scheduled run
without any change in the repo.

**Instructions for Claude Code:**
1. Bump the actions to their current majors in all workflows (`ci`, `run`,
   `crawl-check`, `geo-check`, `site-check`).
2. Pin the CLI: add `CLAUDE_CODE_VERSION` as a workflow `env` (a version that
   is current and works) and `npm install -g
   @anthropic-ai/claude-code@$CLAUDE_CODE_VERSION`, in `run.yml` and
   `crawl-check.yml`. Document how to bump it in `docs/OPERATIONS.md`
   (*Run on GitHub*).
3. Optional: add `.github/dependabot.yml` for `github-actions` and `pip`.

### O6. Documentation: mostly current, a few files outdated

`OPERATIONS.md`, `CONFIGURATION.md` and `DATA_MODEL.md` are detailed and
mostly current (flyers, Instagram, sweeps and re-checks are covered).
Outdated:

| File | Problem |
| --- | --- |
| `docs/ARCHITECTURE.md` | says discovery uses Opus (config: Sonnet); no flyers, Instagram, source sweeps, re-checks or open search; the sequence diagram shows one crawl step; the module table lacks `flyers`, `instagram`, `feeds` details |
| `prompts/README.md` | discovery "(Opus)"; `instagram_lookup.md`, `instagram_screen.md` and `instagram_session.md` are missing from the table |
| `README.md` | "Status … see docs/ISSUES.md" and `<!-- Screenshot will be added in Phase 4. -->` are leftovers |
| `docs/ISSUES.md` | a one-off setup script for milestones and labels; obsolete as a working document |
| `docs/OPERATIONS.md` | *Discovery*: `site:instagram.com` searches (ignored by the tool, see O4); *Usage*: cost numbers for Opus with 30 searches |
| `docs/decisions/ADR-005` | the cost estimate ("3–5 crawl calls, $2") no longer matches 22 calls |
| `docs/CONCEPT.md` | discovery with Opus (`claude-opus-5-5`); **do not edit without the owner's OK** (source of truth) |

**Instructions for Claude Code:**
1. Rewrite `docs/ARCHITECTURE.md` from the code: container diagram with the
   Graph API and the flyer download; sequence diagram with source sweeps,
   flyer calls, re-checks, open search, Instagram step and merge; module table
   with every module in `src/letsgau`; models from `instance/config.yaml`.
   Check every Mermaid diagram renders (GitHub preview or the Mermaid CLI).
2. Update `prompts/README.md` (every file in `prompts/`, model per step from
   the config) and remove the leftovers in `README.md`.
3. Move `docs/ISSUES.md` to `docs/archive/ISSUES.md`, or delete it after
   asking the owner. Fix the links in `README.md` and `CLAUDE.md`.
4. Add a short *Status* note under ADR-005's consequences pointing to this
   review. ADRs are not rewritten.
5. Ask the owner whether `docs/CONCEPT.md` should say Sonnet for discovery.
6. Do this item **after** F1–F3 if they are scheduled, because they change
   the crawl described here.

---

## Needs fixing

Ordered by impact. F1–F3 together should cut an events run by roughly
60–80 % of tokens and calls.

### F1. Every run re-reads every source page with an agent, even when nothing changed

**Evidence:** 16 sweep calls of 5–11 turns each read the same ~60 pages on
every run (≈ $1.60 of $2.01). 129 of 131 matched events were already known.
WebFetch in Claude Code passes pages through a summarising helper model, which
loses data. The run notes show it: "vhs … only loaded as far as pages 1-3",
"evangelische-termine list showed only up to 28.11.", "meinestadt.de showed
only the first 20 events", "dates appear only in poster images". The agent's
multi-turn loop resends the growing context on every turn, which is where the
1.96 M input tokens come from.

**Suggestion:** fetch and cache pages in Python, the way flyers already work
(G5):
1. For every sweep source, download the page (and its known pagination) with
   `flyers.http_fetch` and `flyers.Robots`. Extract the main text (strip
   `script`/`style`/`nav`/`footer`, collapse whitespace, keep link URLs of
   detail pages). Hash the text.
2. If the hash equals the stored one and the cache version matches, reuse the
   events stored for that page: no AI call.
3. Changed pages are sent **as text in the prompt** to a call without web
   tools (`web=False`, few turns). Group several changed pages per call, up to
   a token budget (for example 40 k characters).
4. Pages that cannot be read this way (JavaScript calendars such as the
   schongau.de calendar, login walls, empty text) are marked `needs_agent` and
   keep the current agent sweep.

**Instructions for Claude Code:**
1. Read `crawl.py` (`plan_calls`, `crawl`, `_run_flyer_call`), `flyers.py`
   and `prompts/events.md` first.
2. New module `src/letsgau/pages.py` (docstring) with:
   `fetch_text(url, fetch, robots) -> PageText(url, text, links, sha256)`,
   using only the standard library `html.parser` (no new dependency).
   Pagination: follow `rel="next"` and links whose text is `weiter`, `»`,
   `nächste` or a page number, up to `MAX_PAGES = 8`. Stop when the page
   shows dates past the crawl window.
3. Cache file `data/pages.json`
   (`{schema_version, updated_at, pages: {url: {sha256, version, last_changed,
   last_seen, mode: "text"|"needs_agent", events: [...]}}}`). Add a model in
   `models.py`, document it in `docs/DATA_MODEL.md`, and start with an empty
   file (a new file needs no migration). Keep the stored events like
   `FlyerCacheEntry.events`, so unchanged pages yield them again with today's
   `last_seen`.
4. Prompt: add a mode `pages` to `prompts/events.md` that lists
   `### <url>` + text blocks and says "no web access; take every event between
   … from these texts". Reuse the existing rules section. **Prompt change → a
   live *Crawl check* before merging**, and compare the events found with a
   normal run on the same day (expect ≥ 95 % of the events the sweeps found).
5. `plan_calls`: text-mode sources go into `pages` calls; `needs_agent`
   sources keep `sources` sweeps. A page becomes `needs_agent` when its text
   has fewer than 200 characters or no date pattern (`\d{1,2}\.\d{1,2}\.`).
   Re-try text mode every 30 days.
6. Log per run: `pages: N unchanged (reused), N changed (AI), N agent`.
7. Tests with recorded HTML fixtures (no network): unchanged page → no AI
   call and the stored events come back; changed page → one call;
   pagination stops at the window; robots.txt is respected.
8. Acceptance in the next real run: ≤ 8 AI calls for the sweeps, input tokens
   < 600 k, cost < $0.80, and no drop in event count compared with run #41.

### F2. 234 k output tokens per run: every known event is written out again in full, in two languages

**Evidence:** 159 candidates, each with title and description in DE and EN,
tags, sources, schedule. Only 2 were new. Output tokens cost 5× input tokens.
68 of the 129 updates matched only by title similarity, because the model
re-titled known events ("Film: Chopin…" vs "Chopin… (Lagerhauskino)"), which
also feeds the duplicate problem.

**Suggestion:** give every sweep or page call the known events of its sources
as `id | date | time | title`. Ask for two lists:
- `known`: `{id, status, changes}` for known events that were seen (changes
  only when date, time, place or status differ);
- `new`: full event objects for events not in the list.

Optionally generate the English texts only for new events, in the merge's
Haiku call, instead of in every crawl answer.

**Instructions for Claude Code:**
1. Depends on nothing, but is easier after F1 (fewer call types).
2. `crawl.answer_schema`: add `known_seen: [{id, status, start_time,
   start_date, place}]` (nullable fields = unchanged). Keep `events` for new
   ones. In `events_from_answer`, turn each `known_seen` item into an
   "observation" that `merge.combine` applies to the stored event (update
   `last_verified`, `misses = 0`, append the source, apply changed fields).
   Ids not in the call's list are ignored and logged.
3. `render_prompt`: for `sources`/`pages` calls, list the known events whose
   sources match the call's sources (by `discovery.normalize_url` prefix).
   Remove the `later_events` block where `known_seen` makes it redundant.
4. Prompt change → live test. Acceptance: output tokens of an events run
   ≤ 100 k; "updated by similarity" in the merge summary ≤ 15.
5. The data schema of `events.json` does not change.

### F3. Sources that never yield anything are swept on every run

**Evidence:** sweep selection (`discovery.known_sources_for`) takes every
active source with rating ≥ 3 or ≥ 2 hits, with no limit in sweep mode, and
nothing ever demotes a source. Run #41 swept pages that returned nothing
again: kinoprogramm.com (404), lagerhauskino.pfaffen-winkel.net (DNS error),
tango-oberland.de, tickets.item-events.de, schongauer-hexennacht.de,
kulze.info, schongau-belebt.de, brauhaus-schongau.reservix.de,
musik-zum-tagesausklang.de, the flea-market pages (only 2027 dates) and the
schongau.de calendar (JavaScript, never readable). `sources.json` holds 262
entries, 198 without a single hit and 187 rated 1–2 (swipein.restaurant,
golocal, a 2016 yumpu brochure, wanderlog, …). It also holds near-duplicates:
`rce-event.de …os=0` and `…os=10`, three host variants of the Lagerhauskino,
six meinestadt.de URLs. `hits` counts every sighting on every run, so it
measures run frequency more than value.

**Suggestion:**
- Track the result per source and run (`last_result: events|empty|unreachable`,
  `empty_runs`). Leave a source out of sweeps after 3 empty or 2 unreachable
  runs in a row (`status: dormant`). Discovery re-rates dormant sources
  monthly. The whitelist is never made dormant, only warned about.
- Collapse URL variants: same domain + same path, different pagination query
  → one source with a `note` about the pagination.
- Prune: sources rated 1–2 with no hit for 90 days move to a compact
  `rejected` list (`domain`, `path`, `reason`, `date`) that discovery uses to
  avoid suggesting them again.

**Instructions for Claude Code:**
1. Schema change of `data/sources.json`: add `last_result`, `empty_runs` and
   status `dormant` to `models.Source`, and a top-level `rejected` list.
   Write a migration script (for example `python -m letsgau migrate sources`,
   or a one-off script in the PR description) and run it on the `data` branch
   in the same PR. Update `docs/DATA_MODEL.md`.
2. Fill `last_result` from each call's `checked_sources`
   (`events_found`/`no_events`/`unreachable`), matched to sources with
   `crawl._same_source`. Update it in the same place as `record_hits`.
3. `known_sources_for`: skip `dormant`. `discovery`: list dormant sources for
   re-rating; a rating ≥ 3 makes one active again with `empty_runs = 0`.
4. Collapse variants in `discovery.merge_answer` and once in the migration.
5. Log per run: `sources: N swept, N dormant, N newly dormant (<urls>)`.
6. Acceptance: on the current data the migration leaves ≤ 80 entries in
   `sources`. The next events run sweeps ≤ 40 sources without losing events
   from the top 15 domains.

### F4. The open search is not worth its call

**Evidence:** run #41 made 6 searches and found 0 new events. Results were
dominated by aggregators already covered and by places with the same name
(Altenstadt in Hessen). The checklist has about 25 queries per municipality,
and a call runs only a part of it.

**Suggestion:** add the postcode or "Oberbayern" to every query
(`"Altenstadt 86972 Veranstaltungen"`, `"Schongau Oberbayern Konzert"`). Run
the open search only on every second events run, or only for municipalities
whose discovery coverage has gaps. Record in the run summary how many events
the search call added that no sweep found. If that stays 0 for 4 runs, the
default should be off.

**Instructions for Claude Code:**
1. `crawl.search_queries`: append ` {postcode}` (from `region.municipalities`,
   when set) to each query. Keep the checklist rotation.
2. New option `depth.open_search: "every_run" | "alternate" | "off"` (default
   `"alternate"`), documented in `docs/CONFIGURATION.md` and commented in both
   config files. "alternate" uses the same week-parity logic as
   `rotate_queries`.
3. After the merge, count events whose only sources came from the search call
   and print them in the summary (`open search: N new events only found
   there`).
4. The query text is code, not a prompt file, but it changes what the model
   does: run a live *Crawl check*.

### F5. Re-checks cost a call per 20 events and duplicate the sweep work

**Evidence:** 4 calls, $0.26, for events whose sources the sweeps had mostly
just read.

**Suggestion:** with F1, an event is confirmed without AI when its primary
source page is unchanged and its cached events still contain it. A changed
page is re-read anyway. Only events whose primary source is not a sweep
source (found by the open search or on Instagram) need an AI re-check, and
only within `deep_check_days`.

**Instructions for Claude Code:**
1. Do this after F1. In `crawl.crawl`, before planning `recheck` calls,
   remove events whose primary source URL is in `pages.json` with `mode:
   text`. They are confirmed by the page cache: mark them found, so the merge
   does not count a miss.
2. Keep the existing "confirmed by the source reading" filter for the rest.
3. Tests: an event on an unchanged cached page is neither re-checked nor
   missed.

### F6. Events filed under the wrong place

**Evidence (this week's cleanup):** both VdK Nikolausfeier entries were at the
Jakob-Pfeiffer-Haus (Schongau), and one was filed under Altenstadt because the
regional calendar's Altenstadt page listed it. "Silvester im Gasthof Janser"
had Schongau as its place (the inn is in Schwabniederhofen, Altenstadt).
Gospel-choir events in Herzogsägmühle (Peiting, outside the area) were filed
under Schongau. A wrong place blocks duplicate detection and can put events
from outside the area on the site.

**Suggestion:** a venue → place table. Learn it from stored events (venue
name → place they agree on) plus an instance file for corrections. Apply it in
`to_event`: a known venue overrides the model's place, and a venue known to
lie outside the area drops the event.

**Instructions for Claude Code:**
1. New optional instance file `instance/sources/venues.yaml`
   (`- {name: "Gasthof Janser", place: "Altenstadt"}`,
   `- {name: "Martinskirche Herzogsägmühle", place: null}` = outside the
   area), a config key `sources.venues_file`, documented and commented.
   Matching uses the normalized name (`merge._venue_key`).
2. In `crawl.to_event`, after place resolution: if the venue is in the file,
   use its place (null → reject with reason "venue outside the area").
   Otherwise, if ≥ 2 stored events with the same venue key agree on a place,
   use it.
3. Seed the file with the cases above. Tests for override, rejection and
   learned mapping.

### F7. Members-only events depend on the model's judgement alone

**Evidence:** club parties, rehearsals and confirmation classes got into the
calendar. The topic text was made more specific on 2026-11-13 (PR #103), but
it is still a judgement call for Sonnet (crawl) and Haiku (merge).

**Suggestion:** a deterministic pre-filter for the obvious cases, before the
models: configurable title patterns in the instance config. The model check
stays for everything else.

**Instructions for Claude Code:**
1. New config key `excluded_title_patterns` (list of case-insensitive
   regexes), commented in `instance/config.yaml` and documented. Seed for
   Schongau: `\bJahreshauptversammlung\b`, `\bHauptversammlung\b`,
   `\bMitgliederversammlung\b`, `\b(Krippenspiel|Chor|Gospelchor\w*)-?Probe\b`,
   `\bProbe\b`, `\bKonfi(rmanden)?(nachmittag|unterricht)\b`,
   `\b(Weihnachts|Nikolaus|Jahresabschluss)feier (des|der) \w*(verein|jugend)`,
   `\bÜbungsschießen\b`, `\bPreisverteilung\b.*\bschießen\b`.
2. Apply it in `crawl.to_event` (reject with reason "excluded title pattern")
   and once per merge to stored events. Log each removal.
3. Tests with the titles removed by hand on 2026-11-13 (see the `data` commit
   `abc0f1b`): all filtered; "Fotogruppe Altenstadt: Clubabend" (open to
   guests) and "Adventsbasar" are kept.

### F8. A stale "still on" copy of a postponed event survives

**Evidence:** "Kai und Funky von Ton Steine Scherben" (scheduled, 5 Dec) next
to the postponed "Ton Steine Scherben…" (postponed to 12 Mar 2027).
`merge.apply` step 0 leaves postponed and cancelled events out of the stored
pair check, so the two are never compared. The merge prompt does not show the
status either.

**Instructions for Claude Code:**
1. In `merge.apply` step 0, also pair a stored `postponed`/`cancelled` event
   with stored `scheduled` events on the same day and place (`same_place`),
   with a title score ≥ `UNCLEAR`.
2. If such a pair is confirmed (clear score, or the AI says same), keep the
   postponed or cancelled event, append the other's sources, and drop the
   scheduled copy. Never turn a postponed event back into scheduled from a
   stale copy. Only a crawl answer for the *postponed* event itself can do
   that.
3. Show the status in `prompts/merge.md` pairs (`| status: postponed`).
   **Prompt change → live test.**
4. Test with the Ton Steine Scherben pair.

### F9. Output tokens wasted on fields the model cannot know

Smaller, but on every event: the model writes a `description` in DE and EN
(≤ 200 characters each) even when the source gives only a title and a date.
Many descriptions are filler ("Filmvorführung im Lagerhauskino Schongau.",
"Konzert im Reaktor Schongau.").

**Suggestion:** allow `description: null` when the source says nothing beyond
title, date and place. The site shows nothing then. Together with F2 (no
re-writing of known events) this removes most filler.

**Instructions for Claude Code:**
1. Check `templates/` and `feeds.py` handle a missing description (JSON-LD,
   ICS, search index). `models.LocalizedText` for `description` would become
   optional. That is a schema change of `events.json`: migrate (no-op, the
   field stays filled for existing events) and document it.
2. Prompt rule: "If the source gives nothing beyond title, date, time and
   place, set description to null; never write a description that only
   repeats these." Prompt change → live test.

---

## Suggested order

1. **F3** (sources: dormant, collapse, prune): fast, low risk, cuts calls
   right away.
2. **F1** (page cache + text mode), then **F5** (re-checks via the cache).
3. **F2** (known events as ids, no full re-write) and **F9**.
4. **F6**, **F7**, **F8** (data quality).
5. **F4** (open search), **O4** (discovery), **O3** (topic fingerprint).
6. **O1**, **O2** (refactor, remove dead code), **O5** (workflows).
7. **O6** (docs), last, so the docs describe the new crawl.

After each of F1–F4, compare the job summary (calls, tokens, cost, events,
"by similarity") with run #41: $2.01, 22 calls, 159 candidates, 2 new,
68 by similarity.
