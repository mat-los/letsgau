"""Tests for the discovery stage (Phase 7) and source statistics."""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from letsgau import crawl, discovery
from letsgau.ai_runner import AiError, AiRequest, AiResult
from letsgau.config import Config, load_config
from letsgau.models import (
    Event,
    Place,
    PlacesFile,
    SourceOrigin,
    SourcesFile,
    SourceStatus,
    Usage,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"
TODAY = date(2026, 11, 9)


def answer() -> dict[str, Any]:
    return json.loads((FIXTURES / "discovery" / "opus_answer.json").read_text(encoding="utf-8"))


@pytest.fixture
def config() -> Config:
    config = load_config(REPO_ROOT / "instance" / "config.yaml")
    sources = config.sources.model_copy(
        update={
            "whitelist_file": str(FIXTURES / "crawl" / "whitelist.yaml"),
            "blacklist_file": str(FIXTURES / "crawl" / "blacklist.yaml"),
        }
    )
    return config.model_copy(update={"sources": sources})


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    names = [
        ("Schongau", None),
        ("Peiting", None),
        ("Herzogsägmühle", "Peiting"),
        ("Altenstadt", None),
        ("Steingaden", None),
        ("Rottenbuch", None),
        ("Burggen", None),
        ("Denklingen", None),
    ]
    places = [
        Place(
            name=n,
            osm_id=f"node/{i}",
            place_type="village",
            lat=47.8,
            lon=10.9,
            drive_time_minutes=float(i),
            municipality=m or n,
        )
        for i, (n, m) in enumerate(names)
    ]
    pf = PlacesFile(
        fingerprint="sha256:x",
        generated_at="2026-11-08T04:00:00Z",
        center={"lat": 47.8, "lon": 10.9},
        drive_time_minutes=30,
        place_types=["village"],
        places=places,
    )
    (d / "places.json").write_text(pf.model_dump_json(), encoding="utf-8")
    return d


class FakeRunner:
    def __init__(self, answers: list[dict[str, Any]] | None = None, fail: bool = False) -> None:
        self.answers = answers
        self.fail = fail
        self.requests: list[AiRequest] = []

    def run(self, request: AiRequest) -> AiResult:
        self.requests.append(request)
        if self.fail:
            raise AiError("claude failed")
        data = answer() if self.answers is None else self.answers[len(self.requests) - 1]
        return AiResult(
            data=copy.deepcopy(data), usage=Usage(requests=1, web_searches=6, duration_s=60)
        )


# --- acceptance #24: Opus run extends sources.json and writes suggested.md ------------------


def test_discovery_extends_sources_and_writes_suggestions(
    config: Config, data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = FakeRunner(
        answers=[answer(), {"sources": [], "blacklist_candidates": [], "notes": ""}]
    )
    result = discovery.run(config, runner=runner, data_dir=data_dir, today=TODAY)

    # 7 municipalities (Herzogsägmühle belongs to Peiting) in batches of 6 -> 2 Opus calls
    assert [r.model for r in runner.requests] == ["claude-opus-5-5"] * 2
    assert runner.requests[0].blocked_domains == ["example-spam.de"]
    prompt = runner.requests[0].prompt
    assert "- Peiting (including Herzogsägmühle)" in prompt
    assert "https://www.schongau.de/veranstaltungen" in prompt  # whitelist is known already

    stored = SourcesFile.model_validate_json((data_dir / "sources.json").read_text("utf-8"))
    by_url = {s.url: s for s in stored.sources}
    pfaffenwinkel = by_url["https://veranstaltungen.pfaffen-winkel.de/schongau/"]
    assert pfaffenwinkel.origin is SourceOrigin.DISCOVERY and pfaffenwinkel.rating == 5
    assert pfaffenwinkel.update_frequency == "weekly" and pfaffenwinkel.first_seen == TODAY
    assert by_url["https://www.ecpeiting.de/spielplan"].places == ["Peiting"]  # district resolved
    assert by_url["https://www.schongau.de/veranstaltungen"].origin is SourceOrigin.WHITELIST
    assert not any("example-spam.de" in u for u in by_url)  # blacklist wins
    assert not any(u == "not a url" for u in by_url)
    assert len(result.added) == 3

    suggested = (data_dir / "suggested.md").read_text(encoding="utf-8")
    assert "## Whitelist candidates" in suggested
    assert '- url: "https://veranstaltungen.pfaffen-winkel.de/schongau/"' in suggested
    assert '  places: ["Schongau"]' in suggested
    assert '- domain: "eventfinder-copy.example"' in suggested
    assert '- domain: "reaktor-sog.de"' in suggested  # rated 1
    assert "The Schongau town calendar is loaded with JavaScript." in capsys.readouterr().out


def test_second_discovery_rerates_and_keeps_stats(config: Config, data_dir: Path) -> None:
    discovery.run(config, runner=FakeRunner(), data_dir=data_dir, today=TODAY)
    path = data_dir / "sources.json"
    stored = SourcesFile.model_validate_json(path.read_text("utf-8"))
    stored.sources = [
        s.model_copy(update={"hits": 7}) if "ecpeiting" in s.url else s for s in stored.sources
    ]
    path.write_text(stored.model_dump_json(), encoding="utf-8")

    second = answer()
    second["sources"][1]["rating"] = 3
    later = date(2026, 12, 1)
    result = discovery.run(
        config, runner=FakeRunner(answers=[second, second]), data_dir=data_dir, today=later
    )
    club = next(s for s in result.sources.sources if "ecpeiting" in s.url)
    assert club.rating == 3 and club.hits == 7 and club.first_seen == TODAY
    assert club.last_checked == later
    assert result.added == []


def test_dry_run_writes_nothing(config: Config, data_dir: Path) -> None:
    discovery.run(
        config, runner=FakeRunner(), data_dir=data_dir, today=TODAY, dry_run=True, limit_places=2
    )
    assert not (data_dir / "sources.json").exists()
    assert not (data_dir / "suggested.md").exists()


def test_all_batches_failing_raises(config: Config, data_dir: Path) -> None:
    with pytest.raises(discovery.DiscoveryError, match="every discovery batch failed"):
        discovery.run(config, runner=FakeRunner(fail=True), data_dir=data_dir, today=TODAY)


def test_blacklisted_existing_source_is_marked(config: Config, data_dir: Path) -> None:
    discovery.run(config, runner=FakeRunner(), data_dir=data_dir, today=TODAY)
    path = data_dir / "sources.json"
    stored = SourcesFile.model_validate_json(path.read_text("utf-8"))
    spam = stored.sources[0].model_copy(
        update={"url": "https://sub.example-spam.de/x", "domain": "sub.example-spam.de"}
    )
    stored.sources.append(spam)
    path.write_text(stored.model_dump_json(), encoding="utf-8")
    result = discovery.run(config, runner=FakeRunner(), data_dir=data_dir, today=TODAY)
    marked = next(s for s in result.sources.sources if "example-spam" in s.url)
    assert marked.status is SourceStatus.BLACKLISTED


# --- events stage integration -----------------------------------------------------------------


def _event(url: str, place: str = "Herzogsägmühle") -> Event:
    return Event.model_validate(
        {
            "id": "2026-11-20-x",
            "title": {"de": "X", "en": "X"},
            "description": {"de": "d", "en": "d"},
            "venue": {"place": place},
            "schedule": {"start_date": "2026-11-20"},
            "primary_tag": "sports",
            "tags": ["sports"],
            "sources": [
                {"url": url, "primary": True, "first_seen": "2026-11-09", "last_seen": "2026-11-09"}
            ],
            "first_seen": "2026-11-09",
            "last_verified": "2026-11-09",
        }
    )


def test_record_hits_counts_and_adds_crawl_sources(config: Config, data_dir: Path) -> None:
    discovery.run(config, runner=FakeRunner(), data_dir=data_dir, today=TODAY)
    sources = discovery.load_sources(data_dir)
    events = [
        _event("https://www.ecpeiting.de/spielplan/2026-11-20"),  # under a known URL
        _event("https://ecpeiting.de/spielplan"),  # same source without www
        _event("https://www.kreisbote.de/lokales/schongau/x"),  # unknown -> origin crawl
        _event("https://example-spam.de/y"),  # blacklisted -> never added
    ]
    municipality = {"Herzogsägmühle": "Peiting"}
    updated = discovery.record_hits(sources, events, municipality, ["example-spam.de"], TODAY)
    by_url = {s.url: s for s in updated.sources}
    assert by_url["https://www.ecpeiting.de/spielplan"].hits == 2
    assert by_url["https://www.ecpeiting.de/spielplan"].last_hit == TODAY
    kreisbote = by_url["https://www.kreisbote.de/"]
    assert kreisbote.origin is SourceOrigin.CRAWL and kreisbote.places == ["Peiting"]
    assert kreisbote.hits == 1
    assert not any("example-spam" in u for u in by_url)


def test_known_sources_for_batch() -> None:
    discovered = discovery.Source(
        url="https://cal.example/peiting",
        domain="cal.example",
        places=["Peiting"],
        origin=SourceOrigin.DISCOVERY,
        rating=4,
        first_seen=TODAY,
    )
    weak = discovered.model_copy(update={"url": "https://weak.example", "rating": 2})
    busy = weak.model_copy(update={"url": "https://busy.example", "hits": 5})
    elsewhere = discovered.model_copy(update={"url": "https://other.example", "places": ["X"]})
    file = SourcesFile(
        updated_at="2026-11-09T00:00:00Z", sources=[discovered, weak, busy, elsewhere]
    )
    picked = discovery.known_sources_for(file, ["Peiting"])
    assert [s.url for s in picked] == ["https://cal.example/peiting", "https://busy.example"]


def test_crawl_prompt_lists_known_sources_and_counts_hits(config: Config, data_dir: Path) -> None:
    discovery.run(config, runner=FakeRunner(), data_dir=data_dir, today=TODAY)

    class CrawlRunner:
        def __init__(self) -> None:
            self.requests: list[AiRequest] = []

        def run(self, request: AiRequest) -> AiResult:
            self.requests.append(request)
            if request.step == "merge":
                return AiResult(data={"pairs": [], "excluded": []}, usage=Usage())
            event = {
                "ref": "e1",
                "parent_ref": None,
                "title": {"de": "EC Peiting – EV Füssen", "en": "EC Peiting vs EV Füssen"},
                "description": {"de": "Derby.", "en": "Derby."},
                "venue": {"name": None, "place": "Peiting", "address": None},
                "schedule": {
                    "start_date": "2026-11-13",
                    "end_date": None,
                    "start_time": "20:00",
                    "end_time": None,
                    "all_day": False,
                    "dates": None,
                    "opening_hours": None,
                },
                "primary_tag": "sports",
                "tags": ["sports"],
                "status": "scheduled",
                "postponed_to_date": None,
                "sources": [{"url": "https://www.ecpeiting.de/spielplan", "primary": True}],
            }
            events = [event] if "Peiting" in request.prompt else []
            return AiResult(
                data={"events": events, "checked_sources": [], "notes": ""}, usage=Usage(requests=1)
            )

    runner = CrawlRunner()
    crawl.run(config, runner=runner, data_dir=data_dir, today=TODAY)
    first = runner.requests[0].prompt
    assert "These sources are known to list events here; check them too:" in first
    assert "https://www.ecpeiting.de/spielplan (EC Peiting schedule)" in first
    stored = discovery.load_sources(data_dir)
    club = next(s for s in stored.sources if "ecpeiting" in s.url)
    assert club.hits == 1
