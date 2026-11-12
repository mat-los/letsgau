# Instagram lookup session

Instructions for a scheduled Claude Code session (monthly, after the discovery run). The
pipeline's own AI search rarely shows Instagram usernames; this session uses the web search of
a Claude Code session instead, which does. It only **proposes** candidates: the pipeline checks
every one against the account's profile (Graph API) before it reads it, so the session merges
its own pull request.

## 1. Read what is known

```bash
git fetch origin main data
# one branch per session; add -2, -3 … if the month's branch exists already
branch=claude/instagram-candidates-$(date -u +%Y-%m); n=2
while git ls-remote --exit-code --heads origin "$branch" >/dev/null; do
  branch=claude/instagram-candidates-$(date -u +%Y-%m)-$n; n=$((n + 1))
done
git checkout -B "$branch" origin/main
git show origin/data:data/instagram.json > /tmp/instagram.json
git show origin/data:data/places.json > /tmp/places.json
git show origin/data:data/sources.json > /tmp/sources.json
```

(Use the scratchpad directory instead of `/tmp` if the session has one.)

- **The venue list**, built from three parts:
  1. `venues` in `/tmp/instagram.json`: the cafés, bars, clubs and venues discovery saw (name,
     places, website); may be missing or short;
  2. the sources in `/tmp/sources.json` with `kind` `venue` or `club` (`name`, `places`, `url`);
  3. **always** the municipality's own directory of restaurants and businesses: find it with
     a search like `<municipality> Essen Trinken Gastgeber` or `<municipality> Gastronomie`
     (often on the municipality's website, e.g. "Schongau erleben > Essen & Trinken >
     Gastgeber"), open it and take every café, bar, pub, beer garden, club and venue.
  Merge the three by name.
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
2. For every place on the venue list that may hold events and whose account is not known yet,
   search `<name> <municipality> instagram`, once per place, at most 40 per session, in this
   order: first **every** café, bar, pub, beer garden and club or disco from the
   municipality's directory (part 3), then music and culture venues, cinemas, theatres and
   event organisers from parts 1 and 2. Directory pages such as Restaurant Guru, Das Örtliche,
   Tripadvisor or the municipality's business directory often name the account; the venue's
   own website may link it.

   Do **not** search per place for: sports clubs and other associations (choirs, scouts,
   charities, rescue services; the municipality's club pages only link its own account, so
   the three searches above find theirs), chains and fast-food restaurants, delivery services,
   shops, hotels and restaurants without events.

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
- run `python -m letsgau check` and `python -m pytest -q` (a bare `pytest` may belong to
  another Python);
- commit (Conventional Commits, English), push the branch;
- open a pull request that lists every candidate with its place and where it was found;
- **merge it** (squash) once the checks above pass. That is safe: the next events run checks
  every candidate against its profile and reads only the confirmed ones.

Do not start a workflow run. If nothing new was found, open no pull request and just report
that.
