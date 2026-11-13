"""Tests for the event crawl and the Phase 2 merge. AI answers are recorded fixtures."""

from __future__ import annotations

import copy
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from letsgau import crawl, merge
from letsgau.ai_runner import AiError, AiRequest, AiResult
from letsgau.cli import main
from letsgau.config import Config, load_config
from letsgau.crawl import (
    Candidate,
    CrawlError,
    PlaceIndex,
    WhitelistEntry,
    event_id,
    make_batches,
    slugify,
    to_event,
)
from letsgau.models import Event, EventsFile, EventStatus, Place, PlacesFile, State, Usage

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "crawl"
CONFIG_PATH = REPO_ROOT / "instance" / "config.yaml"
TODAY = date(2026, 11, 9)  # crawl window: 2026-11-09 .. 2026-12-07 (28 days)
PLACE_NAMES = [
    "Schongau",
    "Altenstadt",
    "Peiting",
    "Hohenpeißenberg",
    "Peißenberg",
    "Steingaden",
    "Rottenbuch",
    "Burggen",
]


def _answer() -> dict[str, Any]:
    data = json.loads((FIXTURES / "claude_cli_result.json").read_text(encoding="utf-8"))
    return data["structured_output"]


def make_config(tmp_path: Path, sweeps: int = 0, store_days: int = 28, **limits: Any) -> Config:
    """Instance config with fixture lists; ``sweeps=0`` is one combined call per batch.

    ``store_days`` defaults to the crawl window (28), as these tests were written for it.
    """
    config = load_config(CONFIG_PATH)
    config = config.model_copy(
        update={
            # one open-search call of 13 searches in every run unless a test sets otherwise
            "depth": config.depth.model_copy(
                update={
                    "crawl_sources_per_request": sweeps,
                    "crawl_searches_per_request": 13,
                    "open_search": "every_run",
                }
            ),
            "window": config.window.model_copy(update={"store_days": store_days}),
        }
    )
    sources = config.sources.model_copy(
        update={
            "whitelist_file": str(FIXTURES / "whitelist.yaml"),
            "blacklist_file": str(FIXTURES / "blacklist.yaml"),
            "flyers_file": "",
            "instagram_file": "",
        }
    )
    return config.model_copy(
        update={"sources": sources, "limits": config.limits.model_copy(update=limits)}
    )


def write_places(
    data_dir: Path, names: list[str] = PLACE_NAMES, municipalities: dict[str, str] | None = None
) -> None:
    municipalities = municipalities or {}
    places = [
        Place(
            name=n,
            osm_id=f"node/{i + 1}",
            place_type="village",
            lat=47.8,
            lon=10.9,
            drive_time_minutes=float(i * 3 + 2),
            municipality=municipalities.get(n, n),
        )
        for i, n in enumerate(names)
    ]
    data_dir.mkdir(parents=True, exist_ok=True)
    pf = PlacesFile(
        fingerprint="sha256:test",
        generated_at="2026-11-08T04:00:00Z",
        center={"lat": 47.811, "lon": 10.896},
        drive_time_minutes=30,
        place_types=["city", "town", "village"],
        places=places,
    )
    (data_dir / "places.json").write_text(pf.model_dump_json(), encoding="utf-8")


class FakeRunner:
    """Returns recorded answers; records every request."""

    def __init__(
        self,
        answers: list[dict[str, Any]] | None = None,
        fail: set[int] | None = None,
        searches: int = 14,
        fetched: list[str] | None = None,
        merge_answer: dict[str, Any] | None = None,
    ) -> None:
        self.answers = answers
        self.fail = fail or set()
        self.searches = searches
        self.fetched = fetched or []
        self.requests: list[AiRequest] = []
        self.merge_requests: list[AiRequest] = []
        self.merge_answer = merge_answer or {"pairs": [], "excluded": []}

    def run(self, request: AiRequest) -> AiResult:
        if request.step == "merge":
            self.merge_requests.append(request)
            return AiResult(data=copy.deepcopy(self.merge_answer), usage=Usage(requests=1))
        self.requests.append(request)
        n = len(self.requests)
        if n in self.fail:
            raise AiError("claude failed: subtype='error_during_execution'")
        if self.answers is None:
            data = _answer() if n == 1 else {"events": [], "checked_sources": [], "notes": ""}
        else:
            data = self.answers[n - 1]
        return AiResult(
            data=copy.deepcopy(data),
            usage=Usage(
                requests=1,
                web_searches=self.searches,
                input_tokens=1000,
                output_tokens=100,
                duration_s=10,
            ),
            fetched_urls=self.fetched,
        )


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return make_config(tmp_path)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    write_places(d)
    return d


def by_title(events: list[Event]) -> dict[str, Event]:
    return {e.title.de: e for e in events}


# --- ids, places, batches ---------------------------------------------------------------


def test_slugify_and_event_id() -> None:
    assert slugify("Schongauer Weihnachtsmarkt") == "schongauer-weihnachtsmarkt"
    assert slugify("Füße & Größe – Ölmühle!") == "fuesse-groesse-oelmuehle"
    assert event_id(date(2026, 11, 28), "Weihnachtsmarkt", "Schongau") == (
        "2026-11-28-weihnachtsmarkt-schongau"
    )
    # place already in the title: not repeated
    assert event_id(date(2026, 11, 28), "Schongauer Christkindlmarkt", "Schongau") == (
        "2026-11-28-schongauer-christkindlmarkt"
    )
    assert len(slugify("x" * 30 + " " + "y" * 40)) <= 50


