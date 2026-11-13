"""Tests for reading source pages as text and the page cache (no network, no AI)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from letsgau import crawl, flyers, pages
from letsgau.models import EventsFile, PagesFile
from test_crawl import TODAY, FakeRunner, make_config, write_places

WHITELIST = "https://www.schongau.de/veranstaltungen"

LIST_HTML = """<html><head><title>Termine</title><script>var x = 1;</script></head>
<body><nav>Home | Kontakt</nav>
<h1>Veranstaltungen</h1>
<ul>
  <li><b>27.11.2026</b> Schongauer Christkindlmarkt, Marienplatz
      <a href="/veranstaltungen/christkindlmarkt?PHPSESSID=abc123">Details</a></li>
  <li><b>20.11.2026, 20:00</b> Kabarett im Ballenhaus
      <a href="https://www.instagram.com/ballenhaus/">Instagram</a></li>
</ul>
<a href="?page=2">weiter &raquo;</a>
<footer>Impressum</footer></body></html>"""

PAGE_2 = """<html><body><ul><li>05.12.2026 Adventskonzert in der Stadtpfarrkirche, 19:00 Uhr,
mit Chor und Orchester der Stadt Schongau</li></ul></body></html>"""


def serve(
    site: dict[str, str], hits: list[str] | None = None
) -> Callable[[str, int], tuple[bytes, str]]:
    def fetch(url: str, max_bytes: int) -> tuple[bytes, str]:
        if hits is not None:
            hits.append(url)
        if url not in site:
            raise OSError(f"404 {url}")
        return site[url].encode("utf-8"), "text/html; charset=utf-8"

    return fetch


def read(site: dict[str, str]) -> pages.PageText:
    fetch = serve(site)
    return pages.read(WHITELIST, fetch, flyers.Robots(fetch))


def test_text_keeps_content_links_and_follows_next_page() -> None:
    page = read({WHITELIST: LIST_HTML, WHITELIST + "?page=2": PAGE_2})
    text = page.text
    assert "27.11.2026 Schongauer Christkindlmarkt, Marienplatz" in text
    assert "<https://www.schongau.de/veranstaltungen/christkindlmarkt>" in text  # no session id
    assert "<https://www.instagram.com/ballenhaus/>" in text
    assert "--- next page: https://www.schongau.de/veranstaltungen?page=2" in text
    assert "05.12.2026 Adventskonzert" in text
    assert "var x" not in text and "Home | Kontakt" not in text and "Impressum" not in text
    assert page.sha256 == read({WHITELIST: LIST_HTML, WHITELIST + "?page=2": PAGE_2}).sha256


@pytest.mark.parametrize(
    ("site", "reason"),
    [
        ({}, "download failed"),
        ({WHITELIST: '<html><body><div id="app"></div></body></html>'}, "no dates"),
    ],
)
def test_pages_that_cannot_be_read_as_text(site: dict[str, str], reason: str) -> None:
    with pytest.raises(pages.PageError, match=reason):
        read(site)


def test_pdf_and_robots_are_left_to_the_agent() -> None:
    def pdf(url: str, max_bytes: int) -> tuple[bytes, str]:
        if url.endswith("robots.txt"):
            raise OSError("none")
        return b"%PDF-1.7", "application/pdf"

    with pytest.raises(pages.PageError, match="not HTML"):
        pages.read(WHITELIST, pdf, flyers.Robots(pdf))
    site = {
        "https://www.schongau.de/robots.txt": "User-agent: *\nDisallow: /",
        WHITELIST: LIST_HTML,
    }
    fetch = serve(site)
    with pytest.raises(pages.PageError, match="robots"):
        pages.read(WHITELIST, fetch, flyers.Robots(fetch))


# --- the crawl with the page cache --------------------------------------------------------------


def _event(title: str, day: str, url: str, file: str = "page 1") -> dict[str, Any]:
    return {
        "ref": "e1",
        "parent_ref": None,
        "title": {"de": title, "en": title},
        "description": {"de": f"{title}.", "en": f"{title}."},
        "venue": {"name": None, "place": "Schongau", "address": None},
        "schedule": {
            "start_date": day,
            "end_date": None,
            "start_time": "20:00",
            "end_time": None,
            "all_day": False,
            "dates": None,
            "opening_hours": None,
        },
        "primary_tag": "concert",
        "tags": ["concert"],
        "status": "scheduled",
        "postponed_to_date": None,
        "sources": [{"url": url, "primary": True}],
        "file": file,
    }


def _answer(*events: dict[str, Any]) -> dict[str, Any]:
    return {"events": list(events), "checked_sources": [], "instagram_accounts": [], "notes": ""}


@pytest.fixture
def setup(tmp_path: Path) -> tuple[Any, Path]:
    data_dir = tmp_path / "data"
    write_places(data_dir, ["Schongau"])
    return make_config(tmp_path, sweeps=4), data_dir


def test_changed_page_is_read_as_text_then_reused(setup: tuple[Any, Path]) -> None:
    config, data_dir = setup
    site = {WHITELIST: LIST_HTML}
    found = _answer(_event("Kabarett im Ballenhaus", "2026-11-20", WHITELIST))
    first = FakeRunner(answers=[found, _answer()])
    result = crawl.run(config, runner=first, data_dir=data_dir, today=TODAY, fetch=serve(site))

    request = first.requests[0]
    assert request.step == "events batch 1/1 (pages 1)" and request.web is False
    assert "### page 1: https://www.schongau.de/veranstaltungen" in request.prompt
    assert "Kabarett im Ballenhaus" in request.prompt and "no web access" in request.prompt
    assert [r.step for r in first.requests[1:]] == ["events batch 1/1 (search)"]  # no sweep
    assert result.swept == {WHITELIST: "events"}
    cache = PagesFile.model_validate_json((data_dir / "pages.json").read_text())
    entry = cache.pages[WHITELIST]
    assert entry.mode == "text" and len(entry.events) == 1 and "file" not in entry.events[0]

    # next run, page unchanged: no AI call for it, the event comes from the cache
    later = TODAY + timedelta(days=3)
    second = FakeRunner(answers=[_answer()] * 5)
    result = crawl.crawl(config, second, data_dir=data_dir, today=later, fetch=serve(site))
    assert [r.step for r in second.requests] == ["events batch 1/1 (search)"]
    assert [e.title.de for e in result.events] == ["Kabarett im Ballenhaus"]
    assert result.swept == {WHITELIST: "events"} and result.warnings == []

    # the page changes: read again
    site[WHITELIST] = LIST_HTML.replace("20.11.2026", "21.11.2026")
    third = FakeRunner(answers=[_answer(), _answer()])
    crawl.crawl(config, third, data_dir=data_dir, today=later, fetch=serve(site))
    assert third.requests[0].step == "events batch 1/1 (pages 1)"


def test_page_that_cannot_be_read_as_text_goes_to_the_agent(setup: tuple[Any, Path]) -> None:
    config, data_dir = setup
    hits: list[str] = []
    js = {WHITELIST: '<html><body><div id="app"></div></body></html>'}
    runner = FakeRunner(answers=[_answer(), _answer()])
    crawl.run(config, runner=runner, data_dir=data_dir, today=TODAY, fetch=serve(js, hits))
    assert [r.step for r in runner.requests] == [
        "events batch 1/1 (sources 1)",
        "events batch 1/1 (search)",
    ]
    entry = PagesFile.model_validate_json((data_dir / "pages.json").read_text()).pages[WHITELIST]
    assert entry.mode == "agent" and "no dates" in entry.reason

    # not downloaded again until the retry pause is over
    hits.clear()
    crawl.crawl(
        config,
        FakeRunner(answers=[_answer()] * 3),
        data_dir=data_dir,
        today=TODAY,
        fetch=serve(js, hits),
    )
    assert WHITELIST not in hits
    later = TODAY + timedelta(days=pages.RETRY_TEXT_DAYS)
    crawl.crawl(
        config,
        FakeRunner(answers=[_answer()] * 3),
        data_dir=data_dir,
        today=later,
        fetch=serve(js, hits),
    )
    assert WHITELIST in hits


def test_events_on_a_page_read_as_text_need_no_recheck(setup: tuple[Any, Path]) -> None:
    config, data_dir = setup
    gone = _event("Lesung im Ballenhaus", "2026-11-11", WHITELIST + "/lesung")
    crawl.run(
        config,
        runner=FakeRunner(answers=[_answer(gone), _answer()]),
        data_dir=data_dir,
        today=TODAY,
        fetch=serve({WHITELIST: LIST_HTML.replace("</ul>", "<li>11.11.2026 Lesung</li></ul>")}),
    )
    stored = EventsFile.model_validate_json((data_dir / "events.json").read_text())
    assert [e.title.de for e in stored.events] == ["Lesung im Ballenhaus"]

    # the list page no longer shows the talk: no re-check call, the merge counts a miss
    runner = FakeRunner(answers=[_answer()] * 5)
    result = crawl.run(
        config, runner=runner, data_dir=data_dir, today=TODAY, fetch=serve({WHITELIST: LIST_HTML})
    )
    assert [r.step for r in runner.requests] == [
        "events batch 1/1 (pages 1)",
        "events batch 1/1 (search)",
    ]
    assert result.merge is not None and result.merge.missed == [stored.events[0].id]
    assert json.loads((data_dir / "events.json").read_text())["events"][0]["misses"] == 1
