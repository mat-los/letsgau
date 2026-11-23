# Über {{ site_name }}

{{ site_name }} zeigt alle öffentlich auffindbaren Veranstaltungen in den Gemeinden
{{ municipalities }}, eine Seite pro Tag für die nächsten {{ display_days }} Tage: Konzerte,
Märkte, Sport, Kultur, Vorträge und alles, was es sonst noch gibt.

## Warum es diese Seite gibt

„In Schongau ist nichts los“, das hört man oft. Gleichzeitig verpasst man vieles und
erfährt erst hinterher, dass etwas war.

Ich wollte deshalb eine Seite, auf der ich alles finde, was rund um Schongau los ist.
Daraus wurde ein kleines KI-Projekt: Eine KI sucht die Termine selbstständig zusammen.
Als ich mit den Ergebnissen zufrieden war, habe ich die Seite für alle geöffnet.

## Wer dahintersteckt

- Ein privates Projekt nebenbei, ohne kommerziellen Hintergrund.
- Keine Werbung, kein Sponsor: Die Kosten trage ich selbst.
- Deshalb bleibt das Gebiet vorerst auf Schongau und die Nachbargemeinden beschränkt.
  Ein größerer Radius wäre möglich, kostet aber mehr KI-Rechenzeit.

## Keine Magie

Die Technik dahinter ist einfach. Ich habe keine Zeile Code selbst geschrieben, sondern
die Seite im Gespräch mit [Claude Code](https://claude.com/claude-code) gebaut, einer KI
zum Programmieren. Gehostet wird sie auf GitHub Pages, der gesamte Code ist Open Source
auf GitHub.

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

Vorschläge, Kritik oder fehlt etwas? {% if feedback_endpoint %}Schreib mir gern über das
[Feedback-Formular](../feedback/).{% else %}Schreib an
[{{ contact_email }}](mailto:{{ contact_email }}) oder nutze das
[Feedback-Formular](../feedback/).{% endif %} Der Code ist Open Source:
[{{ repo_url }}]({{ repo_url }}).
