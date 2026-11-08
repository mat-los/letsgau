# GitHub issues, milestones and labels

Project organisation as described in `docs/CONCEPT.md` (section "Projektorganisation in GitHub").
The issues and labels exist on GitHub (labels were auto-created without color or description).
Milestones cannot be created with the tools available to Claude Code, so run the script at the
end once with the [GitHub CLI](https://cli.github.com/): it creates the milestones, sets label
colors and descriptions, adds the two unused labels and assigns every issue to its milestone.

## Milestones

| Milestone | Scope | Release |
| --- | --- | --- |
| Phase 0 – Scaffold | Repo structure, config loading, CLI, first docs | `v0.1.0` |
| Phase 1 – Geo | Isochrone, places, drive times | `v0.2.0` |
| Phase 2 – Event crawl | Claude crawl of events per batch of places | `v0.3.0` |
| Phase 3 – Merge and status | Dedupe, status lifecycle, cleanup | `v0.4.0` |
| Phase 4 – Website | Mobile-first day pages DE/EN | `v0.5.0` |
| Phase 5 – AI readability | JSON-LD, llms.txt, raw data, sitemap | `v0.6.0` |
| Phase 6 – Scheduler and workflow | Daily workflow, due jobs, commit and deploy | `v0.7.0` |
| Phase 7 – Discovery | Find and rate sources with Opus | `v0.8.0` |
| Phase 8 – Content, domain, wrap-up | Content pages, custom domain, fork test | `v1.0.0` |

## Labels

| Label | Color | Description |
| --- | --- | --- |
| `phase-0` | `#1d76db` | Phase 0: Scaffold |
| `phase-1` | `#1d76db` | Phase 1: Geo |
| `phase-2` | `#1d76db` | Phase 2: Event crawl |
| `phase-3` | `#1d76db` | Phase 3: Merge and status |
| `phase-4` | `#1d76db` | Phase 4: Website |
| `phase-5` | `#1d76db` | Phase 5: AI readability |
| `phase-6` | `#1d76db` | Phase 6: Scheduler and workflow |
| `phase-7` | `#1d76db` | Phase 7: Discovery |
| `phase-8` | `#1d76db` | Phase 8: Content, domain, wrap-up |
| `bug` | `#d73a4a` | Something is wrong |
| `feature-request` | `#a2eeef` | New feature or improvement |
| `source-suggestion` | `#0e8a16` | Missing event or event source |
| `backlog` | `#cfd3d7` | Idea for after version 1 |
| `crawler-report` | `#fbca04` | Automated crawler run report |

## Issues

One issue per acceptance criterion, labelled `phase-N` and assigned to the phase milestone.
Phase 0 is implemented in the first pull request.

### Phase 0 – Scaffold

- [x] [#1](https://github.com/mat-los/letsgau/issues/1) Repo structure, pyproject.toml, Ruff, pytest; ci.yml runs green
- [x] [#2](https://github.com/mat-los/letsgau/issues/2) config.yaml loaded with Pydantic; a typo gives a clear message with line and field
- [x] [#3](https://github.com/mat-los/letsgau/issues/3) `python -m letsgau --help` lists all commands; first versions of README.md, CLAUDE.md, docs/ARCHITECTURE.md

### Phase 1 – Geo

- [x] [#4](https://github.com/mat-los/letsgau/issues/4) Plausible place list with drive times in data/places.json for Schongau and 30 minutes
- [x] [#5](https://github.com/mat-los/letsgau/issues/5) Changing drive time or center triggers recomputation; a second run without change does not
- [x] [#6](https://github.com/mat-los/letsgau/issues/6) include_places and exclude_places work (e.g. Landsberg and Weilheim)

### Phase 2 – Event crawl

- [ ] [#7](https://github.com/mat-los/letsgau/issues/7) A run returns events for 28 days; all pass schema validation
- [ ] [#8](https://github.com/mat-los/letsgau/issues/8) No source comes from a blacklisted domain; every whitelist source was queried
- [ ] [#9](https://github.com/mat-los/letsgau/issues/9) Every event has DE and EN texts, tags only from the list, exactly one primary_tag
- [ ] [#10](https://github.com/mat-los/letsgau/issues/10) `--dry-run` and `--limit-places` work

### Phase 3 – Merge and status

- [ ] [#11](https://github.com/mat-los/letsgau/issues/11) Two consecutive runs create no duplicates; sources are appended
- [ ] [#12](https://github.com/mat-los/letsgau/issues/12) Fixtures for cancellation, postponement, vanished event and program items yield the right status / parent_id
- [ ] [#13](https://github.com/mat-los/letsgau/issues/13) Multi-day events stored once in events.json; past events are deleted

### Phase 4 – Website

- [ ] [#14](https://github.com/mat-los/letsgau/issues/14) Day pages DE and EN for 21 days with coverage note, date bar, sections, expand, program items and status display
- [ ] [#15](https://github.com/mat-los/letsgau/issues/15) Filters with include/exclude, search, grouping, swipe; settings persist while browsing
- [ ] [#16](https://github.com/mat-los/letsgau/issues/16) A fixture day with 100 events is clear on a 375 px wide screen
- [ ] [#17](https://github.com/mat-los/letsgau/issues/17) All events visible without JavaScript; Lighthouse mobile >= 90 for performance and accessibility

### Phase 5 – AI readability

- [ ] [#18](https://github.com/mat-los/letsgau/issues/18) JSON-LD passes the schema.org validator without errors
- [ ] [#19](https://github.com/mat-los/letsgau/issues/19) llms.txt, events.json, events.ics, sitemap.xml and robots.txt are generated
- [ ] [#20](https://github.com/mat-los/letsgau/issues/20) Fetching a day page with curl shows all events in the text

### Phase 6 – Scheduler and workflow

- [ ] [#21](https://github.com/mat-los/letsgau/issues/21) Daily run executes only due jobs; frequency changes in config.yaml apply on the next run
- [ ] [#22](https://github.com/mat-los/letsgau/issues/22) "Run workflow" button with job selection works
- [ ] [#23](https://github.com/mat-los/letsgau/issues/23) Data is committed, Pages is deployed, job summary shows changes and costs

### Phase 7 – Discovery

- [ ] [#24](https://github.com/mat-los/letsgau/issues/24) Opus run extends sources.json and writes sources/suggested.md with rated candidates
- [ ] [#25](https://github.com/mat-los/letsgau/issues/25) Triggered automatically after a geo change

### Phase 8 – Content, domain, wrap-up

- [ ] [#26](https://github.com/mat-los/letsgau/issues/26) About, "Why is my event not here?", imprint and privacy as DE and EN templates
- [ ] [#27](https://github.com/mat-los/letsgau/issues/27) Custom domain described in README.md; CNAME generated from base_url
- [ ] [#28](https://github.com/mat-los/letsgau/issues/28) A fresh fork with another center (e.g. Murnau) runs through with only the README steps after `letsgau init` and contains no Schongau data

### Backlog (label `backlog`, no milestone)

- [ ] [#29](https://github.com/mat-los/letsgau/issues/29) **Crawler report as GitHub issue:** Each run opens an issue "Crawler report YYYY-MM-DD" with label `crawler-report`: new, changed and cancelled events; failed fetches; discarded entries with reason (schema, topic blacklist); new sources as whitelist/blacklist candidates to tick off; searches, tokens and estimated costs. The previous report is closed automatically. Needs `issues: write` in the workflow and eventually replaces `sources/suggested.md`.

## Project board

Create a GitHub Project (board layout) with the columns **Todo**, **In progress**, **Done**
and link it to the repository (*Projects → New project → Board*). Enable the built-in workflows
"Item closed → Done" and "Pull request merged → Done" so that `Closes #N` moves the card.

## Milestones and label colors with `gh`

Run after `gh auth login`. Labels use `--force` and are safe to rerun; run the milestone part once.

```bash
#!/usr/bin/env bash
set -euo pipefail
REPO=mat-los/letsgau

# Labels
gh label create "phase-0" --repo "$REPO" --color 1d76db --description "Phase 0: Scaffold" --force
gh label create "phase-1" --repo "$REPO" --color 1d76db --description "Phase 1: Geo" --force
gh label create "phase-2" --repo "$REPO" --color 1d76db --description "Phase 2: Event crawl" --force
gh label create "phase-3" --repo "$REPO" --color 1d76db --description "Phase 3: Merge and status" --force
gh label create "phase-4" --repo "$REPO" --color 1d76db --description "Phase 4: Website" --force
gh label create "phase-5" --repo "$REPO" --color 1d76db --description "Phase 5: AI readability" --force
gh label create "phase-6" --repo "$REPO" --color 1d76db --description "Phase 6: Scheduler and workflow" --force
gh label create "phase-7" --repo "$REPO" --color 1d76db --description "Phase 7: Discovery" --force
gh label create "phase-8" --repo "$REPO" --color 1d76db --description "Phase 8: Content, domain, wrap-up" --force
gh label create "bug" --repo "$REPO" --color d73a4a --description "Something is wrong" --force
gh label create "feature-request" --repo "$REPO" --color a2eeef --description "New feature or improvement" --force
gh label create "source-suggestion" --repo "$REPO" --color 0e8a16 --description "Missing event or event source" --force
gh label create "backlog" --repo "$REPO" --color cfd3d7 --description "Idea for after version 1" --force
gh label create "crawler-report" --repo "$REPO" --color fbca04 --description "Automated crawler run report" --force

# Milestones
gh api "repos/$REPO/milestones" -f title="Phase 0 – Scaffold" -f description="Repo structure, config loading, CLI, first docs (release v0.1.0)" >/dev/null
gh api "repos/$REPO/milestones" -f title="Phase 1 – Geo" -f description="Isochrone, places, drive times (release v0.2.0)" >/dev/null
gh api "repos/$REPO/milestones" -f title="Phase 2 – Event crawl" -f description="Claude crawl of events per batch of places (release v0.3.0)" >/dev/null
gh api "repos/$REPO/milestones" -f title="Phase 3 – Merge and status" -f description="Dedupe, status lifecycle, cleanup (release v0.4.0)" >/dev/null
gh api "repos/$REPO/milestones" -f title="Phase 4 – Website" -f description="Mobile-first day pages DE/EN (release v0.5.0)" >/dev/null
gh api "repos/$REPO/milestones" -f title="Phase 5 – AI readability" -f description="JSON-LD, llms.txt, raw data, sitemap (release v0.6.0)" >/dev/null
gh api "repos/$REPO/milestones" -f title="Phase 6 – Scheduler and workflow" -f description="Daily workflow, due jobs, commit and deploy (release v0.7.0)" >/dev/null
gh api "repos/$REPO/milestones" -f title="Phase 7 – Discovery" -f description="Find and rate sources with Opus (release v0.8.0)" >/dev/null
gh api "repos/$REPO/milestones" -f title="Phase 8 – Content, domain, wrap-up" -f description="Content pages, custom domain, fork test (release v1.0.0)" >/dev/null

# Assign issues to milestones
for i in 1 2 3; do gh issue edit "$i" --repo "$REPO" --milestone "Phase 0 – Scaffold"; done
for i in 4 5 6; do gh issue edit "$i" --repo "$REPO" --milestone "Phase 1 – Geo"; done
for i in 7 8 9 10; do gh issue edit "$i" --repo "$REPO" --milestone "Phase 2 – Event crawl"; done
for i in 11 12 13; do gh issue edit "$i" --repo "$REPO" --milestone "Phase 3 – Merge and status"; done
for i in 14 15 16 17; do gh issue edit "$i" --repo "$REPO" --milestone "Phase 4 – Website"; done
for i in 18 19 20; do gh issue edit "$i" --repo "$REPO" --milestone "Phase 5 – AI readability"; done
for i in 21 22 23; do gh issue edit "$i" --repo "$REPO" --milestone "Phase 6 – Scheduler and workflow"; done
for i in 24 25; do gh issue edit "$i" --repo "$REPO" --milestone "Phase 7 – Discovery"; done
for i in 26 27 28; do gh issue edit "$i" --repo "$REPO" --milestone "Phase 8 – Content, domain, wrap-up"; done
```
