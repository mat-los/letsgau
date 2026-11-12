# Operations

> How to run, check and debug the pipeline.

## Validate the configuration

```bash
python -m letsgau check
```

## Run jobs locally

```bash
python -m letsgau run --job events --dry-run --limit-places 3
```

`--job` accepts `auto` (only due jobs), `geo`, `discovery`, `events`, `build`
or `all`. `events` also runs `build` afterwards, so new events go live at once. `--dry-run` writes nothing to `data/`, `geo/` or `site/`.
`letsgau run` reads `.env` from the current directory (existing environment
variables win).

## Geo stage

```bash
python -m letsgau run --job geo --dry-run
```

```bash
python -m letsgau run --job geo
```

- Writes `data/places.json`, `data/state.json` and `geo/isochrone.geojson`
  (gitignored on `main`; the bot commits them to the `data` branch in Phase 6).
- Skips everything if center, drive time and place filters are unchanged; such a
  run needs no `ORS_API_KEY`. `--force` recomputes anyway.
- A changed center or drive time fetches a new isochrone and sets
  `force_discovery` in `state.json`. Changing only `place_types`,
  `include_places` or `exclude_places` reuses the stored isochrone.
- Warnings name `include_places` that were not found and `exclude_places` that
  were not in the list. Check the printed list for plausibility after a change.
- Each place gets its municipality (OSM `admin_level=8`) from the same Overpass
  query; the event crawl batches by municipality. A `places.json` without
  municipalities is refreshed once with the stored isochrone.
- Membership comes from the isochrone, drive times from the matrix; at the
  edge a place can show slightly more than `drive_time_minutes` (e.g. 31 min
  for 30). For Schongau, 30 minutes give about 105 places, 5 minutes only
  Schongau itself (live check 2026-11-08).
- Requests per recomputation: 1 isochrone, 1–2 Overpass queries, 1 matrix
  request per 500 places. Overpass queries take about 30–40 s.

### Live check on GitHub

*Actions → Geo check → Run workflow* (input: drive time, default 30) runs the
geo stage as a dry run with the `ORS_API_KEY` secret, prints the place list in
the job summary and uploads freshly recorded fixtures (`geo-fixtures`
artifact). It also runs on pull requests that change the workflow file.

### External services

| Service | Default URL | Override |
| --- | --- | --- |
| OpenRouteService (HeiGIT) | `https://api.heigit.org/openrouteservice` (`/v2/isochrones/driving-car`, `/v2/matrix/driving-car`) | `ORS_BASE_URL` |
| Overpass API | `https://overpass-api.de/api/interpreter`, then `overpass.private.coffee`, then `maps.mail.ru` (first that answers; if all fail, the list is retried after 60 s and 120 s) | `OVERPASS_URL` (comma-separated list) |

