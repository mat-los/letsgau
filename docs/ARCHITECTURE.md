# Architecture

Everything lives in one GitHub repository. A workflow started daily runs a
Python pipeline that lets Claude read and search the web, stores the results as
JSON on the `data` branch and builds static HTML for GitHub Pages.

The authoritative specification is [CONCEPT.md](CONCEPT.md) (German). Details
of every stage are in [OPERATIONS.md](OPERATIONS.md), the files in
[DATA_MODEL.md](DATA_MODEL.md).

## Context (C4 level 1)

```mermaid
flowchart LR
  OP["Operator<br/>edits instance/"]
  U["Visitors on mobile"]
  AI["Search engines and AI assistants"]
  SYS["Let's Gau<br/>GitHub repo + Actions + Pages"]
  CL["Claude<br/>Claude Code or Claude API"]
  OSM["OpenStreetMap Overpass"]
  WEB["Public websites<br/>municipalities, clubs, venues"]
  IG["Instagram Graph API"]
  OP --> SYS
  U --> SYS
  AI --> SYS
  SYS --> CL
  SYS --> OSM
  SYS --> WEB
  SYS --> IG
  CL --> WEB
```

The pipeline downloads source pages, flyers and Instagram posts itself; Claude
reads them, and searches and fetches the web only where that is not enough
(discovery, the open search, pages that need a browser).

## Containers (C4 level 2)

```mermaid
flowchart LR
  subgraph REPO["GitHub repository"]
    CFG["instance/<br/>config.yaml, sources/, content/"]
    WF["GitHub Actions<br/>daily + manual"]
    PY["Python pipeline<br/>src/letsgau"]
    PR["prompts/"]
    DATA[("data/*.json<br/>branch: data")]
    SITE["site/<br/>generated HTML"]
  end
  OSM["OpenStreetMap Overpass"]
  CL["Claude<br/>Sonnet, Haiku"]
  WEB["Public websites"]
  IG["Instagram Graph API"]
  PAGES["GitHub Pages"]
  WF --> PY
  CFG --> PY
  PR --> PY
  PY <--> DATA
  PY --> OSM
  PY --> WEB
  PY --> IG
  PY --> CL
  CL --> WEB
  PY --> SITE
  SITE --> PAGES
```

## Modules

| Module | Responsibility |
| --- | --- |
| `cli` | command-line entry point (`letsgau check`, `run`, `sources`, `init`) |
| `config` | load and validate `instance/config.yaml` (file, line and field in errors) |
| `scheduler` | decide which jobs are due, run them in order, job summary |
| `geo` | places of the configured municipalities (Overpass) → `places.json` |
| `discovery` | find and rate sources per municipality (focus passes) → `sources.json`, `suggested.md` |
| `crawl` | event crawl per batch: plan the calls, validate answers, hand over to the merge |
| `pages` | source pages as text with a SHA-256 cache (`pages.json`): unchanged pages cost no AI call |
| `flyers` | download posters and PDFs of the flyer pages; the crawl's flyer calls with a cache (`flyers.json`) |
| `instagram` | Graph API (Business Discovery), candidate account check, the crawl's Instagram step (`instagram.json`) |
| `merge` | duplicates, AI tie-break and topic check, title patterns, status lifecycle, cleanup → `events.json` |
| `ai_runner` | Claude Code headless (subscription) or Claude API (`api_key`), JSON-schema answers, usage |
| `models` | Pydantic models of the `data/*.json` files |
| `build`, `feeds` | HTML DE/EN, JSON-LD, JSON, ICS, `llms.txt`, sitemap, robots |
| `init` | set up a fresh instance from `examples/instance/` |
| `util` | shared helpers: paths, URLs, prompt templates, JSON files, logging |

## Sequence of a run

```mermaid
sequenceDiagram
  participant A as GitHub Actions
  participant S as Scheduler
  participant G as Geo
  participant D as Discovery
  participant E as Event crawl
  participant M as Merge
  participant B as Site build
  A->>S: cron or manual start with job=...
  S->>S: read config.yaml + state.json, decide what is due
  S->>G: places of the municipalities (only if they changed)
  opt discovery due (monthly, or after a geo change)
    D->>D: three focus passes per batch, rate sources, check Instagram candidates
  end
  opt events due (Monday and Thursday)
    E->>E: download known sources as text, reuse unchanged pages
    E->>E: page calls (changed text), agent sweeps (pages that need a browser)
    E->>E: flyer calls (new files only), re-checks, open search (every other run)
    E->>E: Instagram step (new posts only)
    E->>M: candidates and known events seen again
    M->>M: match against events.json, title patterns, Haiku tie-break and topic check, misses, cleanup
  end
  S->>B: build HTML DE/EN, JSON, ICS, llms.txt
  B->>A: commit data/ to the data branch, deploy Pages
```

## Event status lifecycle

```mermaid
stateDiagram-v2
  [*] --> scheduled: newly found
  scheduled --> cancelled: cancellation notice found
  scheduled --> postponed: new date found
  scheduled --> unconfirmed: misses reach threshold
  unconfirmed --> scheduled: found again
  postponed --> [*]: old date passed
  cancelled --> [*]: date passed
  scheduled --> [*]: date passed
  unconfirmed --> [*]: date passed
```

A cancelled event stays visible until its date. A postponement creates a new
event on the new date; `postponed_to` on the old event points to it.

## Key decisions

| Topic | Choice | Reason |
| --- | --- | --- |
| Hosting | GitHub Pages | free, custom domain, automatic HTTPS |
| Scheduling | workflow daily, frequencies in `config.yaml` | changing a frequency is one line, not cron syntax |
| Storage | JSON files on the `data` branch | free, versioned by Git |
| Pipeline | Python 3.12, Pydantic, Jinja2 | readable, good validation of AI output |
| AI | Claude Code headless (default) or Claude API with web search and fetch | no own crawler for discovery and search |
| Reading sources | the pipeline downloads pages, flyers and posts and caches what the AI read | an unchanged source costs no AI call |
| Models | Sonnet for discovery and events, Haiku for duplicates, topics and the Instagram pre-screen | `models` in `config.yaml` |
| Frontend | static HTML, one CSS, little vanilla JS | fast on mobile, readable for AIs without JS |
| Diagrams | Mermaid | renders on GitHub, maintainable as text |

ADRs live in [decisions/](decisions/).
