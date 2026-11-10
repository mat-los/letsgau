"""Tests for the site build (Phase 4): pages, day logic, guards, no-JS content."""

from __future__ import annotations

import re
import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from letsgau import build
from letsgau.build import (
    BuildError,
    active_on,
    day_view,
    hours_today,
    is_timed,
    sort_key,
    weekdays_in,
)
from letsgau.cli import main
from letsgau.config import load_config
from letsgau.models import Event, EventsFile

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "instance" / "config.yaml"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "site" / "events.json"
NOW = datetime(2026, 11, 13, 20, 0, tzinfo=UTC)  # 21:00 in Berlin, Friday
SATURDAY = date(2026, 11, 14)


@pytest.fixture(scope="module")
def events() -> list[Event]:
    return EventsFile.model_validate_json(FIXTURE.read_text(encoding="utf-8")).events


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("build")
    (root / "data").mkdir()
    shutil.copy(FIXTURE, root / "data" / "events.json")
    build.build(load_config(CONFIG_PATH), data_dir=root / "data", site_dir=root / "site", now=NOW)
    return root / "site"


def page(site: Path, rel: str) -> str:
    return (site / rel / "index.html").read_text(encoding="utf-8")


def ev(**schedule: object) -> Event:
    return Event.model_validate(
        {
            "id": "2026-11-14-x",
            "title": {"de": "X", "en": "X"},
            "description": {"de": "d", "en": "d"},
            "venue": {"place": "Schongau"},
            "schedule": {"start_date": "2026-11-14", **schedule},
            "primary_tag": "concert",
            "tags": ["concert"],
            "sources": [
                {
                    "url": "https://x.example",
                    "primary": True,
                    "first_seen": "2026-11-09",
                    "last_seen": "2026-11-09",
                }
            ],
            "first_seen": "2026-11-09",
            "last_verified": "2026-11-09",
        }
    )


# --- day logic --------------------------------------------------------------------------


def test_weekday_specs() -> None:
    assert weekdays_in("mon-fri") == {0, 1, 2, 3, 4}
    assert weekdays_in("sat,sun") == {5, 6}
    assert weekdays_in("fri-mon") == {4, 5, 6, 0}
    assert weekdays_in("wed") == {2}


def test_active_on_respects_ranges_and_single_dates() -> None:
    market = ev(end_date="2026-11-20")
    assert active_on(market, date(2026, 11, 17)) and not active_on(market, date(2026, 11, 21))
    weekends = ev(end_date="2026-11-22", dates=["2026-11-14", "2026-11-15", "2026-11-22"])
    assert active_on(weekends, date(2026, 11, 15))
    assert not active_on(weekends, date(2026, 11, 18))


def test_untimed_first_then_by_start_time_and_opening_hours() -> None:
    assert is_timed(ev(start_time="09:30"))
    assert not is_timed(ev())  # no time known
    assert not is_timed(ev(start_time="10:00", end_date="2026-11-15"))  # multi-day
    evening, morning = ev(start_time="20:00"), ev(start_time="09:30")
    no_time, multi_day = ev(), ev(start_time="10:00", end_date="2026-11-15")
    order = sorted([evening, multi_day, morning, no_time], key=sort_key)
    assert order[:2] in ([no_time, multi_day], [multi_day, no_time])
    assert order[2:] == [morning, evening]
    hours = ev(
        end_date="2026-11-30",
        opening_hours=[
            {"days": "mon-fri", "from": "16:00", "to": "20:00"},
            {"days": "sat,sun", "from": "11:00", "to": "21:00"},
        ],
    )
    assert hours_today(hours, SATURDAY) == "11:00–21:00"
    assert hours_today(hours, date(2026, 11, 16)) == "16:00–20:00"
    assert hours_today(ev(), SATURDAY) is None


def test_day_view_nests_program_items(events: list[Event]) -> None:
    view = day_view(events, SATURDAY)
    assert view.count == 103
    parent = next(r for r in view.rows if r.children)
    assert parent.event.title.de == "Basteln für Kinder #1"
    assert [c.title.de for c in parent.children] == [
        "Programmpunkt 0",
        "Programmpunkt 1",
        "Programmpunkt 2",
    ]
    copies = [r for r in view.rows if r.standalone_copy_of]
    assert len(copies) == 3 and copies[0].standalone_copy_of is parent.event
    assert len([r for r in view.rows if not r.timed]) == 16  # 8 multi-day + events without time


# --- pages (acceptance #14, #16, #17) ----------------------------------------------------


def test_pages_for_21_days_in_both_languages(site: Path) -> None:
    for lang in ("de", "en"):
        days = sorted(p.name for p in (site / lang).iterdir() if p.is_dir() and p.name[0] == "2")
        assert len(days) == 21
        assert days[0] == "2026-11-13" and days[-1] == "2026-12-03"
        assert (site / lang / "index.html").exists()
    assert (site / "index.html").exists()
    assert (site / "assets" / "style.css").exists() and (site / "assets" / "app.js").exists()
    assert (site / ".nojekyll").exists()


