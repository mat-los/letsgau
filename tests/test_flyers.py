"""Tests for flyer pages: image/PDF extraction, download rules, crawl calls, runner files."""

from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

from letsgau import crawl, flyers
from letsgau.ai_runner import AiRequest, AiResult, ClaudeCodeRunner, _content
from letsgau.config import Config, load_config
from letsgau.models import Place, PlacesFile, Usage

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "crawl"
TODAY = date(2026, 11, 9)
JPEG = b"\xff\xd8\xff\xe0" + b"x" * 30_000
SMALL_JPEG = b"\xff\xd8\xff\xe0" + b"x" * 500
PDF = b"%PDF-1.7\n" + b"y" * 1000

PAGE = """<!doctype html><html><head>
<meta property="og:image" content="/media/poster-dec.jpg"></head><body>
<img src="/img/logo.png" alt="Logo">
<img src="/img/thumb-150x150.jpg" srcset="/img/big-poster.jpg 1200w, /img/thumb-150x150.jpg 150w">
<img data-src="https://cdn.example.org/flyer2.webp" src="data:image/gif;base64,AAAA">
<img src="/img/icon-facebook.png"><img src="/img/divider.svg">
<a href="/files/programm.pdf">Programm (PDF)</a>
<a href="/media/poster-dec.jpg">same poster again</a>
<img src="/img/tiny.jpg">
</body></html>"""


class FakeWeb:
    def __init__(self, pages: dict[str, tuple[bytes, str]], robots: str = "") -> None:
        self.pages = pages
        self.robots = robots
        self.calls: list[str] = []

    def __call__(self, url: str, max_bytes: int) -> tuple[bytes, str]:
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            return self.robots.encode(), "text/plain"
        if url not in self.pages:
            raise OSError(f"HTTP 404: {url}")
        return self.pages[url]


def site() -> FakeWeb:
    return FakeWeb(
        {
            "https://venue.example/": (PAGE.encode(), "text/html; charset=utf-8"),
            "https://venue.example/media/poster-dec.jpg": (JPEG, "image/jpeg"),
            "https://venue.example/img/big-poster.jpg": (JPEG + b"2", "image/jpeg"),
            "https://cdn.example.org/flyer2.webp": (
                b"RIFF\x00\x00\x00\x00WEBP" + b"z" * 30_000,
                "",
            ),
            "https://venue.example/files/programm.pdf": (PDF, "application/pdf"),
            "https://venue.example/img/tiny.jpg": (SMALL_JPEG, "image/jpeg"),
        }
    )


# --- extraction and download ---------------------------------------------------------------


def test_candidates_pick_posters_in_page_order() -> None:
    urls = flyers.candidates(PAGE, "https://venue.example/")
    assert urls == [
        "https://venue.example/media/poster-dec.jpg",  # og:image
        "https://venue.example/img/big-poster.jpg",  # largest srcset entry
        "https://cdn.example.org/flyer2.webp",  # lazy-load attribute
        "https://venue.example/files/programm.pdf",
        "https://venue.example/img/tiny.jpg",
    ]  # no logo, social icon, svg, data: URI or duplicate


def test_collect_downloads_images_and_pdfs(tmp_path: Path) -> None:
    files, warnings = flyers.collect("https://venue.example/", tmp_path, max_files=10, fetch=site())
    assert [f.kind for f in files] == ["image", "image", "image", "pdf"]  # tiny.jpg skipped
    assert [f.path.name for f in files] == ["01.jpg", "02.jpg", "03.webp", "04.pdf"]
    assert files[3].url == "https://venue.example/files/programm.pdf"
    assert files[0].path.read_bytes() == JPEG
    assert warnings == []
    # the same bytes under a second URL are taken once
    web = site()
    web.pages["https://venue.example/img/big-poster.jpg"] = (JPEG, "image/jpeg")
    files, _ = flyers.collect("https://venue.example/", tmp_path / "d", max_files=10, fetch=web)
    assert len(files) == 3 and len({f.sha256 for f in files}) == 3


