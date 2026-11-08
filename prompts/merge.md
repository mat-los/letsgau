{#- Merge prompt (Jinja2), rendered by src/letsgau/merge.py once per events run.
    Every change must be tested with: python -m letsgau run --job events --dry-run -#}
You check the results of an automatic event crawl for a local event calendar.
{%- if pairs %}

## Task 1: duplicates

Each pair below was found by the crawl and looks similar to an event that is already known.
Decide for each pair whether both entries describe the same event (same occasion at the same
place and time, possibly with a different title, spelling or language). Different performances,
different days of a series that is stored separately, or different events at the same venue are
not the same event.
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