def test_place_index_resolves_variants() -> None:
    index = PlaceIndex(
        [
            Place(name=n, osm_id="node/1", place_type="town", lat=0, lon=0)
            for n in ["Landsberg am Lech", "Peiting"]
        ]
    )
    assert index.resolve("peiting") == "Peiting"
    assert index.resolve("Landsberg") == "Landsberg am Lech"
    assert index.resolve("Peiting (Obb.)") is None or index.resolve("Peiting (Obb.)") == "Peiting"
    assert index.resolve("München") is None
    assert index.resolve(None) is None


def test_batches_assign_whitelist_and_known_events() -> None:
    places = [
        Place(name=n, osm_id="node/1", place_type="village", lat=0, lon=0) for n in PLACE_NAMES
    ]
    whitelist = [
        WhitelistEntry(url="https://a.de", places=["Rottenbuch"]),
        WhitelistEntry(url="https://regional.de"),
    ]
    batches = make_batches(crawl.group_places(places), 6, whitelist, [])
    assert [len(b.groups) for b in batches] == [6, 2]
    assert [w.url for w in batches[0].whitelist] == ["https://regional.de"]
    assert [w.url for w in batches[1].whitelist] == ["https://a.de"]


def _place(name: str, minutes: float, municipality: str | None = None) -> Place:
    return Place(
        name=name,
        osm_id="node/1",
        place_type="village",
        lat=0,
        lon=0,
        drive_time_minutes=minutes,
        municipality=municipality or name,
    )


def test_places_are_grouped_by_municipality() -> None:
    places = [
        _place("Schongau", 2.6),
        _place("Altenstadt", 7.8),
        _place("Peiting", 8.2),
        _place("Herzogsägmühle", 10.2, "Peiting"),
        _place("Hausen", 10.4, "Altenstadt"),
        _place("Birkland", 12.3, "Peiting"),
        _place("Leeder", 24.8, "Fuchstal"),  # municipality without a place node
    ]
    groups = crawl.group_places(places)
    assert [g.name for g in groups] == ["Schongau", "Altenstadt", "Peiting", "Fuchstal"]
    peiting = groups[2]
    assert peiting.districts == ["Herzogsägmühle", "Birkland"]
    assert peiting.drive_time_minutes == 8.2
    assert groups[3].districts == ["Leeder"]
    index = PlaceIndex(places)
    assert index.resolve("Fuchstal") == "Fuchstal"
    assert index.resolve("herzogsägmühle") == "Herzogsägmühle"


def test_prompt_lists_municipalities_with_districts(config: Config, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    write_places(
        data_dir,
        ["Schongau", "Peiting", "Herzogsägmühle", "Birkland", "Altenstadt"],
        municipalities={"Herzogsägmühle": "Peiting", "Birkland": "Peiting"},
    )
    runner = FakeRunner()
    result = crawl.crawl(config, runner, data_dir=data_dir, today=TODAY, limit_places=2)
    prompt = runner.requests[0].prompt
    assert "- Peiting (5 min from Schongau), including Herzogsägmühle, Birkland" in prompt
    assert "Altenstadt" not in prompt
    assert result.batches[0].places == ["Schongau", "Peiting"]
    assert result.skipped_places == ["Altenstadt"]
    # church events also get the religion tag (the instance has that category)
    assert 'also gets "religion" as one of its tags' in prompt


# --- validation of single candidates ----------------------------------------------------


def _candidate(**changes: Any) -> Candidate:
    item = copy.deepcopy(_answer()["events"][2])  # EC Peiting – EV Füssen
    for key, value in changes.items():
        target = item
        *path, last = key.split("__")
        for part in path:
            target = target[part]
        target[last] = value
    return Candidate.model_validate(item)


def _to_event(config: Config, cand: Candidate, **kw: Any) -> Event:
    index = PlaceIndex(
        [Place(name=n, osm_id="node/1", place_type="village", lat=0, lon=0) for n in PLACE_NAMES]
    )
    return to_event(
        cand, config=config, places=index, blocked=["example-spam.de"], today=TODAY, **kw
    )


def test_valid_candidate_becomes_event(config: Config) -> None:
    event = _to_event(config, _candidate())
    assert event.id == "2026-11-13-ec-peiting-ev-fuessen"
    assert event.venue.place == "Peiting"
    assert event.schedule.start_time == "20:00"
    assert event.tags == ["sports"] and event.primary_tag == "sports"
    assert [s.primary for s in event.sources] == [True]
    assert event.first_seen == event.last_verified == TODAY
    assert event.status is EventStatus.SCHEDULED


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"schedule__start_date": "2026-12-08"}, "after store_days"),
        ({"schedule__start_date": "2026-11-01"}, "already over"),
        ({"venue__place": "München"}, "not in the area"),
        ({"tags": ["sports", "ice-hockey"]}, "unknown tags"),
        ({"tags": ["sports", "party", "concert", "outdoor"]}, "more than 3 tags"),
        (
            {"sources": [{"url": "https://www.example-spam.de/x", "primary": True}]},
            "no valid source",
        ),
        ({"sources": []}, "no valid source"),
    ],
)
def test_invalid_candidates_are_rejected(
    config: Config, changes: dict[str, Any], reason: str
) -> None:
    with pytest.raises(ValueError, match=reason):
        _to_event(config, _candidate(**changes))


