{#- Discovery prompt (Jinja2), rendered per batch of municipalities (and per focus pass) by
    src/letsgau/discovery.py.
    Every change must be tested with: python -m letsgau run --job discovery --dry-run --limit-places 2 -#}
You build the source list of a local event calendar around {{ region.label }} (Bavaria, Germany).
Find the websites where public events in the municipalities below are announced, and rate them.
The calendar is only useful if it is close to complete, so be thorough: a small town has dozens of
clubs, parishes and venues that announce events. Missing a source is worse than checking a useless one.

## Municipalities

{% for group in groups -%}
- {{ group.name }}{% if group.districts %} (including {{ group.districts | join(", ") }}){% endif %}
{% endfor %}
{%- if focus %}
## Focus of this pass: {{ focus.title }}

This is one of several passes; other passes cover the other kinds of sources. In this pass, look
for these kinds of sources only:
{% for category in focus.categories %}
- {{ category }}
{%- endfor %}

Work through this checklist for every municipality, in German. Adapt a query if it finds nothing
useful, and follow up on every lead (links on pages you open, names mentioned in articles):
{% for group in groups %}
{%- for query in focus.queries %}
- "{{ query | replace("{m}", group.name) }}"
{%- endfor %}
{%- endfor %}
{%- if focus.extra %}

{{ focus.extra }}
{%- endif %}
{%- if instagram and focus.id == "venues" %}

Many cafés, bars, clubs and small venues announce their events only on Instagram. Instagram pages
themselves cannot be opened here, but search results list them. Run these searches for every
municipality exactly as written (the site: operator limits the results to Instagram profiles) and
take the account names from the instagram.com/<username> links in the results:
{% for group in groups %}
- "site:instagram.com {{ group.name }} café"
- "site:instagram.com {{ group.name }} bar club party"
- "site:instagram.com {{ group.name }} verein konzert veranstaltung"
{%- endfor %}

Report every café, bar, pub, club or venue of these municipalities that you find there or anywhere
else in this pass (linked on its website, in its Google listing, or from a search like
"<name> <municipality> Instagram") in "instagram_accounts", also if it has no website of its own.

Then, for the cafés, bars, pubs and venues of this pass whose account you did not find that way,
search "<name> <municipality> instagram" (one search each, as far as your limit allows). Directory
sites such as Restaurant Guru, Das Örtliche, Tripadvisor or the municipality's business directory
often name the account. Report such names in "instagram_candidates" with the place's name, its
municipality and its own website if you know it (otherwise ""). The pipeline checks every candidate
against the account's profile, so report a name you saw attributed to the place even if you cannot
confirm it; but never invent one.
{%- endif %}
{%- else %}
## What to look for

Event calendars of the municipality and the tourist office, regional calendars, clubs (sports,
music, culture, traditional costume clubs, fire brigade), parishes, cultural centres, cinemas,
theatres, music venues, cafés and bars with events, libraries, adult education centres, local
newspapers and their event pages.
{%- endif %}

Facebook and Instagram pages do not count as sources{% if instagram %} (but see "instagram_accounts"
below){% endif %}. You have at most {{ max_searches }} web searches for
this {{ "pass" if focus else "batch" }}; use most of them. Fetching pages does not count against
the limit, so open every promising page and read it.

For every source, check and report:
- "has_current_events": does it list events in the coming weeks?
- "dates_as_text": are date, time and place written as text (not only in images or PDFs)?
- "update_frequency": weekly, monthly, rare or unknown.
- "rating": 5 = current events with date and place as text, updated often; 4 = good but less
  complete or less frequent; 3 = useful sometimes; 2 = rarely useful; 1 = useless or misleading
  (outdated, only images, spam).
- "kind": municipality, club, venue, calendar, press or other.
- "places": the municipalities above that the source covers (exactly as written above).
- "url": the page that lists the events (not just the home page, if there is a separate page).
- "note": one short sentence, e.g. "paginated with ?page=2" or "events only as flyer images".

Report every page you checked, also the weak ones (rating 1 or 2), so that nobody checks them again.
{%- if known %}

## Already known sources

These are known and were checked recently; the event crawl reads the good ones twice a week. Do
not open them, do not search for them and do not report them again. Only if you notice on another
page that one has moved or is gone, report it with the new URL or rating 1. Spend your searches on
sources that are not in this list.
{% for source in known %}
- {{ source.url }}{% if source.rating %} (rated {{ source.rating }}/5){% endif %}
{%- endfor %}
{%- endif %}
{%- if recheck %}

## Known sources to check again

These were rated a while ago. Open each once and report it again with a fresh rating.
{% for source in recheck %}
- {{ source.url }}{% if source.rating %} (rated {{ source.rating }}/5){% endif %}
{%- endfor %}
{%- endif %}
{%- if blocked_domains %}

Never report sources on these domains: {{ blocked_domains | join(", ") }}.
{%- endif %}

Report websites that should never be used (spam, copied or outdated listings) as
"blacklist_candidates" with domain and reason.
{%- if focus %}

In "coverage", give one entry per category of this pass and municipality: "found" if you found at
least one usable source, "nothing_found" if you searched and there is none (e.g. no cinema in the
village), "not_checked" if you ran out of searches; "note" says briefly what you found or tried.
{%- endif %}
{%- if instagram %}

In "instagram_accounts", list the Instagram accounts of local organisers (clubs, venues, cafés,
bars, parishes, the municipality) in the municipalities above that you came across: linked on pages
you read, or in search results. "username" is the account name without @ (from
instagram.com/<username>), "name" is the organiser, "places" the municipalities (exactly as written
above). Only accounts you actually saw in a link to the account or in its own search result; never
guess a name (a name a directory attributes to a place goes to "instagram_candidates"). Cafés,
bars and pubs count even if you don't know whether they hold events. Leave out accounts of people,
of shops and businesses that never hold events, and of organisers outside these municipalities.
{%- if focus and focus.id != "venues" %}
Answer "instagram_candidates" with an empty list.
{%- elif not focus %}
Report names that only a directory attributes to a place (not confirmed by a link or its own
search result) in "instagram_candidates" with name, municipality and the place's own website.
{%- endif %}
{%- endif %}
Use "notes" for anything the operator should know (blocked sites, PDFs only, sources to check by hand).

Answer only with the JSON object described by the schema.
