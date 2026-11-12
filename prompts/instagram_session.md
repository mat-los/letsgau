# Instagram lookup session

Instructions for a scheduled Claude Code session (monthly, after the discovery run). The
pipeline's own AI search rarely shows Instagram usernames; this session uses the web search of
a Claude Code session instead, which does. It only **proposes** candidates: the pipeline checks
every one against the account's profile (Graph API) before it reads it.

## 1. Read what is known

```bash
git fetch origin main data
git checkout -B claude/instagram-candidates-$(date -u +%Y-%m) origin/main
git show origin/data:data/instagram.json > /tmp/instagram.json
git show origin/data:data/places.json > /tmp/places.json
```

- `venues` in `/tmp/instagram.json`: the cafés, bars, clubs and venues discovery saw (name,
  places, website).
- Known names, which are never proposed again: the keys of `accounts` and `rejected` in
  `/tmp/instagram.json`, and the usernames in `instance/sources/instagram.yaml` and
  `instance/sources/instagram_candidates.yaml`.
- The municipalities: the distinct `municipality` (or `name`) values in `/tmp/places.json`.

## 2. Search

Use the WebSearch tool with `mode: "extended"`.

1. For every municipality, run these three searches:
   - `site:instagram.com <municipality> café`
   - `site:instagram.com <municipality> bar club party`
   - `site:instagram.com <municipality> verein konzert veranstaltung`
2. For every venue in `venues` (at most 40 per session; cafés, bars and pubs first), search
   `<name> <municipality> instagram`. Directory pages such as Restaurant Guru, Das Örtliche,
   Tripadvisor or the municipality's business directory often name the account; the venue's
   own website may link it.

Take a username only from an `instagram.com/<username>` link in a result, or from a page that
attributes it to that place. Never guess or construct a name. Leave out:
- accounts of people;
- shops and businesses that never hold events;
- places outside the municipalities;
- clubs or venues with adult or fetish content.

## 3. Propose

Append each new name to `instance/sources/instagram_candidates.yaml` (replace `[]` by a list if
needed):

```yaml
- username: "jeanscafe"
  name: "Jeanscafe"
  places: ["Schongau"]
  website: ""            # the place's own website if known
  found_on: "https://restaurantguru.com/Jeanscafe-Schongau"
```

Then:
- add one line to `CHANGELOG.md` under `Unreleased` (`### Changed`), without the instance's
  name, e.g. "- Instance: N new Instagram candidates for the profile check.";
- run `python -m letsgau check` and `pytest -q`;
- commit (Conventional Commits, English), push the branch;
- open a pull request that lists every candidate with its place and where it was found.

Do not merge the pull request and do not start a workflow run. If nothing new was found, open no
pull request and just report that.
