# ADR-005: Focused AI calls for discovery and the event crawl

- Status: accepted
- Date: 2026-11-09

## Context

The first real run for Schongau used one discovery call and one crawl call. The
discovery call stopped after 21 of 40 searches and missed whole categories
(sports clubs, library, museum, annual events), and nobody could tell which
categories it had looked at. The crawl call used 3 of 40 searches, skipped
several known sources and read a paginated calendar only partly. One broad
call with a long list of instructions lets the model decide on its own when it
is "done", and with lots of context it takes shortcuts.

## Decision

- Split the work into several narrow calls whose scope is fixed by code:
  - Discovery: three focus passes per batch (official and regional calendars,
    clubs and parishes, venues and annual events), each with a German query
    checklist per municipality rendered from code. Later passes see the
    sources of earlier ones. Each pass reports coverage per category
    (`found`, `nothing_found`, `not_checked`).
  - Crawl: *source sweeps* that read a few known sources each completely, with
    pagination, and one *open search* per batch that skips those sources and
    runs a query checklist per month, topic and district.
- The existing merge removes duplicates between the calls of a run.
- The behaviour is controlled by the new `depth` config section; the old single
  call per batch remains available (`discovery_focus_passes: false`,
  `crawl_sources_per_request: 0`) for large areas or a small budget.
- In subscription mode the summary shows the cost at API prices, so the plan
  usage of the extra calls is visible.

## Consequences

- For one municipality a full run uses about 3 discovery calls and 3–5 crawl
  calls instead of 1 + 1: roughly $4 (discovery, monthly) and $2 (crawl, twice a
  week) at API prices.
- For the 30-minute area (about 44 municipalities) the thorough settings
  would exceed the search budget and the plan's limits in one run; spreading
  discovery over several runs is planned for then.
- Coverage gaps are visible in `data/suggested.md` and can be fixed with
  whitelist entries.
