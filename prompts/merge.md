{#- Merge prompt (Jinja2), rendered by src/letsgau/merge.py once per events run.
    Every change must be tested with: python -m letsgau run --job events --dry-run -#}
You check the results of an automatic event crawl for a local event calendar.
{%- if pairs %}

## Task 1: duplicates

Each pair below was found by the crawl and looks similar to an event that is already known.
Decide for each pair whether both entries describe the same event (same occasion at the same
place and time, possibly with a different title, spelling or language). Different performances,
different days of a series that is stored separately, or different events at the same venue are
not the same event. But if one entry is a series (a date range) and the other is a single date of
that same series (same organiser, same activity, same place and time), they are the same event:
answer true, and the series is kept.
{% for pair in pairs %}
Pair {{ pair.number }}:
- A: {{ pair.new.title.de }} | {{ pair.new.schedule.start_date }}{% if pair.new.schedule.end_date and pair.new.schedule.end_date != pair.new.schedule.start_date %} to {{ pair.new.schedule.end_date }}{% endif %}{% if pair.new.schedule.start_time %} {{ pair.new.schedule.start_time }}{% endif %} | {{ pair.new.venue.name or "" }}, {{ pair.new.venue.place }} | {{ pair.new.description.de }}
- B: {{ pair.old.title.de }} | {{ pair.old.schedule.start_date }}{% if pair.old.schedule.end_date and pair.old.schedule.end_date != pair.old.schedule.start_date %} to {{ pair.old.schedule.end_date }}{% endif %}{% if pair.old.schedule.start_time %} {{ pair.old.schedule.start_time }}{% endif %} | {{ pair.old.venue.name or "" }}, {{ pair.old.venue.place }} | {{ pair.old.description.de }}
{%- endfor %}

Answer every pair in "pairs" with its number and "same_event": true or false.
{%- else %}

There are no duplicate pairs; answer "pairs" with an empty list.
{%- endif %}
{%- if new_events and topics %}

## Task 2: excluded topics

The calendar never lists events on these topics:
{% for topic in topics -%}
- {{ topic }}
{% endfor %}
List in "excluded" every event below that clearly falls under one of these topics, with its id
and the topic. Only clear cases; when in doubt, keep the event.
{% for event in new_events %}
- {{ event.id }} | {{ event.title.de }} | {{ event.venue.name or "" }}, {{ event.venue.place }} | {{ event.description.de }}
{%- endfor %}
{%- else %}

Answer "excluded" with an empty list.
{%- endif %}
{%- if classify %}

## Task 3: kind and main category

For every entry below, answer in "classified" with its id:

- "kind":
  - "event": a dated happening open to the public with its own programme, which someone
    would plan to attend: concerts, festivals, markets, performances, films, talks, parties,
    tournaments and first-team matches, guided tours, exhibitions, special services
    (Christmette, a Festgottesdienst with choir or orchestra, a service with a concert,
    children's Bible days, processions, pilgrimages).
  - "routine": the same thing every week or month without a programme of its own: regular
    worship, prayer groups, Bible study, Eucharistic adoration, weekly club, youth or senior
    groups (games afternoons, lunch, handicrafts), regular training, public skating hours,
    lower-league, reserve and youth matches.
  - "course": a course or workshop that needs a booking (cooking, brewing, language, yoga,
    painting, vhs courses), also with several dates.
  - "offer": not an event: a menu special or theme dinner without a programme (e.g. "Halbes
    Hendl", "Schnitzeltag"), a discount or free entry, opening hours of a museum, pool or
    sauna, an appointment or service (a badge test, a collection, a drop-off), helpers
    preparing an event.
- "primary_tag": the main category, one of: {{ tag_ids | join(", ") }}. Keep the given one
  unless it is clearly wrong. "seniors" only for events made specifically for older people.
  "religion" as the main category only for worship and religious occasions; a church concert is
  "concert". A commemoration (Volkstrauertag, Totenehrung) is not "art-culture": use "religion"
  when it is a service, otherwise "talk-workshop". A Christmas market is "market-festival".
{% for event in classify %}
- {{ event.id }} | {{ event.title.de }} | {{ event.venue.name or "" }}, {{ event.venue.place }} | tags: {{ event.tags | join(", ") }} (main: {{ event.primary_tag }}){% if event.schedule.dates %} | {{ event.schedule.dates | length }} dates{% endif %} | {{ event.description.de }}
{%- endfor %}
{%- else %}

Answer "classified" with an empty list.
{%- endif %}