def test_collect_respects_max_files_and_robots(tmp_path: Path) -> None:
    files, _ = flyers.collect("https://venue.example/", tmp_path, max_files=2, fetch=site())
    assert len(files) == 2
    web = site()
    web.robots = "User-agent: *\nDisallow: /media/\n"
    files, _ = flyers.collect("https://venue.example/", tmp_path / "b", max_files=10, fetch=web)
    assert "https://venue.example/media/poster-dec.jpg" not in [f.url for f in files]
    assert "https://venue.example/media/poster-dec.jpg" not in web.calls
    web.robots = "User-agent: *\nDisallow: /\n"
    files, warnings = flyers.collect(
        "https://venue.example/", tmp_path / "c", max_files=5, fetch=web
    )
    assert files == [] and "disallowed by robots.txt" in warnings[0]


def test_collect_direct_pdf_and_failures(tmp_path: Path) -> None:
    web = site()
    files, _ = flyers.collect(
        "https://venue.example/files/programm.pdf", tmp_path, max_files=1, fetch=web
    )
    assert [(f.kind, f.path.name) for f in files] == [("pdf", "01.pdf")]
    files, warnings = flyers.collect("https://venue.example/gone", tmp_path, max_files=1, fetch=web)
    assert files == [] and "HTTP 404" in warnings[0]
    empty = FakeWeb({"https://empty.example/": (b"<html><p>nothing</p></html>", "text/html")})
    files, warnings = flyers.collect("https://empty.example/", tmp_path, max_files=5, fetch=empty)
    assert warnings == ["https://empty.example/: no flyer images or PDFs found"]


# --- crawl integration -------------------------------------------------------------------------


def make_config(tmp_path: Path, entries: list[dict[str, Any]], sweeps: int = 4) -> Config:
    flyer_file = tmp_path / "flyers.yaml"
    flyer_file.write_text(yaml.safe_dump(entries), encoding="utf-8")
    config = load_config(REPO_ROOT / "instance" / "config.yaml")
    sources = config.sources.model_copy(
        update={
            "whitelist_file": str(FIXTURES / "whitelist.yaml"),
            "blacklist_file": str(FIXTURES / "blacklist.yaml"),
            "flyers_file": str(flyer_file),
        }
    )
    depth = config.depth.model_copy(
        update={"crawl_sources_per_request": sweeps, "crawl_searches_per_request": 13}
    )
    return config.model_copy(update={"sources": sources, "depth": depth})


def write_places(data_dir: Path) -> None:
    data_dir.mkdir(parents=True)
    places = [Place(name="Schongau", osm_id="node/1", place_type="town", lat=47.8, lon=10.9)]
    pf = PlacesFile(
        fingerprint="sha256:t",
        generated_at="2026-11-08T04:00:00Z",
        center={"lat": 47.8, "lon": 10.9},
        drive_time_minutes=5,
        place_types=["town"],
        places=places,
    )
    (data_dir / "places.json").write_text(pf.model_dump_json(), encoding="utf-8")


