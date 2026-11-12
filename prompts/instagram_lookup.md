{#- Instagram lookup prompt (Jinja2), rendered once per discovery batch after its passes by
    src/letsgau/discovery.py (render_lookup_prompt). Every candidate it returns is checked
    against the account's profile before it is kept. -#}
You find the Instagram accounts of cafés, bars, pubs, clubs and venues for a local event calendar
around {{ region.label }} (Bavaria, Germany). Many of them announce their events only on Instagram;
the calendar reads the public posts of their accounts.

## Municipalities

{% for group in groups -%}
- {{ group.name }}{% if group.districts %} (including {{ group.districts | join(", ") }}){% endif %}
{% endfor %}
## Step 1: the list of places

Make a list of at most {{ max_places }} places in these municipalities that may hold events: cafés,
bars, pubs, beer gardens, clubs and discos, music and culture venues, breweries and inns with
events, active clubs. Start with these known ones:
{% for source in venues %}
- {{ source.name or source.domain }} ({{ source.url }})
{%- else %}
- (none known yet)
{%- endfor %}

Add the cafés, bars, pubs and venues from the municipality's own directory of restaurants and
businesses (on its website, often "Essen & Trinken", "Gastronomie" or "Unternehmen A-Z"), the
tourist office's listings and similar directories. Open those pages and read the names.

## Step 2: one search per place

For every place on the list, run one search "<name> <municipality> instagram", as far as your limit
of {{ max_searches }} searches allows; start with the cafés, bars and pubs. Instagram pages cannot
be opened here, but the account name is often visible:
- in a search result that links instagram.com/<username>,
- on a directory page that names the account (Restaurant Guru, Das Örtliche, Tripadvisor,
  Gelbe Seiten, the municipality's business directory),
- on the place's own website (a link to instagram.com/<username>).

## Step 3: report

Report every account name you found in "instagram_candidates": "username" without @, "name" the
place, "places" its municipality (exactly as written above), "website" the place's own website if
you know it, otherwise "". Report a name even if only one directory gives it: the pipeline checks
every name against the account's profile and drops it if it belongs to another place. But never
invent or guess a name.
{%- if known_accounts %}

These accounts are known already; do not report them again:
{{ known_accounts | join(", ") }}
{%- endif %}

Use "notes" for places whose account you could not find. Answer only with the JSON object
described by the schema.
