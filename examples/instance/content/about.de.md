# Über {{ site_name }}

{{ site_name }} zeigt alle öffentlich auffindbaren Veranstaltungen bis
{{ drive_time_minutes }} Fahrminuten um {{ region_label }}, eine Seite pro Tag für die
nächsten {{ display_days }} Tage: Konzerte, Märkte, Sport, Kultur, Vorträge und
alles, was es sonst noch gibt.

[TODO: Warum gibt es diese Seite? Erzähle in ein paar Sätzen die Idee und deine Motivation.]

## Wie es funktioniert

Eine KI durchsucht zweimal pro Woche öffentliche Webseiten von Gemeinden,
Vereinen, Veranstaltungsorten und regionalen Kalendern und fasst die Termine kurz
in eigenen Worten zusammen. Jede Veranstaltung verlinkt auf ihre Quelle.

**Instagram:** Viele Vereine, Cafés und Bars kündigen ihre Termine nur dort an.
Deshalb liest die Seite auch die neuesten Beiträge von Instagram-Konten, die sie
kennt: weil eine Webseite darauf verlinkt oder weil sie in unserer Liste stehen.
Das geht nur bei Business- und Creator-Konten, nicht bei privaten.

**Facebook und andere soziale Netzwerke** sind nicht dabei: Sie erlauben nicht,
ihre Seiten automatisch abzufragen. Steht ein Event nur dort, fehlt es hier.
[Fehlt dein Event?](../fehlt-mein-event/)

**Hinweis:** Die Events werden automatisch per KI gesammelt und können falsch oder
veraltet sein. Bitte vor dem Besuch kurz beim Veranstalter prüfen.

## Kontakt

Fehlt etwas oder stimmt etwas nicht? {% if feedback_endpoint %}Schreib uns über das
[Feedback-Formular](../feedback/).{% else %}Schreib an
[{{ contact_email }}](mailto:{{ contact_email }}) oder nutze das
[Feedback-Formular](../feedback/).{% endif %} Der Code ist Open Source:
[{{ repo_url }}]({{ repo_url }}).