def poster_event() -> dict[str, Any]:
    return {
        "ref": "e1",
        "parent_ref": None,
        "title": {"de": "Punk-Abend", "en": "Punk night"},
        "description": {"de": "Drei Bands live.", "en": "Three bands live."},
        "venue": {"name": "Reaktor", "place": "Schongau", "address": None},
        "schedule": {
            "start_date": "2026-11-21",
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
        "sources": [
            {"url": "https://venue.example/", "primary": True},
            {"url": "https://venue.example/img/big-poster.jpg", "primary": False},
        ],
    }


class Runner:
    def __init__(self) -> None:
        self.requests: list[AiRequest] = []
        self.seen_files: list[list[str]] = []

    def run(self, request: AiRequest) -> AiResult:
        self.requests.append(request)
        self.seen_files.append([Path(f).name for f in request.files if Path(f).exists()])
        events = [poster_event()] if request.files else []
        return AiResult(
            data={"events": events, "checked_sources": [], "notes": ""},
            usage=Usage(requests=1, input_tokens=5000, web_searches=0 if request.files else 13),
        )


def test_flyer_page_gets_its_own_call_with_files(tmp_path: Path) -> None:
    config = make_config(
        tmp_path, [{"url": "https://venue.example/", "note": "posters", "places": ["Schongau"]}]
    )
    data_dir = tmp_path / "data"
    write_places(data_dir)
    runner = Runner()
    result = crawl.crawl(config, runner, data_dir=data_dir, today=TODAY, fetch=site())

    steps = [r.step for r in runner.requests]
    assert steps == [
        "events batch 1/1 (sources 1)",
        "events batch 1/1 (flyers https://venue.example/)",
        "events batch 1/1 (search)",
    ]
    flyer = runner.requests[1]
    assert flyer.web is False and flyer.max_searches == 0
    assert runner.seen_files[1] == [
        "01.jpg",
        "02.jpg",
        "03.webp",
        "04.pdf",
    ]  # present during the call
    assert "downloaded from https://venue.example/ (posters)" in flyer.prompt
    assert "- files/02.jpg (image, https://venue.example/img/big-poster.jpg)" in flyer.prompt
    assert "Known events to re-check" not in flyer.prompt
    assert "- https://venue.example/" in runner.requests[2].prompt  # search skips the flyer page
    assert [e.title.de for e in result.events] == ["Punk-Abend"]
    assert result.batches[0].calls == 3 and result.usage.requests == 3
    assert not any(Path(p).exists() for p in flyer.files)  # temporary files are gone


def test_flyer_page_without_files_makes_no_ai_call(tmp_path: Path) -> None:
    config = make_config(tmp_path, [{"url": "https://venue.example/gone"}], sweeps=0)
    data_dir = tmp_path / "data"
    write_places(data_dir)
    runner = Runner()
    result = crawl.crawl(config, runner, data_dir=data_dir, today=TODAY, fetch=site())
    assert [r.step for r in runner.requests] == ["events batch 1/1"]  # only the combined call
    assert any("flyers: https://venue.example/gone: HTTP 404" in w for w in result.warnings)


def test_missing_flyer_file_is_a_warning(tmp_path: Path) -> None:
    config = make_config(tmp_path, [])
    config = config.model_copy(
        update={
            "sources": config.sources.model_copy(update={"flyers_file": str(tmp_path / "x.yaml")})
        }
    )
    data_dir = tmp_path / "data"
    write_places(data_dir)
    result = crawl.crawl(config, Runner(), data_dir=data_dir, today=TODAY, fetch=site())
    assert any("x.yaml not found" in w for w in result.warnings)


def test_instance_flyer_list_is_valid() -> None:
    config = load_config(REPO_ROOT / "instance" / "config.yaml")
    entries = crawl._load_list(REPO_ROOT / config.sources.flyers_file, crawl.FlyerEntry)
    assert {e.url for e in entries} >= {"https://www.reaktor-sog.de/"}


# --- runner -----------------------------------------------------------------------------------


def test_cli_gets_read_access_to_the_files_only(tmp_path: Path) -> None:
    image = tmp_path / "01.jpg"
    image.write_bytes(JPEG)
    request = AiRequest(step="flyers", model="m", prompt="p", schema={}, web=False, files=[image])
    cmd = ClaudeCodeRunner().command(request)
    assert cmd[cmd.index("--tools") + 1] == "Read"
    assert cmd[cmd.index("--allowedTools") + 1] == "Read(./files/**)"
    seen: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen["files"] = sorted(p.name for p in (Path(kwargs["cwd"]) / "files").iterdir())
        out = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 2,
               "structured_output": {"events": []}, "usage": {}, "modelUsage": {}}  # fmt: skip
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(out), stderr="")

    ClaudeCodeRunner(run=fake_run).run(request)
    assert seen["files"] == ["01.jpg"]


def test_api_attaches_images_and_pdfs(tmp_path: Path) -> None:
    image, pdf = tmp_path / "01.jpg", tmp_path / "02.pdf"
    image.write_bytes(JPEG)
    pdf.write_bytes(PDF)
    blocks = _content(AiRequest(step="s", model="m", prompt="read", schema={}, files=[image, pdf]))
    assert blocks[0] == {"type": "text", "text": "read"}
    assert [b["type"] for b in blocks[1:]] == ["text", "image", "text", "document"]
    assert blocks[2]["source"]["media_type"] == "image/jpeg"
    assert blocks[4]["source"]["media_type"] == "application/pdf"
    assert _content(AiRequest(step="s", model="m", prompt="plain", schema={})) == "plain"