def test_window_edges_are_inclusive(config: Config) -> None:
    assert _to_event(config, _candidate(schedule__start_date="2026-12-07"))
    ongoing = _candidate(schedule__start_date="2026-11-01", schedule__end_date="2026-11-09")
    assert _to_event(config, ongoing).schedule.start_date == date(2026, 11, 1)


def test_sources_cleaned_and_one_primary(config: Config) -> None:
    cand = _candidate(
        sources=[
            {"url": "https://example-spam.de/a", "primary": True},
            {"url": "https://www.ecpeiting.de/spielplan", "primary": False},
            {"url": "https://www.ecpeiting.de/spielplan", "primary": False},
            {"url": "ftp://x.de/y", "primary": False},
            {"url": "https://www.kreisbote.de/x", "primary": False},
        ]
    )
    event = _to_event(config, cand)
    assert [s.url for s in event.sources] == [
        "https://www.ecpeiting.de/spielplan",
        "https://www.kreisbote.de/x",
    ]
    assert [s.primary for s in event.sources] == [True, False]


def test_description_is_truncated(config: Config) -> None:
    long = "Wort " * 80
    event = _to_event(config, _candidate(description={"de": long, "en": long}))
    assert len(event.description.de) <= config.events.description_max_chars
    assert event.description.de.endswith("…")


# --- whole answers ------------------------------------------------------------------------


def test_answer_validation_drops_invalid_and_links_children(config: Config) -> None:
    index = PlaceIndex(
        [Place(name=n, osm_id="node/1", place_type="village", lat=0, lon=0) for n in PLACE_NAMES]
    )
    events, rejected = crawl.events_from_answer(
        _answer(), config=config, places=index, blocked=["example-spam.de"], today=TODAY
    )
    titles = by_title(events)
    assert set(titles) == {
        "Schongauer Christkindlmarkt",
        "Kinderchor am Christkindlmarkt",
        "EC Peiting – EV Füssen",
        "Malabend im Café",
        "Leonhardiritt",
        "Kabarett im Ballenhaus",
    }
    reasons = {r.title: r.reason for r in rejected}
    assert "after store_days" in reasons["Weit entfernter Markt"]
    assert "no valid source" in reasons["Spam-Event"]
    assert "not in the area" in reasons["Konzert in München"]
    assert "unknown tags" in reasons["Falsches Tag"]

    market = titles["Schongauer Christkindlmarkt"]
    choir = titles["Kinderchor am Christkindlmarkt"]
    assert choir.parent_id == market.id
    assert market.schedule.end_date == date(2026, 12, 13)
    assert market.schedule.opening_hours[0].from_ == "16:00"  # type: ignore[index]
    assert titles["Leonhardiritt"].status is EventStatus.CANCELLED
    kabarett = titles["Kabarett im Ballenhaus"]
    assert kabarett.status is EventStatus.POSTPONED
    assert kabarett.postponed_to is None  # new date not in this answer


def test_postponed_links_to_new_date(config: Config) -> None:
    answer = _answer()
    moved = copy.deepcopy(answer["events"][5])
    moved.update(ref="e6b", status="scheduled", postponed_to_date=None)
    moved["schedule"]["start_date"] = "2026-12-04"
    answer["events"][5]["postponed_to_date"] = "2026-12-04"
    answer["events"].append(moved)
    index = PlaceIndex(
        [Place(name=n, osm_id="node/1", place_type="village", lat=0, lon=0) for n in PLACE_NAMES]
    )
    events, _ = crawl.events_from_answer(
        answer, config=config, places=index, blocked=[], today=TODAY
    )
    old = next(e for e in events if e.status is EventStatus.POSTPONED)
    assert old.postponed_to == "2026-12-04-kabarett-im-ballenhaus-schongau"


def test_schema_errors_are_rejected_not_fatal(config: Config) -> None:
    answer = _answer()
    answer["events"][0]["schedule"]["start_date"] = "28.11.2026"
    answer["events"].append({"nonsense": True})
    index = PlaceIndex([Place(name="Schongau", osm_id="node/1", place_type="town", lat=0, lon=0)])
    _, rejected = crawl.events_from_answer(
        answer, config=config, places=index, blocked=[], today=TODAY
    )
    assert any(r.reason.startswith("schema: schedule.start_date") for r in rejected)
    assert any(r.reason.startswith("schema:") and r.title == "?" for r in rejected)


# --- the stage (acceptance criteria of Phase 2) -------------------------------------------


