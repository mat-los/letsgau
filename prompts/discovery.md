{#- Discovery prompt (Jinja2), rendered per batch of municipalities by src/letsgau/discovery.py.
    Every change must be tested with: python -m letsgau run --job discovery --dry-run --limit-places 2 -#}
You build the source list of a local event calendar around {{ region.label }} (Bavaria, Germany).
Find the websites where public events in the municipalities below are announced, and rate them.

## Municipalities

{% for group in groups -%}
- {{ group.name }}{% if group.districts %} (including {{ group.districts | join(", ") }}){% endif %}
{% endfor %}
## What to look for

Event calendars of the municipality and the tourist office, regional calendars, clubs (sports,
music, culture, traditional costume clubs, fire brigade), parishes, cultural centres, cinemas,
theatres, music venues, cafés and bars with events, libraries, adult education centres, local
newspapers and their event pages. Facebook and Instagram pages do not count.

You have at most {{ max_searches }} web searches for this batch. Open every promising page.

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
{%- if known %}

## Already known sources

These are known already. Report one again only if your rating differs or the URL moved.
{% for source in known %}
- {{ source.url }}{% if source.rating %} (rated {{ source.rating }}/5){% endif %}
{%- endfor %}
{%- endif %}
{%- if blocked_domains %}

Never report sources on these domains: {{ blocked_domains | join(", ") }}.
{%- endif %}

Report websites that should never be used (spam, copied or outdated listings) as
"blacklist_candidates" with domain and reason. Use "notes" for anything the operator should know.

Answer only with the JSON object described by the schema.
