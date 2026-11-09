# Architecture

Everything lives in one GitHub repository. A workflow started daily runs a
Python pipeline that lets Claude search the web, stores the results as JSON on
the `data` branch and builds static HTML for GitHub Pages.

The authoritative specification is [CONCEPT.md](CONCEPT.md) (German).

## Context (C4 level 1)

```mermaid
flowchart LR
  OP["Operator<br/>edits instance/config.yaml"]
  U["Visitors on mobile"]
  AI["Google and AI assistants"]
  SYS["Let's Gau<br/>GitHub repo + Actions + Pages"]
  CL["Claude<br/>Claude Code or Claude API"]
  ORS["OpenRouteService"]
  OSM["OpenStreetMap Overpass"]
  WEB["Public websites<br/>municipalities, clubs, venues"]
  OP --> SYS
  U --> SYS
  AI --> SYS
  SYS --> CL
  SYS --> ORS
  SYS --> OSM
  CL --> WEB
```

## Containers (C4 level 2)

```mermaid
flowchart LR
  subgraph REPO["GitHub repository"]
    CFG["instance/config.yaml"]
    WF["GitHub Actions<br/>daily + manual"]
    PY["Python pipeline<br/>src/letsgau"]
    DATA[("data/*.json<br/>branch: data")]
    SITE["site/<br/>generated HTML"]
  end
  ORS["OpenRouteService (HeiGIT)<br/>isochrone + matrix"]
  OSM["OpenStreetMap Overpass<br/>places in area"]
  CL["Claude<br/>Opus, Sonnet, Haiku<br/>web search + fetch"]
  WEB["Public websites<br/>municipalities, clubs, venues"]
  PAGES["GitHub Pages<br/>custom domain"]
  U["Visitors on mobile"]
  AI["Google and AI assistants"]
  WF --> PY
  CFG --> PY
  PY <--> DATA
  PY --> ORS
  PY --> OSM
  PY --> CL
  CL --> WEB
  PY --> SITE
  SITE --> PAGES
  U --> PAGES
  AI --> PAGES
```

## Modules

| Module | Responsibility | Phase |
| --- | --- | --- |
| `config` | load and validate `config.yaml` | 0 |
| `cli` | command-line entry point | 0 |
| `scheduler` | decide which jobs are due, run them in order, job summary | 6 ✓ |
| `geo` | isochrone (ORS), places (Overpass), drive times (ORS matrix) | 1 ✓ |
| `ai_runner` | Claude Code headless (subscription) or Claude API (api_key) | 2 ✓ |
| `crawl` | event crawl per batch of places, validation | 2 ✓ |
| `models` | Pydantic models for `data/*.json` | 2–3 |
| `merge` | fuzzy dedupe, Haiku tie-break and topic check, status lifecycle, cleanup | 3 ✓ |
| `build`, `feeds` | HTML DE/EN, JSON-LD, JSON, ICS, llms.txt, sitemap, robots | 4–5 ✓ |
| `discovery` | find and rate sources, `sources.json`, suggestions, hit statistics | 7 ✓ |
| `init` | set up a fresh instance from `examples/instance/` | 8 ✓ |

## Sequence of a run

```mermaid
sequenceDiagram
  participant A as GitHub Actions
  participant S as Scheduler
  participant G as Geo
  participant D as Discovery (Opus)
  participant E as Event crawl (Sonnet)
  participant M as Merge (Haiku)
  participant B as Site build
  A->>S: start via cron or manually with job=...
  S->>S: read config.yaml + state.json, determine due jobs
  alt center or drive time changed
    S->>G: recompute isochrone and place list
    S->>D: force discovery
  end
  opt discovery due
    D->>D: find and rate new sources
  end
  opt event crawl due
    E->>E: week 1 deep check, weeks 2-4 new events only
    E->>M: event candidates as JSON
    M->>M: match against events.json, status, topic filter, cleanup
  end
  S->>B: build HTML DE/EN, JSON, ICS, llms.txt
  B->>A: commit data/, deploy Pages
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
| Storage | JSON files on the `data` branch | free, versioned by Git; Supabase possible later |
| Pipeline | Python 3.12, Pydantic, Jinja2 | readable, good validation of AI output |
| AI | Claude Code headless (default) or Claude API with `web_search` / `web_fetch` | no own crawler needed |
| Models | Opus for discovery, Sonnet for events, Haiku for duplicates | thoroughness where it counts |
| Frontend | static HTML, one CSS, little vanilla JS | fast on mobile, readable for AIs without JS |
| Diagrams | Mermaid | renders on GitHub, maintainable as text |

ADRs live in [decisions/](decisions/).
