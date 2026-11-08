"""Tests for the event crawl and the Phase 2 merge. AI answers are recorded fixtures."""

from __future__ import annotations

import copy
import json
from datetime import date
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


def make_config(tmp_path: Path, **limits: Any) -> Config:
    config = load_config(CONFIG_PATH)
    sources = config.sources.model_copy(
        update={
            "whitelist_file": str(REPO_ROOT / "instance/sources/whitelist.yaml"),
            "blacklist_file": str(REPO_ROOT / "instance/sources/blacklist.yaml"),
        }
    )
    return config.model_copy(
        update={"sources": sources, "limits": config.limits.model_copy(update=limits)}
    )


def write_places(data_dir: Path, names: list[str] = PLACE_NAMES) -> None:
    places = [
        Place(
            name=n,
            osm_id=f"node/{i + 1}",
            place_type="village",
            lat=47.8,
            lon=10.9,
            drive_time_minutes=float(i * 3 + 2),
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
        searches: int = 5,
        fetched: list[str] | None = None,
    ) -> None:
        self.answers = answers
        self.fail = fail or set()
        self.searches = searches
        self.fetched = fetched or []
        self.requests: list[AiRequest] = []

    def run(self, request: AiRequest) -> AiResult:
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
    batches = make_batches(places, 6, whitelist, [])
    assert [len(b.places) for b in batches] == [6, 2]
    assert [w.url for w in batches[0].whitelist] == ["https://regional.de"]
    assert [w.url for w in batches[1].whitelist] == ["https://a.de"]


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
        ({"schedule__start_date": "2026-12-08"}, "after the crawl window"),
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
    assert "after the crawl window" in reasons["Weit entfernter Markt"]
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
    assert "dry run, nothing written" in out and "5 places skipped" in out


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
        target.write_text((REPO_ROOT / "instance" / "sources" / name).read_text("utf-8"), "utf-8")
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
    assert len(runner.requests) == 1
    assert not (tmp_path / "data" / "events.json").exists()


def test_cli_events_without_places_fails_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["--config", str(CONFIG_PATH), "run", "--job", "events", "--dry-run"]) == 1
    assert "run `letsgau run --job geo` first" in capsys.readouterr().err