The ORS key goes into the `Authorization` header. HeiGIT deprecated the old host
`api.openrouteservice.org` in 2026 and moved key management to
[account.heigit.org](https://account.heigit.org); existing keys work on the new
host. Every request is logged to stderr with status and duration (`geo: POST ...`).
Daily and per-minute quotas are shown in the HeiGIT dashboard; a daily
limit answers `403`, the per-minute limit `429` (retried with back-off). Very
long drive times may exceed the isochrone range allowed by the free plan; ORS
then returns an error that the stage prints.

## Event crawl

```bash
python -m letsgau run --job events --dry-run --limit-places 3
```

- Needs `data/places.json` (run `--job geo` first) and the secret of the
  configured `ai.auth` mode.
- Prints every event and writes `data/events.json` and the usage to
  `data/state.json` (not with `--dry-run`). `--limit-places N` crawls only the
  N nearest municipalities.
- Places are grouped by municipality (`municipality` in `places.json`); the
  prompt lists each municipality with its districts, and events keep the exact
  district as their place.
- With `depth.crawl_sources_per_request: N` (default 4) every batch is split
  into *source sweeps*, *re-checks* and the *open search*:
  - each sweep reads N known sources (whitelist first, then discovered ones)
    completely, following pagination to the end of the window, with at most 8
    searches to find moved pages;
  - each re-check call opens the sources of up to 20 known events of the next
    `deep_check_days` and reports date, time, cancellation or postponement.
    Events that a source call of the same run already reported again count as
    confirmed and are left out; if none remain, the re-check call is skipped;
  - the open search skips the known sources and runs a fixed query checklist
    per municipality (every month of the window, 15 topics, every district).
    Its budget, `depth.crawl_searches_per_request`, is split into calls of
    about 13 searches (default 13 → one call), each with a part of the
    checklist. Every run starts the checklist at a different query (Monday
    and Thursday about half a list apart), so over a few runs every query is
    run even when a call stops early. The log lists the searches each call
    actually ran. A call that stops early is not asked again: a second
    attempt in a live run repeated the same queries and found nothing new.

  All answers go through the same merge, which
  removes duplicates between the calls. If a call fails, the events of the
  other calls are kept, but the batch does not count as covered, so no stored
  event is marked as missed. `0` gives one combined call per batch (cheaper,
  less complete).
- Flyer pages (`sources.flyers_file`) get one extra call each: the pipeline
  downloads up to `max_files` posters and PDFs (`src/letsgau/flyers.py`) and the
  model reads them without web access (Claude Code: Read on a temporary
  `files/` folder; API: attached as image/document blocks). An image costs
  about 1,500 input tokens. If nothing could be downloaded, no AI call is made
  and the crawl warns.
- Flyer cache (`data/flyers.json`): every downloaded file gets a SHA-256
  fingerprint. Only new or changed files are sent to the AI, which reports
  every event on them up to `store_days`; unchanged files reuse the stored
  events without an AI call. Identical files under two URLs are read once. The
  log shows `N flyer file(s) from <page>, N already read, N new`.
- Instagram (`sources.instagram_file`, `instagram.*`; `src/letsgau/instagram.py`):
  every batch ends with one Instagram step, so accounts found by its other
  calls are read in the same run.
  - **Accounts:** the accounts of the list whose `places` are in the batch,
    then (with `instagram.read_mined`) accounts *mined* in this or earlier
    runs: every web call reports the Instagram accounts that the pages it read
    link to (`instagram_accounts` in its answer), and new ones are added to
    `data/instagram.json` with the batch's municipalities; discovery adds
    accounts too (see *Discovery*). At most `max_accounts_per_run` accounts per
    run: the list first, then the mined accounts read least recently, so all
    of them take turns when there are more than the limit.
  - **Reading:** the Graph API's *Business Discovery* returns the latest
    `posts_per_account` posts (caption, image or video cover, carousel images,
    time, link). Posts older than `max_post_age_days` and posts already read
    are skipped; new posts are read by the AI in groups of 6 (caption plus up
    to 4 images each, no web access), with the post's link as the source.
    Results are cached per post, like flyers.
  - **Accounts that cannot be read:** personal and private accounts
    (`not_business`) or unknown names (`not_found`) are marked and tried again
    after 30 days; a warning only for accounts on the list. An invalid token,
    a rate limit or a network error stops the step for the run, with a
    warning. Without the two secrets the step is skipped with one warning.
  - The token is never written to logs, warnings or `data/instagram.json`.
- Warnings name dropped events with the reason (schema, window, place, tags,
  source), failed batches and whitelist sources that were not checked.
- Subscription mode runs `claude -p` (Claude Code CLI, `npm install -g
  @anthropic-ai/claude-code`) in a temporary directory with only WebSearch and
  WebFetch. `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` are removed from its
  environment so it never bills an API key. Runs count against the plan's usage
  limits; the cost shown is what the run would cost at API prices (the CLI's
  `total_cost_usd`), a measure of how much of the plan a run uses.
- API mode needs `ANTHROPIC_API_KEY`; the cost shown is an estimate from token
  prices and $10 per 1,000 searches.
- `LETSGAU_RECORD_DIR=dir` saves every raw AI answer (for test fixtures).
- The merge runs in the same stage (also with `--dry-run`, which then only
  prints its summary): `merge: N new, N updated (N by similarity), N cancelled,
  N postponed, N now unconfirmed, N excluded by topic, N past deleted`.
  Unclear duplicates and the topic check of new events cost one call to the
  merge model (Haiku) per run; if that call fails, unclear pairs stay separate.
  Besides title similarity 0.5–0.85, every pair with the same day, start time
  and municipality goes to that call, also two events already stored (which
  are then combined: the older one stays, sources are joined, links to the
  removed one are redirected).

### Check the sources

```bash
python -m letsgau sources --check
```

Lists every whitelist URL (templates expanded for the municipalities in
`data/places.json`) with its HTTP status. The *Crawl check* workflow runs it for
the 30-minute area and shows the result in the job summary.

### Live check on GitHub

*Actions → Crawl check → Run workflow* (input: number of places, default 3)
computes the places for the instance config, runs discovery into a throwaway
`data/` and then the crawl as a dry run, with `ORS_API_KEY` and
`CLAUDE_CODE_OAUTH_TOKEN`. Results appear only in the job summary and the
`crawl-recordings` artifact. It is manual only: a run uses about as much of the
plan as a real run (roughly $6 at API prices for one municipality with the
thorough `depth` settings), so use it for bigger prompt changes before merging;
for a small test area a real *Run* after merging is the cheaper test.

## Discovery

```bash
python -m letsgau run --job discovery --dry-run --limit-places 2
```

- Runs monthly (`jobs.discovery`) and after every geo change. Municipalities are
  batched by `depth.discovery_places_per_request`. With
  `depth.discovery_focus_passes` (default) each batch gets three calls to the
  discovery model (`models.discovery`, Sonnet by default), one per focus: official and regional calendars (and
  the municipality's club directory), clubs and parishes (working through that
  directory), venues, institutions and annual events. Each pass has a fixed
  German query checklist per municipality and sees the sources of the earlier
  passes. Without focus passes it is one general call per batch.
- Every focus pass also reports coverage per category (`found`,
  `nothing_found`, `not_checked`); the gaps are printed and listed in the
  *Coverage* section of `data/suggested.md`.
- Adds new sources to `data/sources.json` (`origin: discovery`), re-rates known
  ones, syncs the whitelist in and marks blacklisted domains.
- **Instagram** (with `sources.instagram_file`):
  - every pass reports the Instagram accounts it saw (`instagram_accounts`);
    the venues pass runs `site:instagram.com <town> …` searches on purpose;
  - the passes list the cafés, bars, pubs, clubs and venues they saw in
    `venues_seen`; the venues pass reads the municipality's own gastronomy
    and business directory for it;
  - the AI lookup call per batch (`prompts/instagram_lookup.md`) is off by
    default (`instagram.ai_lookup`): its search engine rarely shows Instagram
    usernames. Instead, a **scheduled Claude Code session**
    (`prompts/instagram_session.md`, monthly after discovery) searches the
    accounts of the venues in `data/instagram.json` (`venues`) with the web
    search of a Claude Code session and proposes them in
    `instance/sources/instagram_candidates.yaml` by pull request; after the
    merge, the next events or discovery run checks them;
  - **every** account found by discovery, every entry of
    `sources.instagram_candidates_file` and every mined account that was
    never read yet, is checked with the Graph API (`business_discovery` with
    name, biography and website; needs the `INSTAGRAM_*` secrets). It is kept
    only if it is a business or creator account whose name, biography or
    website names the municipality (or one of its districts), or whose
    username contains it together with more than the bare place name, or
    which links the organiser's own website. Others go to `rejected` in
    `data/instagram.json` (checked again after 180 days) and are removed
    from the accounts; at most 40 checks per run. Accounts on the list
    (`instagram.yaml`) are never checked. The log lists confirmed, rejected
    and known accounts that were checked and kept, with the evidence.
- Writes `data/suggested.md` on the `data` branch: whitelist candidates (≥ 3
  hits or rating ≥ 4) and blacklist candidates as YAML blocks to copy.
- The events stage gives each batch the active sources of its municipalities
  (rating ≥ 3 or ≥ 2 hits; all of them in sweep mode, at most 12 in combined
  mode) and counts hits per source after every run.
- Known sources are only listed in the prompt (not opened or reported again)
  if they are whitelisted, or were rated or had a hit in the last 90 days
  (`RECHECK_SOURCES_DAYS`); older ones are listed separately to be re-rated.
- Usage: with Opus and 30 searches per pass about $4.35 at API prices for
  Schongau alone (three passes); with the defaults (Sonnet, 20 searches, known
  sources not re-read) roughly a third of that.

## Build the website

```bash
python -m letsgau run --job build
```

- Renders `site/` from `data/events.json` (and `data/places.json` for grouping
  by distance): `/<lang>/` (today) and `/<lang>/<YYYY-MM-DD>/` for
  `display_days` days in every language, plus `/` (language redirect).
  "Today" is the date in `site.timezone`.
- Links are relative, so the folder works on a custom domain and on
  `<owner>.github.io/<repo>/`; canonical and hreflang use `site.base_url`.
- Aborts if `config.yaml` still has placeholders or, in GitHub Actions,
  `site.owner` differs from the repository owner.
- `--dry-run` builds into a temporary folder instead of `site/`.
- Preview locally: `python -m http.server --directory site` and open
  `http://localhost:8000/de/`.

Content pages come from `instance/content/<page>.<lang>.md` (about,
missing-event, imprint, privacy, optional feedback intro); config values are
filled in, `[TODO: ...]` markers are listed by `letsgau check` and reported by
the build. The build also writes `/<lang>/feedback/`, `/<lang>/changelog/`
(from `CHANGELOG.md`) and `CNAME` for a custom domain in `base_url`.

## Set up a new region

```bash
python -m letsgau init
```

Asks for center, drive time, site name, URL, contact and GitHub owner (or takes
`--label --lat --lon --minutes --name --base-url --contact --owner --yes`) and
replaces `instance/` with fresh files from `examples/instance/`.

Machine-readable outputs (`src/letsgau/feeds.py`), all generated by the build:

| File | Content |
| --- | --- |
| day pages | schema.org `Event` per event as JSON-LD (`@graph`), program items as `subEvent`/`superEvent`, `eventStatus` Scheduled/Cancelled/Postponed |
| `/llms.txt` | what the site is, coverage, URL patterns, data links, tags, AI notice, last update |
| `/data/events.json` | public copy of `data/events.json` without `misses` |
| `/data/events.ics`, `/data/events.<lang>.ics` | iCalendar feed; times in UTC, events on single dates once per date |
| `/data/search.<lang>.json` | search index: every event of the displayed days once (on its first displayed day), sorted by date and time, with folded search text (including the category names in every language) and the status if not scheduled; fetched by `static/search.js` only when a search starts |
| `/sitemap.xml` | all day pages with hreflang alternates |
| `/robots.txt` | allows everyone, explicitly GPTBot, ClaudeBot, PerplexityBot, Google-Extended; with `site.indexing: false` only `Allow: /` (every page then carries `noindex`) |

The *Site check* workflow builds the fixture day with 100 events and runs
Lighthouse (mobile; performance and accessibility must be ≥ 90) and a `curl`
check that every event is in the HTML.

## Run on GitHub

The workflow `.github/workflows/run.yml` runs daily at 04:17 UTC and can be
started by hand: *Actions → Run → Run workflow* (also in the GitHub mobile app),
with the job (`auto`, `geo`, `discovery`, `events`, `build`, `all`) and an
optional dry run. A `concurrency` group prevents overlapping runs. The deploy
job runs only when the run job uploaded a site, so a run that builds nothing
(e.g. `discovery`) is not marked as failed.

What `--job auto` does (`src/letsgau/scheduler.py`):

| Stage | When |
| --- | --- |
| `geo` | always; does nothing unless center, drive time or place filters changed |
| `discovery` | when its schedule is due, or forced after a geo change (`force_discovery`) |
| `events` | when its schedule is due |
| `build` | when its schedule is due (default daily) and after every events run |

"Due" means: the most recent scheduled day up to today is after the job's last
successful run (`last_run` in `state.json`). A missed run is caught up the next
day, and a changed frequency in `config.yaml` applies on the next run. A failing
stage does not stop the build; the run then ends with exit code 1.

The workflow checks out the `data` branch as a worktree (created on the first
run), links `data/` and `geo/` into the checkout, runs the pipeline, commits the
changes to `data` as `letsgau-bot` and deploys `site/` with the official Pages
actions. The job summary lists the stages, event changes (new, updated,
cancelled, postponed, unconfirmed, excluded, deleted) and the AI usage.

Every run also uploads the artifact **ai-recordings** (kept 7 days): the raw
answer of every AI call and, in subscription mode, the transcript of each call
(`*.transcript.jsonl`, every search query and fetched page). The log prints the
search queries of each call as `… N searches: query | query | …`. Use it to see
why an event was missed.

One-time setup: *Settings → Pages → Source: GitHub Actions*, and the secrets
below.

## Secrets

| Secret | Needed for |
| --- | --- |
| `CLAUDE_CODE_OAUTH_TOKEN` | `ai.auth: subscription` (create with `claude setup-token`); must not be combined with `ANTHROPIC_API_KEY` in the same environment |
| `ANTHROPIC_API_KEY` | `ai.auth: api_key` |
| `ORS_API_KEY` | geo stage (OpenRouteService); free key from [account.heigit.org](https://account.heigit.org) |
| `INSTAGRAM_ACCESS_TOKEN` | optional: reading Instagram accounts (see below); without it, Instagram is skipped |
| `INSTAGRAM_USER_ID` | optional: the ID of your own Instagram professional account |

Locally, put them into `.env` (see `.env.example`). In API mode, set a monthly
spend limit in the Anthropic Console.

### Instagram setup

Only business and creator accounts can be read, through your own Instagram
*professional* account (Graph API, "Instagram API with Facebook Login"):

1. Switch your Instagram account to a professional account and link it to a
   Facebook page (page settings → Linked accounts → Instagram).
2. On developers.facebook.com create an app (type *Business*) and add
   *Instagram → API setup with Facebook Login*.
3. Add the permissions `instagram_basic`, `instagram_manage_insights` (required for
   Business Discovery; without it the API answers error `#10`, "Application does
   not have permission"), `pages_show_list`, `pages_read_engagement` and
   `business_management` (needed when the page belongs to a business portfolio)
   to the app's use case, and to the *Facebook Login for Business* configuration
   if you use one. The `instagram_business_*` permissions belong to "Instagram
   API with Instagram Login" and do not work here. In the *Graph API Explorer*,
   generate a user token with them; `<page id>?fields=instagram_business_account`
   (page ID from Business settings → Pages) shows your Instagram user ID.
4. Test: `<user id>?fields=business_discovery.username(<account>){media.limit(3){caption,timestamp,permalink}}`
   should return the account's latest posts.
5. For the workflow, create a token that does not expire: Meta Business Suite →
   Business settings → System users → assign the app and the page → generate
   a token with the permissions above. (A user token from the Explorer expires
   after hours, an extended one after about 60 days.)
6. Save it as the secret `INSTAGRAM_ACCESS_TOKEN` and the user ID as
   `INSTAGRAM_USER_ID`. List accounts in `instance/sources/instagram.yaml`.

Meta changes this API now and then; if every account fails with `auth` or
`other`, check the token and the current Meta documentation first. A missing
app permission (`permission`) stops the Instagram step for the whole run.

## Costs

Every run writes requests, searches, tokens, the estimated cost and the AI time
to the GitHub job summary; the usage of the last events and discovery runs is
also in `data/state.json` (`last_usage`).
