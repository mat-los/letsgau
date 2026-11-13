"""Machine-readable outputs of the site build (Phase 5, "AI readability").

* :func:`json_ld`: schema.org ``Event`` objects for the day pages
* ``llms.txt``: what the site is, coverage, URL patterns, data links
* ``data/events.json``: public copy of the events without internal fields
* ``data/events.ics``: iCalendar feed to subscribe to
* ``sitemap.xml`` (with hreflang alternates) and ``robots.txt``
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from letsgau.models import Event, EventsFile, EventStatus

if TYPE_CHECKING:
    from letsgau.build import Site

AI_CRAWLERS = ("GPTBot", "ClaudeBot", "Claude-Web", "PerplexityBot", "Google-Extended")
INTERNAL_FIELDS = {"misses"}
STATUS_LD = {
    EventStatus.SCHEDULED: "https://schema.org/EventScheduled",
    EventStatus.UNCONFIRMED: "https://schema.org/EventScheduled",
    EventStatus.CANCELLED: "https://schema.org/EventCancelled",
    EventStatus.POSTPONED: "https://schema.org/EventPostponed",
}


# --- JSON-LD ----------------------------------------------------------------------------


def _iso(day: date, hhmm: str | None, tz: ZoneInfo) -> str:
    if not hhmm:
        return day.isoformat()
    hours, minutes = (int(x) for x in hhmm.split(":"))
    return datetime.combine(day, time(hours, minutes), tz).isoformat()


def event_ld(
    event: Event, lang: str, url: str, tz: ZoneInfo, country: str = "DE"
) -> dict[str, Any]:
    s = event.schedule
    address: dict[str, Any] = {
        "@type": "PostalAddress",
        "addressLocality": event.venue.place,
        "addressCountry": country,
    }
    if event.venue.address:
        address["streetAddress"] = event.venue.address
    data: dict[str, Any] = {
        "@type": "Event",
        "@id": url,
        "url": url,
        "name": getattr(event.title, lang),
        "description": getattr(event.description, lang),
        "startDate": _iso(s.start_date, s.start_time, tz),
        "eventStatus": STATUS_LD[event.status],
        "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
        "inLanguage": lang,
        "location": {
            "@type": "Place",
            "name": event.venue.name or event.venue.place,
            "address": address,
        },
    }
    end_day = s.end_date or (s.start_date if s.end_time else None)
    if end_day:
        data["endDate"] = _iso(end_day, s.end_time, tz)
    primary = next(src.url for src in event.sources if src.primary)
    data["sameAs"] = primary
    return data


def json_ld(rows: Iterable[Any], lang: str, page_url: str, tz: ZoneInfo) -> dict[str, Any]:
    """One ``@graph`` with every event of a day page; program items as ``subEvent``."""
    graph: list[dict[str, Any]] = []
    for row in rows:
        if row.standalone_copy_of is not None:
            continue
        item = event_ld(row.event, lang, f"{page_url}#{row.event.id}", tz)
        if row.children:
            item["subEvent"] = [
                {**event_ld(c, lang, f"{page_url}#{c.id}", tz), "superEvent": {"@id": item["@id"]}}
                for c in row.children
            ]
        if row.event.parent_id:  # program item whose main event is not on this day
            item["superEvent"] = {"@id": f"{page_url}#{row.event.parent_id}"}
        graph.append(item)
    return {"@context": "https://schema.org", "@graph": graph}


# --- public data ----------------------------------------------------------------------------


def public_events(events: Sequence[Event], generated_at: datetime) -> dict[str, Any]:
    payload = EventsFile(updated_at=generated_at, events=list(events)).model_dump(
        mode="json", by_alias=True
    )
    for event in payload["events"]:
        for name in INTERNAL_FIELDS:
            event.pop(name, None)
    return payload


def _ics_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", r"\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> list[str]:
    """Fold content lines at 75 octets (RFC 5545)."""
    out, current = [], ""
    for char in line:
        if len((current + char).encode("utf-8")) > 75:
            out.append(current)
            current = " " + char
        else:
            current += char
    out.append(current)
    return out


def _utc(day: date, hhmm: str, tz: ZoneInfo) -> str:
    hours, minutes = (int(x) for x in hhmm.split(":"))
    moment = datetime.combine(day, time(hours, minutes), tz).astimezone(ZoneInfo("UTC"))
    return moment.strftime("%Y%m%dT%H%M%SZ")


def ics(site: Site, lang: str) -> str:
    """iCalendar feed; events on single dates get one VEVENT per date."""
    tz = ZoneInfo(site.config.site.timezone)
    stamp = site.generated_at.astimezone(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")
    base = site.config.site.base_url.rstrip("/")
    host = base.split("://", 1)[-1].split("/", 1)[0]
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//{site.config.site.name}//letsgau//{lang.upper()}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ics_escape(site.config.site.name)}",
        f"X-WR-TIMEZONE:{site.config.site.timezone}",
    ]
    for event in site.events:
        s = event.schedule
        spans = [(d, d) for d in s.dates] if s.dates else [(s.start_date, s.last_date)]
        for index, (first, last) in enumerate(spans):
            uid = f"{event.id}{'-' + str(index) if s.dates else ''}@{host}"
            lines += ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}"]
            if s.start_time and first == last:
                lines.append(f"DTSTART:{_utc(first, s.start_time, tz)}")
                if s.end_time:
                    lines.append(f"DTEND:{_utc(first, s.end_time, tz)}")
            else:
                lines.append(f"DTSTART;VALUE=DATE:{first:%Y%m%d}")
                lines.append(f"DTEND;VALUE=DATE:{last + timedelta(days=1):%Y%m%d}")
            title = getattr(event.title, lang)
            if event.status is EventStatus.CANCELLED:
                lines.append("STATUS:CANCELLED")
            elif event.status is EventStatus.UNCONFIRMED:
                lines.append("STATUS:TENTATIVE")
            else:
                lines.append("STATUS:CONFIRMED")
            location = ", ".join(
                x for x in (event.venue.name, event.venue.address, event.venue.place) if x
            )
            primary = next(src.url for src in event.sources if src.primary)
            page = f"{base}/{lang}/{first.isoformat()}/#{event.id}"
            lines += [
                f"SUMMARY:{_ics_escape(title)}",
                f"DESCRIPTION:{_ics_escape(getattr(event.description, lang) + chr(10) + primary)}",
                f"LOCATION:{_ics_escape(location)}",
                f"URL:{page}",
                f"CATEGORIES:{','.join(event.tags)}",
                "END:VEVENT",
            ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(part for line in lines for part in _fold(line)) + "\r\n"


# --- llms.txt, sitemap, robots --------------------------------------------------------------


def llms_txt(site: Site, coverage: dict[str, str]) -> str:
    c = site.config
    base = c.site.base_url.rstrip("/")
    default = c.default_language
    tags = "\n".join(f"- `{t.id}`: {t.de} / {t.en}" for t in c.tags)
    langs = ", ".join(c.site.languages)
    # with a form service the address stays off the page (it is in the imprint only)
    contact = (
        f"{base}/{default}/feedback/ (form)"
        if c.site.feedback_form_endpoint
        else c.site.contact_email
    )
    return f"""# {c.site.name}