@pytest.mark.parametrize("url", ["https://venue.example/", "https://venue.example/x.pdf"])
def test_flyer_entry_template_expands_and_keeps_max_files(url: str) -> None:
    entry = crawl.FlyerEntry(url=url.replace("venue", "{municipality_slug}"), max_files=3)
    [one] = entry.expand(["Hohenpeißenberg"])
    assert isinstance(one, crawl.FlyerEntry) and one.max_files == 3
    assert one.url.startswith("https://hohenpeissenberg.example/") and one.places == [
        "Hohenpeißenberg"
    ]


# --- flyer cache (fingerprints) ------------------------------------------------------------


class CachingRunner:
    """Answers flyer calls with one event on 02.jpg and a far-future one on the PDF."""

    def __init__(self) -> None:
        self.flyer_requests: list[AiRequest] = []

    def run(self, request: AiRequest) -> AiResult:
        events: list[dict[str, Any]] = []
        if request.files:
            self.flyer_requests.append(request)
            names = [f"files/{Path(f).name}" for f in request.files]
            assert (
                request.schema["properties"]["events"]["items"]["properties"]["file"]["enum"]
                == names
            )
            if "files/02.jpg" in names:
                events.append({**poster_event(), "file": "files/02.jpg"})
            if "files/04.pdf" in names:
                spring = poster_event()
                spring["ref"] = "e2"
                spring["title"] = {"de": "Frühlingskonzert", "en": "Spring concert"}
                spring["schedule"] = {**spring["schedule"], "start_date": "2027-03-20"}
                events.append({**spring, "file": "files/04.pdf"})
        return AiResult(
            data={"events": events, "checked_sources": [], "notes": ""},
            usage=Usage(requests=1),
        )


def test_flyer_files_are_read_once_and_reused(tmp_path: Path) -> None:
    config = make_config(tmp_path, [{"url": "https://venue.example/", "places": ["Schongau"]}])
    data_dir = tmp_path / "data"
    write_places(data_dir)
    web, runner = site(), CachingRunner()

    first = crawl.run(config, runner=runner, data_dir=data_dir, today=TODAY, fetch=web)
    assert len(runner.flyer_requests) == 1 and len(runner.flyer_requests[0].files) == 4
    assert {"Punk-Abend", "Frühlingskonzert"} <= {e.title.de for e in first.events}
    cache = json.loads((data_dir / "flyers.json").read_text(encoding="utf-8"))
    entries = cache["files"].values()
    assert len(entries) == 4 and sorted(len(e["events"]) for e in entries) == [0, 0, 1, 1]
    assert all("file" not in item for e in entries for item in e["events"])

    # unchanged files: no AI call, the cached events are used again
    second = crawl.crawl(config, runner, data_dir=data_dir, today=TODAY, fetch=web)
    assert len(runner.flyer_requests) == 1
    assert {"Punk-Abend", "Frühlingskonzert"} <= {e.title.de for e in second.events}

    # one changed poster: only that file is read
    web.pages["https://venue.example/img/big-poster.jpg"] = (JPEG + b"new", "image/jpeg")
    crawl.crawl(config, runner, data_dir=data_dir, today=TODAY, fetch=web)
    assert [Path(f).name for f in runner.flyer_requests[1].files] == ["02.jpg"]


def test_flyer_cache_dry_run_and_expiry(tmp_path: Path) -> None:
    config = make_config(tmp_path, [{"url": "https://venue.example/files/programm.pdf"}])
    data_dir = tmp_path / "data"
    write_places(data_dir)
    runner = CachingRunner()
    crawl.run(config, runner=runner, data_dir=data_dir, today=TODAY, fetch=site(), dry_run=True)
    assert not (data_dir / "flyers.json").exists()

    crawl.run(config, runner=runner, data_dir=data_dir, today=TODAY, fetch=site())
    assert len(json.loads((data_dir / "flyers.json").read_text())["files"]) == 1
    # 31 days later the page no longer offers the file: its entry is dropped
    later = date(2026, 12, 10)
    gone = FakeWeb({})
    result = crawl.crawl(config, runner, data_dir=data_dir, today=later, fetch=gone)
    assert result.flyer_cache is not None and result.flyer_cache.files == {}