def test_run_writes_valid_events_for_28_days(config: Config, data_dir: Path) -> None:
    runner = FakeRunner()
    result = crawl.run(config, runner=runner, data_dir=data_dir, today=TODAY)

    stored = EventsFile.model_validate_json((data_dir / "events.json").read_text("utf-8"))
    assert {e.id for e in stored.events} == {e.id for e in result.events}
    assert len(stored.events) == 6
    window_end = date(2026, 12, 7)
    for event in stored.events:
        assert event.schedule.last_date >= TODAY
        assert event.schedule.start_date <= window_end
        # DE and EN texts, tags from the list, exactly one primary tag, one primary source
        assert event.title.de and event.title.en
        assert event.description.de and event.description.en
        assert set(event.tags) <= set(config.tag_ids)
        assert event.primary_tag in event.tags
        assert sum(s.primary for s in event.sources) == 1
        assert not any("example-spam.de" in s.url for s in event.sources)

    state = State.model_validate_json((data_dir / "state.json").read_text("utf-8"))
    assert state.last_usage["events"].requests == 2
    assert "events" in state.last_run
    # 8 places in batches of 6 -> 2 AI calls with the sonnet model
    assert [r.model for r in runner.requests] == ["claude-sonnet-5-5"] * 2
    assert "Schongau" in runner.requests[0].prompt and "Burggen" in runner.requests[1].prompt
    assert runner.requests[0].blocked_domains == ["example-spam.de"]


def test_whitelist_sources_are_queried(config: Config, data_dir: Path) -> None:
    runner = FakeRunner()
    result = crawl.crawl(config, runner, data_dir=data_dir, today=TODAY)
    assert "https://www.schongau.de/veranstaltungen" in runner.requests[0].prompt
    assert result.batches[0].whitelist_missing == []
    assert not any("whitelist" in w for w in result.warnings)


def test_unchecked_whitelist_source_is_reported(config: Config, data_dir: Path) -> None:
    answer = _answer()
    answer["checked_sources"] = []
    runner = FakeRunner(answers=[answer, {"events": [], "checked_sources": [], "notes": ""}])
    result = crawl.crawl(config, runner, data_dir=data_dir, today=TODAY)
    assert result.batches[0].whitelist_missing == ["https://www.schongau.de/veranstaltungen"]
    assert any("whitelist source not checked" in w for w in result.warnings)
    # an API-reported fetch counts as checked
    runner = FakeRunner(answers=[answer, answer], fetched=["https://schongau.de/veranstaltungen/"])
    assert crawl.crawl(config, runner, data_dir=data_dir, today=TODAY).warnings == []