def test_day_page_has_header_elements(site: Path) -> None:
    html = page(site, "de/2026-11-14")
    assert "Bis 5 Fahrminuten um Schongau · 21 Tage im Voraus" in html
    assert '<a href="../../de/2026-11-13/">Heute</a>' in html
    assert 'aria-current="page">Morgen</a>' in html  # Saturday is tomorrow
    assert "Wochenende" not in html  # no weekend button
    assert 'class="dates-nav prev js-only" aria-label="Frühere Tage"' in html
    assert 'class="dates-nav next js-only" aria-label="Spätere Tage"' in html
    assert len(re.findall(r'class="d( we)?"', html)) == 21  # date bar
    assert re.search(r'<span class="dn">14</span><span class="n">103</span>', html)
    assert 'class="chip t-concert" data-tag="concert"' in html
    assert 'data-group="place"' in html
    assert 'data-group="none" aria-pressed="true">Keine</button>' in html
    assert 'id="cats-toggle" aria-controls="chips" aria-expanded="true">Kategorien' in html
    assert "<h2>Vormittag</h2>" not in html and "Ganztägig" not in html
    assert 'rel="prev" href="../../de/2026-11-13/"' in html
    assert 'rel="next" href="../../de/2026-11-15/"' in html


def test_day_list_is_one_list_sorted_by_start_time(site: Path) -> None:
    html = page(site, "de/2026-11-14")
    times = re.findall(r'<details class="ev [^"]*" id="[^"]+"[^>]* data-time="([^"]*)"', html)
    assert times, "no event rows found"
    untimed = [t for t in times if not t]
    assert times[: len(untimed)] == untimed  # untimed (all-day, multi-day, no time) first
    timed = times[len(untimed) :]
    assert timed == sorted(timed) and timed
    assert html.count('<section class="sec">') == 1


def test_every_event_is_in_the_html_without_js(site: Path, events: list[Event]) -> None:
    html = page(site, "de/2026-11-14")
    for event in events:
        assert event.title.de in html
        assert f'id="{event.id}"' in html
    english = page(site, "en/2026-11-14")
    assert "Konzert der Stadtkapelle #51 (EN)" in english
    assert 'data-group="none" aria-pressed="true">None</button>' in english


def test_status_and_program_display(site: Path) -> None:
    html = page(site, "de/2026-11-14")
    assert (
        'class="ev st-cancelled' in html and '<span class="badge cancelled">Abgesagt</span>' in html
    )
    assert '<span class="badge postponed">Verschoben</span>' in html
    assert 'href="../../de/2026-11-21/#2026-11-21-ev7b">Neuer Termin: 2026-11-21</a>' in html
    assert '<span class="badge unconfirmed">Nicht mehr bestätigt</span>' in html
    assert '<span class="prog">heute 3 Programmpunkte</span>' in html
    assert "im Rahmen von Basteln für Kinder #1" in html
    assert "noch bis 19.11. · Heute geöffnet 11:00–21:00" in html
    assert "+1 weitere Quellen" in html


def test_metadata_and_links(site: Path) -> None:
    html = page(site, "de/2026-11-14")
    assert (
        "<title>Was ist los in und um Schongau am Samstag, 14. November 2026"
        " · Let&#39;s Gau</title>" in html
    )
    assert '<link rel="canonical" href="https://letsgau.de/de/2026-11-14/">' in html
    assert '<link rel="alternate" hreflang="en" href="https://letsgau.de/en/2026-11-14/">' in html
    assert '<a href="../../en/2026-11-14/" hreflang="en"' in html
    index = page(site, "de")
    assert (
        'data-index="1"' in index
        and '<link rel="canonical" href="https://letsgau.de/de/2026-11-13/">' in index
    )
    assert 'href="../assets/style.css"' in index
    assert "Zuletzt aktualisiert: <time" in html and "13.11. 21:00" in html


def test_assets_are_small_and_local(site: Path) -> None:
    js = (site / "assets" / "app.js").stat().st_size
    assert js < 10_000
    html = page(site, "de/2026-11-14")
    assert "https://fonts" not in html and "cdn" not in html.lower()


def test_empty_data_builds_pages_with_notice(tmp_path: Path) -> None:
    build.build(
        load_config(CONFIG_PATH), data_dir=tmp_path / "none", site_dir=tmp_path / "site", now=NOW
    )
    assert "Für diesen Tag sind noch keine Events bekannt." in page(tmp_path / "site", "de")


# --- guards -------------------------------------------------------------------------------


def test_guard_rejects_placeholders_and_foreign_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_config(CONFIG_PATH)
    with pytest.raises(BuildError, match="placeholders"):
        build.guard(config, 'name: "{{SITE_NAME}}"')
    monkeypatch.setenv("GITHUB_REPOSITORY_OWNER", "someone-else")
    with pytest.raises(BuildError, match="someone-else"):
        build.guard(config, "")
    monkeypatch.setenv("GITHUB_REPOSITORY_OWNER", "MAT-LOS")
    build.guard(config, "")  # case-insensitive match passes


def test_cli_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_REPOSITORY_OWNER", raising=False)
    (tmp_path / "data").mkdir()
    shutil.copy(FIXTURE, tmp_path / "data" / "events.json")
    assert main(["--config", str(CONFIG_PATH), "run", "--job", "build"]) == 0
    assert "build: 44 day pages" in capsys.readouterr().out
    assert (tmp_path / "site" / "de" / "index.html").exists()
    monkeypatch.setenv("GITHUB_REPOSITORY_OWNER", "someone-else")
    assert main(["--config", str(CONFIG_PATH), "run", "--job", "build"]) == 1
    assert "someone-else" in capsys.readouterr().err
