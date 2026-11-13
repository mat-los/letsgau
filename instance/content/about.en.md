# About {{ site_name }}

{{ site_name }} lists every public event found in {{ municipalities }}, one page per
day for the next {{ display_days }} days:
concerts, markets, sports, culture, talks and everything else.

[TODO: Why does this site exist? Tell the idea and your motivation in a few sentences.]

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
here. [Is your event missing?](../missing-event/)

**Note:** Events are collected automatically by AI and may be wrong or outdated.
Please check with the organizer before you go.

## Contact

Something missing or wrong? {% if feedback_endpoint %}Write to us using the
[feedback form](../feedback/).{% else %}Write to [{{ contact_email }}](mailto:{{ contact_email }})
or use the [feedback form](../feedback/).{% endif %} The code is open source:
[{{ repo_url }}]({{ repo_url }}).
