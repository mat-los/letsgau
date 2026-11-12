"""Tests for the machine-readable outputs (Phase 5): JSON-LD, llms.txt, JSON, ICS, sitemap."""

from __future__ import annotations

import html
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from letsgau import build
from letsgau.config import Config, load_config
from letsgau.models import EventsFile

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "instance" / "config.yaml"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "site" / "events.json"
NOW = datetime(2026, 11, 13, 20, 0, tzinfo=UTC)


def public_config(indexing: bool = True) -> Config:
    """The instance config with ``site.indexing`` set explicitly."""
    config = load_config(CONFIG_PATH)
    site = config.site.model_copy(update={"indexing": indexing})
    return config.model_copy(update={"site": site})


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("feeds")
    (root / "data").mkdir()
    shutil.copy(FIXTURE, root / "data" / "events.json")
    build.build(public_config(), data_dir=root / "data", site_dir=root / "site", now=NOW)
    return root / "site"


def ld_graph(site: Path, rel: str) -> list[dict]:
    text = (site / rel / "index.html").read_text(encoding="utf-8")
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', text, re.S)
    assert match
    return json.loads(match.group(1))["@graph"]


# --- acceptance #18: JSON-LD ------------------------------------------------------------


def test_json_ld_has_one_event_per_main_event(site: Path) -> None:
    graph = ld_graph(site, "de/2026-11-14")
    assert len(graph) == 100  # program items are subEvents
    for item in graph:
        assert item["@type"] == "Event"
        assert item["name"] and item["startDate"] and item["description"]
        assert item["location"]["@type"] == "Place"
        assert item["location"]["address"]["addressLocality"]
        assert item["eventStatus"].startswith("https://schema.org/Event")
        assert item["url"].startswith("https://letsgau.de/de/2026-11-14/#")


def test_json_ld_status_times_and_program(site: Path) -> None:
    graph = {i["name"]: i for i in ld_graph(site, "de/2026-11-14")}
    timed = next(i for i in graph.values() if "T" in i["startDate"])
    assert timed["startDate"].endswith("+01:00")  # Europe/Berlin in November
    statuses = {i["eventStatus"] for i in graph.values()}
    assert "https://schema.org/EventCancelled" in statuses
    assert "https://schema.org/EventPostponed" in statuses
    parent = graph["Basteln für Kinder #1"]
    assert [s["name"] for s in parent["subEvent"]] == [
        "Programmpunkt 0",
        "Programmpunkt 1",
        "Programmpunkt 2",
    ]
    assert parent["subEvent"][0]["superEvent"] == {"@id": parent["@id"]}
    english = ld_graph(site, "en/2026-11-14")
    assert english[0]["inLanguage"] == "en" and english[0]["name"].endswith("(EN)")


# --- acceptance #19: generated files -------------------------------------------------------


def test_llms_txt(site: Path) -> None:
    text = (site / "llms.txt").read_text(encoding="utf-8")
    assert text.startswith("# Let's Gau")
    assert "Events within 5 min of Schongau and in Altenstadt · 21 days ahead" in text
    assert "https://letsgau.de/<lang>/<YYYY-MM-DD>/" in text
    assert "https://letsgau.de/data/events.json" in text and "events.ics" in text
    assert "- `concert`: Konzert / Concert" in text
    assert "collected automatically by AI" in text
    assert "Last updated: 2026-11-13T20:00:00+00:00" in text


def test_public_events_json_drops_internal_fields(site: Path) -> None:
    data = json.loads((site / "data" / "events.json").read_text(encoding="utf-8"))
    assert len(data["events"]) == 103
    assert all("misses" not in e for e in data["events"])
    EventsFile.model_validate({**data, "events": [{**e, "misses": 0} for e in data["events"]]})


def test_ics_feed(site: Path) -> None:
    raw = (site / "data" / "events.ics").read_bytes()
    text = raw.decode("utf-8")
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n")
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))
    assert text.count("BEGIN:VEVENT") == 103
    assert "STATUS:CANCELLED" in text and "STATUS:TENTATIVE" in text
    assert "DTSTART:20261114T083000Z" in text  # 09:30 in Berlin
    assert "DTSTART;VALUE=DATE:20261112\r\nDTEND;VALUE=DATE:20261120" in text  # multi-day
    assert (site / "data" / "events.en.ics").exists()
    unfolded = text.replace("\r\n ", "")
    assert "SUMMARY:Eishockey: EC Peiting – EV Füssen" in unfolded


def test_sitemap_and_robots(site: Path) -> None:
    sitemap = (site / "sitemap.xml").read_text(encoding="utf-8")
    assert sitemap.count("<url>") == 52  # (21 days + 4 content pages + feedback) x 2 languages
    assert "<loc>https://letsgau.de/de/impressum/</loc>" in sitemap
    assert 'hreflang="en" href="https://letsgau.de/en/imprint/"' in sitemap
    assert (
        '<xhtml:link rel="alternate" hreflang="en" href="https://letsgau.de/en/2026-11-14/"/>'
        in sitemap
    )
    robots = (site / "robots.txt").read_text(encoding="utf-8")
    for bot in ("GPTBot", "ClaudeBot", "PerplexityBot", "Google-Extended"):
        assert f"User-agent: {bot}\nAllow: /" in robots
    assert "Sitemap: https://letsgau.de/sitemap.xml" in robots
    assert 'name="robots"' not in (site / "de" / "2026-11-14" / "index.html").read_text()


def test_indexing_false_hides_the_site(tmp_path: Path) -> None:
    built = build.build(
        public_config(indexing=False), data_dir=tmp_path / "none", site_dir=tmp_path, now=NOW
    )
    noindex = '<meta name="robots" content="noindex">'
    pages = ("index.html", "de/index.html", "de/2026-11-14/index.html", "en/imprint/index.html")
    for page in pages:
        assert noindex in (tmp_path / page).read_text(encoding="utf-8"), page
    robots = (tmp_path / "robots.txt").read_text(encoding="utf-8")
    assert "Allow: /" in robots  # crawlers must still see the noindex tag
    assert "Sitemap" not in robots and "GPTBot" not in robots
    assert any("noindex" in warning for warning in built.warnings)


# --- acceptance #20: plain HTML contains every event ---------------------------------------


def test_plain_html_contains_every_event(site: Path) -> None:
    page = html.unescape((site / "de" / "2026-11-14" / "index.html").read_text(encoding="utf-8"))
    text = re.sub(r"<script.*?</script>", "", page, flags=re.S)  # what a non-JS client reads
    events = EventsFile.model_validate_json(FIXTURE.read_text(encoding="utf-8")).events
    for event in events:
        assert event.title.de in text
        assert event.description.de in text


def test_page_metadata_for_sharing(site: Path) -> None:
    page = (site / "de" / "2026-11-14" / "index.html").read_text(encoding="utf-8")
    assert '<meta name="description" content="103 Events in und um Schongau' in page
    assert '<meta property="og:title"' in page and '<meta property="og:url"' in page
    assert 'hreflang="x-default"' in page