def test_dry_run_and_limit_places(
    config: Config, data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = FakeRunner()
    result = crawl.run(
        config, runner=runner, data_dir=data_dir, today=TODAY, dry_run=True, limit_places=3
    )
    assert len(runner.requests) == 1
    assert result.skipped_places == PLACE_NAMES[3:]
    prompt = runner.requests[0].prompt
    assert "Schongau" in prompt and "Peiting" in prompt and "Hohenpeißenberg" not in prompt
    assert not (data_dir / "events.json").exists()
    assert not (data_dir / "state.json").exists()
    out = capsys.readouterr().out
    assert "dry run, nothing written" in out and "5 municipalities skipped" in out


def test_search_budget_is_spread_and_enforced(tmp_path: Path, data_dir: Path) -> None:
    config = make_config(tmp_path, max_web_searches_per_run=20, places_per_request=2)
    runner = FakeRunner(searches=9)
    result = crawl.crawl(config, runner, data_dir=data_dir, today=TODAY)
    # 4 batches: 20 // 4 = 5, then (20 - 9) // 3 = 3, then 2 left -> skipped
    assert [r.max_searches for r in runner.requests] == [5, 3]
    assert [r.error for r in result.batches[2:]] == [
        "skipped: max_web_searches_per_run used up"
    ] * 2


def test_runtime_limit_stops_new_batches(config: Config, data_dir: Path) -> None:
    ticks = iter([0, 0, 10_000, 10_000])
    result = crawl.crawl(
        config, FakeRunner(), data_dir=data_dir, today=TODAY, clock=lambda: next(ticks)
    )
    assert result.batches[1].error == "skipped: max_runtime_minutes reached"


def test_failed_batch_is_reported_others_continue(config: Config, data_dir: Path) -> None:
    result = crawl.crawl(config, FakeRunner(fail={2}), data_dir=data_dir, today=TODAY)
    assert len(result.events) == 6
    assert any("batch 2: claude failed" in w for w in result.warnings)


def test_all_batches_failing_raises(config: Config, data_dir: Path) -> None:
    with pytest.raises(CrawlError, match="every batch failed"):
        crawl.crawl(config, FakeRunner(fail={1, 2}), data_dir=data_dir, today=TODAY)


def test_missing_places_raises(config: Config, tmp_path: Path) -> None:
    with pytest.raises(CrawlError, match="run `letsgau run --job geo` first"):
        crawl.crawl(config, FakeRunner(), data_dir=tmp_path / "empty", today=TODAY)


def test_same_event_from_two_batches_is_stored_once(config: Config, data_dir: Path) -> None:
    second = _answer()
    second["events"] = [second["events"][2]]
    second["events"][0]["sources"] = [{"url": "https://www.kreisbote.de/derby", "primary": True}]
    result = crawl.crawl(
        config, FakeRunner(answers=[_answer(), second]), data_dir=data_dir, today=TODAY
    )
    derby = by_title(result.events)["EC Peiting – EV Füssen"]
    assert [s.url for s in derby.sources] == [
        "https://www.ecpeiting.de/spielplan",
        "https://www.kreisbote.de/derby",
    ]


def test_known_events_go_into_the_prompt(config: Config, data_dir: Path) -> None:
    crawl.run(config, runner=FakeRunner(), data_dir=data_dir, today=TODAY)
    runner = FakeRunner()
    crawl.crawl(config, runner, data_dir=data_dir, today=TODAY)
    prompt = runner.requests[0].prompt
    assert "Known events to re-check (until 2026-11-16)" in prompt
    assert "2026-11-13 20:00 | EC Peiting – EV Füssen | Peiting" in prompt
    assert "Already known later events" in prompt
    assert "Schongauer Christkindlmarkt" in prompt


# --- store_days: events beyond the crawl window are kept ---------------------------------


def test_events_up_to_store_days_are_kept(tmp_path: Path, data_dir: Path) -> None:
    config = make_config(tmp_path, store_days=180)
    assert _to_event(config, _candidate(schedule__start_date="2027-05-08"))  # day 180
    with pytest.raises(ValueError, match="after store_days"):
        _to_event(config, _candidate(schedule__start_date="2027-05-09"))
    result = crawl.run(config, runner=FakeRunner(), data_dir=data_dir, today=TODAY)
    assert "Weit entfernter Markt" in by_title(result.events)  # 2027-03-01, now kept
    runner = FakeRunner()
    crawl.crawl(config, runner, data_dir=data_dir, today=TODAY)
    prompt = runner.requests[0].prompt
    assert "up to 2027-05-08 that you see on a page you read anyway" in prompt
    # the prompt lists known events only up to the crawl window (2026-12-07)
    assert "Weit entfernter Markt" not in prompt
    assert "Schongauer Christkindlmarkt" in prompt


# --- source sweeps and open search (depth.crawl_sources_per_request) --------------------


def _sources_file(data_dir: Path, urls: list[str]) -> None:
    from letsgau.models import Source, SourceOrigin, SourcesFile

    sources = [
        Source(
            url=url,
            domain=crawl.domain_of(url),
            name=f"source {i}",
            places=["Schongau"],
            origin=SourceOrigin.DISCOVERY,
            rating=4,
            first_seen=TODAY,
        )
        for i, url in enumerate(urls)
    ]
    file = SourcesFile(updated_at="2026-11-09T00:00:00Z", sources=sources)
    (data_dir / "sources.json").write_text(file.model_dump_json(), encoding="utf-8")


def test_sweeps_split_known_sources_and_add_an_open_search(tmp_path: Path, data_dir: Path) -> None:
    config = make_config(tmp_path, sweeps=2, places_per_request=8)
    urls = [f"https://club{i}.example/termine" for i in range(14)]  # more than the old cap of 12
    _sources_file(data_dir, urls)
    crawl.run(config, runner=FakeRunner(), data_dir=data_dir, today=TODAY)  # known events
    empty = {"events": [], "checked_sources": [], "notes": ""}
    runner = FakeRunner(answers=[empty] * 10)  # the sweeps find none of the known events
    result = crawl.crawl(config, runner, data_dir=data_dir, today=TODAY)

    # 1 whitelist entry + 14 known sources in sweeps of 2 -> 8 sweeps, then the re-check of
    # the known events of the next 7 days, then the search
    steps = [r.step for r in runner.requests]
    assert steps[0] == "events batch 1/1 (sources 1)" and steps[7] == "events batch 1/1 (sources 8)"
    assert steps[8:] == ["events batch 1/1 (recheck)", "events batch 1/1 (search)"]
    first, recheck, search = (runner.requests[i].prompt for i in (0, 8, 9))
    assert "read the sources below\ncompletely" in first
    assert "- https://www.schongau.de/veranstaltungen" in first
    assert "- https://club0.example/termine (source 0)" in first
    assert "club1.example" not in first
    assert "Known events to re-check" not in first  # sweeps report everything they see
    assert all(r.max_searches == crawl.SWEEP_MAX_SEARCHES for r in runner.requests[:8])

    assert "do not read them again:\n- https://www.schongau.de/veranstaltungen" in search
    assert "- https://club13.example/termine" in search
    months = '"Schongau Veranstaltungen November 2026 86956", "Schongau Veranstaltungen Dez'
    assert months in search
    assert '"Schongau Heimspiel OR Turnier 86956"' in search
    assert '"Schongau Jugendzentrum OR Juze 86956"' in search
    assert "Known events to re-check" not in search  # the search has only one job
    assert "Already known later events" in search
    assert "Known events to re-check (until 2026-11-16)" in recheck
    assert "Report only events from that list" in recheck
    assert "Run the queries of this checklist" not in recheck
    assert runner.requests[8].max_searches == crawl.SWEEP_MAX_SEARCHES
    assert result.batches[0].calls == 10 and result.batches[0].error is None
    assert "Schongau" in result.covered_places
    assert result.swept["https://club13.example/termine"] == "empty"
    assert set(urls) <= set(result.swept)  # every source of a call that answered


def test_recheck_skips_events_the_source_reading_found_again(
    tmp_path: Path, data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = make_config(tmp_path, sweeps=4, places_per_request=8)
    crawl.run(config, runner=FakeRunner(), data_dir=data_dir, today=TODAY)  # known events
    stored = EventsFile.model_validate_json((data_dir / "events.json").read_text("utf-8"))
    deep = [
        e
        for e in stored.events
        if e.parent_id is None
        and e.schedule.start_date <= TODAY + timedelta(days=config.window.deep_check_days)
    ]
    assert len(deep) >= 2

    # all known events found again by the sweep: no re-check call at all
    runner = FakeRunner(answers=[_answer(), {"events": []}])
    crawl.crawl(config, runner, data_dir=data_dir, today=TODAY)
    assert [r.step for r in runner.requests] == [
        "events batch 1/1 (sources 1)",
        "events batch 1/1 (search)",
    ]
    assert "skipped, all" in capsys.readouterr().err

    # the sweep finds all but one: the re-check gets only that one
    answer = _answer()
    missing = deep[0]
    answer["events"] = [c for c in answer["events"] if c["title"]["de"] != missing.title.de]
    runner = FakeRunner(answers=[answer, {"events": []}, {"events": []}])
    crawl.crawl(config, runner, data_dir=data_dir, today=TODAY)
    steps = [r.step for r in runner.requests]
    assert steps == [
        "events batch 1/1 (sources 1)",
        "events batch 1/1 (recheck)",
        "events batch 1/1 (search)",
    ]
    recheck = runner.requests[1].prompt.split("## Known events to re-check")[1].split("##")[0]
    assert missing.title.de in recheck
    assert recheck.count("\n- ") == 1


def test_failed_sweep_keeps_other_events_but_not_coverage(tmp_path: Path, data_dir: Path) -> None:
    config = make_config(tmp_path, sweeps=4, places_per_request=8)
    search = _answer()
    search["notes"] = "search done"
    runner = FakeRunner(answers=[{}, search], fail={1})  # 1 sweep (fails), then the search
    result = crawl.crawl(config, runner, data_dir=data_dir, today=TODAY)
    assert len(result.events) == 6  # from the search call
    report = result.batches[0]
    assert report.calls == 1 and report.error.startswith("[sources 1] claude failed")
    assert report.notes == "[search] search done"
    assert result.covered_places == set()  # nothing is "missed" after a partial failure
    assert any("batch 1: [sources 1] claude failed" in w for w in result.warnings)


def test_search_queries_cover_every_month_and_district() -> None:
    group = crawl.PlaceGroup(
        name="Peiting",
        places=[
            Place(name="Peiting", osm_id="node/1", place_type="town", lat=0, lon=0),
            Place(name="Birkland", osm_id="node/2", place_type="village", lat=0, lon=0),
        ],
    )
    [(name, queries)] = crawl.search_queries([group], date(2026, 12, 20), date(2027, 1, 17))
    assert name == "Peiting"
    assert "Peiting Veranstaltungen Dezember 2026" in queries
    assert "Peiting Veranstaltungen Januar 2027" in queries
    assert "Birkland Veranstaltungen" in queries
    assert len(queries) == 2 + 2 + len(crawl.SEARCH_TOPICS) + 1


def test_open_search_is_split_and_a_short_call_is_not_repeated(
    tmp_path: Path, data_dir: Path
) -> None:
    config = make_config(tmp_path, sweeps=4, places_per_request=8)
    config = config.model_copy(
        update={"depth": config.depth.model_copy(update={"crawl_searches_per_request": 39})}
    )
    # the first search call stops after 2 of 13 searches: it is not asked again
    runner = FakeRunner(answers=[{}, _answer(), {}, {}])
    searches = iter([8, 2, 13, 13])
    original = runner.run

    def run(request: AiRequest) -> AiResult:
        result = original(request)
        result.usage = result.usage.model_copy(update={"web_searches": next(searches)})
        return result

    runner.run = run  # type: ignore[method-assign]
    result = crawl.crawl(config, runner, data_dir=data_dir, today=TODAY)
    steps = [r.step for r in runner.requests]
    assert steps == [
        "events batch 1/1 (sources 1)",
        "events batch 1/1 (search 1)",
        "events batch 1/1 (search 2)",
        "events batch 1/1 (search 3)",
    ]
    parts = [r.prompt for r in runner.requests[1:]]
    assert (
        '"Schongau Veranstaltungen 86956"' in parts[0]
        and '"Schongau Veranstaltungen 86956"' not in parts[1]
    )
    assert all("A first attempt" not in p for p in parts)
    assert result.warnings == []


def test_split_queries_keeps_every_query_once() -> None:
    queries = [("A", [f"a{i}" for i in range(7)]), ("B", [f"b{i}" for i in range(5)])]
    parts = crawl.split_queries(queries, 3)
    assert [sum(len(q) for _, q in p) for p in parts] == [4, 4, 4]
    flat = [q for p in parts for _, items in p for q in items]
    assert flat == [*queries[0][1], *queries[1][1]]
    assert parts[1] == [("A", ["a4", "a5", "a6"]), ("B", ["b0"])]


# --- merge (Phase 2) ----------------------------------------------------------------------


def test_second_run_updates_without_duplicates(config: Config, data_dir: Path) -> None:
    crawl.run(config, runner=FakeRunner(), data_dir=data_dir, today=TODAY)
    later = date(2026, 11, 12)
    second = _answer()
    second["events"][2]["sources"].append(
        {"url": "https://www.kreisbote.de/derby", "primary": False}
    )
    crawl.run(
        config, runner=FakeRunner(answers=[second, {"events": []}]), data_dir=data_dir, today=later
    )
    stored = EventsFile.model_validate_json((data_dir / "events.json").read_text("utf-8"))
    assert len(stored.events) == 6
    derby = by_title(stored.events)["EC Peiting – EV Füssen"]
    assert derby.first_seen == TODAY
    assert derby.last_verified == later
    assert [s.url for s in derby.sources][-1] == "https://www.kreisbote.de/derby"
    assert derby.sources[0].last_seen == later and derby.sources[0].primary


def test_merge_deletes_events_ended_before_yesterday(config: Config) -> None:
    index = PlaceIndex(
        [Place(name=n, osm_id="node/1", place_type="village", lat=0, lon=0) for n in PLACE_NAMES]
    )
    events, _ = crawl.events_from_answer(
        _answer(), config=config, places=index, blocked=[], today=TODAY
    )
    stored = merge.apply(None, events, today=TODAY)
    after = merge.apply(stored, [], today=date(2026, 11, 19))
    titles = {e.title.de for e in after.events}
    assert "EC Peiting – EV Füssen" not in titles  # 2026-11-13, ended before 2026-11-18
    assert "Malabend im Café" in titles  # 2026-11-18 = yesterday, kept
    assert "Schongauer Christkindlmarkt" in titles  # runs until 2026-12-13


# --- CLI ----------------------------------------------------------------------------------


def test_cli_events_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    write_places(tmp_path / "data")
    for name in ("whitelist.yaml", "blacklist.yaml"):
        target = tmp_path / "instance" / "sources" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((FIXTURES / name).read_text("utf-8"), "utf-8")
    runner = FakeRunner()
    monkeypatch.setattr(crawl, "make_runner", lambda _config: runner)
    args = [
        "--config",
        str(CONFIG_PATH),
        "run",
        "--job",
        "events",
        "--dry-run",
        "--limit-places",
        "3",
    ]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "crawl: " in out and "dry run" in out
    # instance depth: one source sweep over the fixture whitelist, then the open search
    # (13 searches) in one call, in every other run (depth.open_search: alternate)
    search = (
        ["events batch 1/1 (search)"] if crawl.open_search_due("alternate", date.today()) else []
    )
    assert [r.step for r in runner.requests] == [
        "events batch 1/1 (sources 1)",
        *search,
    ]
    assert not (tmp_path / "data" / "events.json").exists()


def test_cli_events_without_places_fails_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["--config", str(CONFIG_PATH), "run", "--job", "events", "--dry-run"]) == 1
    assert "run `letsgau run --job geo` first" in capsys.readouterr().err


def test_recorded_answer_passes_validation(
    config: Config, data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The real answer from the Crawl check run validates completely; notes are printed."""
    recorded = json.loads((FIXTURES / "claude_cli_recorded_schongau.json").read_text("utf-8"))
    answer = recorded["structured_output"]
    runner = FakeRunner(answers=[answer, {"events": [], "checked_sources": [], "notes": ""}])
    result = crawl.run(
        config, runner=runner, data_dir=data_dir, today=date(2026, 11, 8), dry_run=True
    )
    assert [e.id for e in result.events] == [
        "2026-11-10-singkreis-schongau",
        "2026-11-11-oekumenische-fahrzeugsegnung-der-neuen-rvo-busse-schongau",
    ]
    assert result.batches[0].rejected == []
    assert result.batches[0].whitelist_missing == []
    assert "note from batch 1: The city of Schongau pages showed no event listings" in (
        capsys.readouterr().out
    )


# --- whitelist templates and source check -------------------------------------------------


def test_whitelist_template_expands_per_municipality(config: Config, tmp_path: Path) -> None:
    template = WhitelistEntry(url="https://cal.example/{municipality_slug}/", note="regional")
    limited = WhitelistEntry(url="https://x.example/{municipality_slug}", places=["Peiting"])
    assert template.is_template
    assert [e.url for e in template.expand(["Schongau", "Hohenpeißenberg"])] == [
        "https://cal.example/schongau/",
        "https://cal.example/hohenpeissenberg/",
    ]
    assert [e.places for e in template.expand(["Schongau"])] == [["Schongau"]]
    assert [e.url for e in limited.expand(["Schongau", "Peiting"])] == ["https://x.example/peiting"]

    places = [_place(n, i * 5.0) for i, n in enumerate(PLACE_NAMES)]
    batches = make_batches(crawl.group_places(places), 6, [template], [])
    assert [w.url for w in batches[1].whitelist] == [
        "https://cal.example/rottenbuch/",
        "https://cal.example/burggen/",
    ]


def test_cli_sources_lists_expanded_urls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_places(tmp_path / "data", ["Schongau", "Peiting"])
    assert (
        main(
            [
                "--config",
                str(CONFIG_PATH),
                "sources",
                "--places",
                str(tmp_path / "data" / "places.json"),
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "https://veranstaltungen.pfaffen-winkel.de/peiting/  (Peiting)" in out
    assert "reaktor" not in out

    monkeypatch.setattr(crawl, "check_url", lambda url: "404" if "peiting" in url else "200")
    assert (
        main(
            [
                "--config",
                str(CONFIG_PATH),
                "sources",
                "--check",
                "--places",
                str(tmp_path / "data" / "places.json"),
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "  404  https://veranstaltungen.pfaffen-winkel.de/peiting/" in out
    total = out.count("  200  ") + out.count("  404  ")  # the instance whitelist may grow
    assert f"{total - 1} of {total} sources reachable" in out


def test_rotate_queries_starts_elsewhere_each_run() -> None:
    queries = [("A", [f"a{i}" for i in range(6)]), ("B", [f"b{i}" for i in range(5)])]
    flat = [q for _, items in queries for q in items]
    monday, thursday = date(2026, 11, 16), date(2026, 11, 19)
    starts = []
    for day in (monday, thursday):
        rotated = crawl.rotate_queries(queries, day)
        order = [q for _, items in rotated for q in items]
        assert sorted(order) == sorted(flat)  # every query once
        assert order == flat[flat.index(order[0]) :] + flat[: flat.index(order[0])]  # rotation
        starts.append(flat.index(order[0]))
    assert abs(starts[0] - starts[1]) in (5, 6)  # about half a list apart
    assert crawl.rotate_queries(queries, monday) == crawl.rotate_queries(queries, monday)
    assert crawl.rotate_queries([], monday) == []


def test_open_search_every_other_run_with_postcodes(tmp_path: Path, data_dir: Path) -> None:
    config = make_config(tmp_path, sweeps=4)
    config = config.model_copy(
        update={"depth": config.depth.model_copy(update={"open_search": "alternate"})}
    )
    monday, thursday = TODAY, TODAY + timedelta(days=3)
    assert crawl.open_search_due("alternate", monday) != crawl.open_search_due(
        "alternate", thursday
    )
    assert crawl.open_search_due("every_run", thursday) and not crawl.open_search_due("off", monday)
    steps = {}
    for day in (monday, thursday):
        runner = FakeRunner(answers=[{"events": []}] * 20)
        crawl.crawl(config, runner, data_dir=data_dir, today=day)
        steps[day] = [r.step for r in runner.requests]
    searched = [day for day, s in steps.items() if any("(search" in step for step in s)]
    assert searched == [monday]
    postcodes = {"schongau": "86956"}
    plan = crawl.search_queries(
        crawl.group_places(
            [Place(name="Schongau", osm_id="node/1", place_type="town", lat=47.8, lon=10.9)]
        ),
        TODAY,
        TODAY + timedelta(days=28),
        postcodes,
    )
    assert plan[0][1][0] == "Schongau Veranstaltungen 86956"
    assert all(q.endswith(" 86956") for q in plan[0][1])


@pytest.mark.parametrize(
    ("title", "excluded"),
    [
        ("Gartenbauverein Altenstadt/Schwabniederhofen, Jahresabschluss", True),
        ("Nikolausfeier Schützenverein Schwabniederhofen", True),
        ("Schützenverein Altenstadt, Preisverteilung Nikolausschießen", True),
        ("Nikolausfeier der Trachtenjugend", True),
        ("Weihnachtsfeier des Trachtenvereins", True),
        ("Krippenspiel-Probe", True),
        ("Gospelchor-Probe", True),
        ("3. Konfinachmittag", True),
        ("80. Jahreshauptversammlung Skiclub Schongau", True),
        ("Übungsschießen Schützenverein Jägerhaus", True),
        ("Fotogruppe Altenstadt: Clubabend", False),
        ("Adventsbasar", False),
        ("LET IT SWING – Weihnachtskonzert der SOG-BigBand", False),
        ("Öffentliche Generalprobe", False),
        ("Probetraining Tischtennis", False),
        ("Jugendtreff", False),
    ],
)
def test_instance_title_patterns(config: Config, title: str, excluded: bool) -> None:
    assert (config.excluded_title(title) is not None) is excluded


def test_title_patterns_drop_new_and_remove_stored_events(config: Config) -> None:
    config = config.model_copy(update={"excluded_title_patterns": [r"\bProbe\b"]})
    item = _answer()["events"][0] | {"title": {"de": "Krippenspiel-Probe", "en": "Rehearsal"}}
    with pytest.raises(ValueError, match="excluded title pattern"):
        to_event(
            Candidate.model_validate(item),
            config=config,
            places=PlaceIndex(
                [Place(name="Schongau", osm_id="node/1", place_type="town", lat=47.8, lon=10.9)]
            ),
            blocked=[],
            today=TODAY,
        )
    kept = to_event(
        Candidate.model_validate(_answer()["events"][0]),
        config=config,
        places=PlaceIndex(
            [Place(name="Schongau", osm_id="node/1", place_type="town", lat=47.8, lon=10.9)]
        ),
        blocked=[],
        today=TODAY,
    )
    rehearsal = kept.model_copy(
        update={
            "id": "2026-11-27-probe-schongau",
            "title": kept.title.model_copy(update={"de": "Chor: Probe"}),
        }
    )
    stored = EventsFile(updated_at="2026-11-09T00:00:00Z", events=[kept, rehearsal])
    report = merge.MergeReport()
    result = merge.apply(
        stored, [], today=TODAY, report=report, excluded_title=config.excluded_title
    )
    assert [e.id for e in result.events] == [kept.id]
    assert report.excluded == {rehearsal.id: "title pattern '\\\\bProbe\\\\b'"}


def test_invalid_title_pattern_is_a_config_error(tmp_path: Path) -> None:
    text = CONFIG_PATH.read_text(encoding="utf-8").replace(
        "excluded_title_patterns:", "excluded_title_patterns:\n  - '(unclosed'", 1
    )
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    from letsgau.config import ConfigError

    with pytest.raises(ConfigError, match="invalid regular expression"):
        load_config(path)
