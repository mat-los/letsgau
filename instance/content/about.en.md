# About {{ site_name }}

{{ site_name }} lists every public event found in the municipalities of {{ municipalities }}, one page per
day for the next {{ display_days }} days:
concerts, markets, sports, culture, talks and everything else.

## Why this site exists

"There's nothing going on in Schongau" is something you hear a lot. At the same time, you
miss a lot and only find out afterwards that something happened.

So I wanted a site where I can find everything that's on around Schongau. It became a
small AI project: an AI collects the events on its own. Once I was happy with the
results, I opened the site to everyone.

## Who is behind it

- A private side project with no commercial background.
- No ads, no sponsor: I pay the costs myself.
- That's why the area is limited to Schongau and its neighbouring municipalities for now.
  A larger radius would be possible, but it costs more AI computing time.

## No magic

The technology behind it is simple. I didn't write a single line of code myself; I built
the site by talking to [Claude Code](https://claude.com/claude-code), an AI for
programming. It is hosted on GitHub Pages, and all the code is open source on GitHub.

## How it works

Twice a week an AI searches public websites of municipalities, clubs, venues and
regional calendars and summarizes the events briefly in its own words. Every
event links to its source.

**Instagram:** many clubs, cafés and bars announce their events only there. So
the site also reads the latest posts of Instagram accounts it knows: because a
website links to them or because they are on our list. This only works for
business and creator accounts, not for private ones.

**Facebook and other social networks** are not included: they do not allow their
pages to be read automatically. An event that is only posted there is missing
here.

**Is your event missing, or do you organize events yourself?** How the site finds
events and how yours gets here is explained under [Is my event missing?](../missing-event/)

**Note:** Events are collected automatically by AI and may be wrong or outdated.
Please check with the organizer before you go.

## Contact

Suggestions, criticism or something missing? {% if feedback_endpoint %}Write to me using the
[feedback form](../feedback/).{% else %}Write to [{{ contact_email }}](mailto:{{ contact_email }})
or use the [feedback form](../feedback/).{% endif %} The code is open source:
[{{ repo_url }}]({{ repo_url }}).
