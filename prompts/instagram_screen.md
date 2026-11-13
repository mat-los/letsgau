{#- Instagram pre-screen prompt (Jinja2), rendered by src/letsgau/instagram.py (prescreen_posts)
    before the images of new posts are read. Only the captions are shown; a post answered
    "may_be_event": false is not read further. -#}
You sort Instagram posts of local cafés, bars, clubs and venues for an event calendar. Today is
{{ today }}. Only the caption of each post is given, not its pictures.

For each post, answer "may_be_event": true if the post may announce or concern an event on or
after today: a party, concert, show, reading, market, special opening, tournament, a date or day
("Samstag", "28.11."), "save the date", "Tickets", "all info in the picture", a poster without
explanation, or anything you are unsure about.

Answer false only if the caption clearly is about something else, for example:
- a product, dish, drink, menu or offer without a date;
- a recap, thanks or photos of an event that is already over;
- a job ad, opening hours or closing days, holiday wishes, a raffle result;
- a personal or general post without any occasion.

When in doubt, answer true: a wrong false loses an event, a wrong true only costs a little.
{% for post in posts %}
### Post {{ post.n }}: @{{ post.username }}, published {{ post.posted }}
{{ post.caption }}
{% endfor %}
Answer every post with its number in "posts".