> Event calendar for the region around {c.region.label}: {coverage.get("en", "")}.
> All public events found on the web, one page per day, in {langs}.

Events are collected automatically by AI (Claude) from public websites and may be
wrong or outdated. Please check with the organizer before you go.

- Municipalities: {", ".join(m.label for m in c.region.municipalities)}
- Days ahead: {c.window.display_days}
- Last updated: {site.generated_at.isoformat()}
- Contact: {contact}

## Pages

- Today: {base}/{default}/
- One page per day and language: {base}/<lang>/<YYYY-MM-DD>/ (lang: {langs})
- Every event has an anchor: {base}/<lang>/<YYYY-MM-DD>/#<event-id>
- Each day page contains all events in plain HTML and as schema.org Event (JSON-LD).

## Data

- All events as JSON: {base}/data/events.json
- Calendar to subscribe (iCalendar): {base}/data/events.ics ({default}),
  per language: {base}/data/events.<lang>.ics
- Sitemap: {base}/sitemap.xml

## Tags

{tags}
"""


def sitemap(site: Site, extra_pages: Sequence[dict[str, str]] = ()) -> str:
    """All day pages plus ``extra_pages`` (lang -> path below the language, e.g. ``impressum/``)."""
    base = site.config.site.base_url.rstrip("/")
    langs = site.config.site.languages
    days = [{lang: f"{d.isoformat()}/" for lang in langs} for d in site.days]
    pages = days + list(extra_pages)
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:xhtml="http://www.w3.org/1999/xhtml">',
    ]
    lastmod = site.generated_at.date().isoformat()
    for page in pages:
        for lang in langs:
            loc = escape(f"{base}/{lang}/{page[lang]}")
            out.append(f"  <url><loc>{loc}</loc><lastmod>{lastmod}</lastmod>")
            for other in langs:
                href = escape(f"{base}/{other}/{page[other]}")
                out.append(f'    <xhtml:link rel="alternate" hreflang="{other}" href="{href}"/>')
            out.append("  </url>")
    out.append("</urlset>")
    return "\n".join(out) + "\n"


def robots_txt(site: Site) -> str:
    base = site.config.site.base_url.rstrip("/")
    if not site.config.site.indexing:
        # Crawling stays allowed: a crawler must fetch a page to see its noindex tag.
        return "# site.indexing is false: every page carries noindex\nUser-agent: *\nAllow: /\n"
    blocks = ["User-agent: *\nAllow: /"]
    blocks += [f"User-agent: {bot}\nAllow: /" for bot in AI_CRAWLERS]
    return "\n\n".join(blocks) + f"\n\nSitemap: {base}/sitemap.xml\n"


def write_all(
    site: Site,
    site_dir: Path,
    coverage: dict[str, str],
    extra_pages: Sequence[dict[str, str]] = (),
) -> None:
    data_dir = site_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "events.json").write_text(
        json.dumps(public_events(site.events, site.generated_at), ensure_ascii=False, indent=1)
        + "\n",
        encoding="utf-8",
    )
    for lang in site.config.site.languages:
        (data_dir / f"events.{lang}.ics").write_text(ics(site, lang), encoding="utf-8", newline="")
    (data_dir / "events.ics").write_text(
        ics(site, site.config.default_language), encoding="utf-8", newline=""
    )
    (site_dir / "llms.txt").write_text(llms_txt(site, coverage), encoding="utf-8")
    (site_dir / "sitemap.xml").write_text(sitemap(site, extra_pages), encoding="utf-8")
    (site_dir / "robots.txt").write_text(robots_txt(site), encoding="utf-8")
