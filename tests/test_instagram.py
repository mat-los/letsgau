"""Tests for reading Instagram business accounts (letsgau.instagram and the crawl step).

The Graph API is never called: answers are written in the documented Business Discovery
shape, images come from a fake fetcher, and the AI is a fake runner.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

from letsgau import crawl, instagram
from letsgau.ai_runner import AiRequest, AiResult
from letsgau.config import Config, load_config
from letsgau.models import InstagramFile, Place, PlacesFile, Usage

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "crawl"
TODAY = date(2026, 11, 12)
TOKEN = "EAAtest-secret-token"
ENV = {instagram.TOKEN_ENV: TOKEN, instagram.USER_ENV: "17841400000000001"}
JPEG = b"\xff\xd8\xff" + b"0" * 30_000


def media(
    media_id: str, day: str, caption: str, kind: str = "IMAGE", **extra: Any
) -> dict[str, Any]:
    return {
        "id": media_id,
        "caption": caption,
        "media_type": kind,
        "media_url": f"https://cdn.example/{media_id}.jpg",
        "permalink": f"https://www.instagram.com/p/{media_id}/",
        "timestamp": f"{day}T18:00:00+0000",
        **extra,
    }


def discovery(username: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "business_discovery": {"username": username, "media": {"data": items}, "id": "178"},
        "id": ENV[instagram.USER_ENV],
    }


NOT_BUSINESS = {
    "error": {
        "message": "Invalid user id",
        "type": "OAuthException",
        "code": 110,
        "error_subcode": 2207013,
    }
}


class Graph:
    """Fake Graph API: answers by username; records the requested accounts."""

    def __init__(self, answers: dict[str, dict[str, Any]]) -> None:
        self.answers = answers
        self.asked: list[str] = []
        self.urls: list[str] = []

    def __call__(self, url: str) -> dict[str, Any]:
        self.urls.append(url)
        fields = parse_qs(urlsplit(url).query)["fields"][0]
        username = fields.split("username(")[1].split(")")[0]
        self.asked.append(username)
        return self.answers.get(username, NOT_BUSINESS)


def fetch(url: str, max_bytes: int) -> tuple[bytes, str]:
    if "broken" in url:
        raise OSError("404")
    return JPEG, "image/jpeg"


def concert(post_ref: str, permalink: str) -> dict[str, Any]:
    return {
        "ref": "e1",
        "parent_ref": None,
        "title": {"de": "Live: The Testers", "en": "Live: The Testers"},
        "description": {"de": "Konzert im Café.", "en": "Concert in the café."},
        "venue": {"name": "Café Test", "place": "Schongau", "address": None},
        "schedule": {
            "start_date": "2026-11-14",
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
        "sources": [{"url": permalink, "primary": True}],
        "file": post_ref,
    }


class Runner:
    """Web calls report a mined account; Instagram calls report one concert per post 1."""

    def __init__(self, mined: list[str] | None = None) -> None:
        self.requests: list[AiRequest] = []
        self.mined = mined or []

    def run(self, request: AiRequest) -> AiResult:
        self.requests.append(request)
        if "(instagram)" in request.step:
            link = request.prompt.split("### post 1: ")[1].split(", ")[2].split("\n")[0]
            events = [concert("post 1", link)]
            return AiResult(
                data={"events": events, "checked_sources": [], "notes": ""},
                usage=Usage(requests=1, input_tokens=3000),
            )
        return AiResult(
            data={
                "events": [],
                "checked_sources": [],
                "instagram_accounts": self.mined,
                "notes": "",
            },
            usage=Usage(requests=1, input_tokens=1000, web_searches=13),
        )


def setup(tmp_path: Path, accounts: list[dict[str, Any]]) -> tuple[Config, Path]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
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
    listing = tmp_path / "instagram.yaml"
    listing.write_text(yaml.safe_dump(accounts), encoding="utf-8")
    config = load_config(REPO_ROOT / "instance" / "config.yaml")
    sources = config.sources.model_copy(
        update={
            "whitelist_file": str(FIXTURES / "whitelist.yaml"),
            "blacklist_file": str(FIXTURES / "blacklist.yaml"),
            "flyers_file": "",
            "instagram_file": str(listing),
        }
    )
    depth = config.depth.model_copy(update={"crawl_sources_per_request": 4})
    return config.model_copy(update={"sources": sources, "depth": depth}), data_dir


# --- the API module ---------------------------------------------------------------------


def test_fetch_posts_parses_business_discovery() -> None:
    graph = Graph(
        {
            "cafe.test": discovery(
                "cafe.test",
                [
                    media("1", "2026-11-10", "Samstag 20 Uhr: The Testers live!"),
                    media(
                        "2",
                        "2026-11-09",
                        "Carousel",
                        kind="CAROUSEL_ALBUM",
                        children={
                            "data": [
                                {"media_type": "IMAGE", "media_url": "https://cdn.example/c1.jpg"},
                                {
                                    "media_type": "VIDEO",
                                    "thumbnail_url": "https://cdn.example/c2.jpg",
                                },
                            ]
                        },
                    ),
                    media(
                        "3",
                        "2026-11-08",
                        "Reel",
                        kind="VIDEO",
                        thumbnail_url="https://cdn.example/r.jpg",
                    ),
                    {"id": "4"},  # no timestamp: ignored
                ],
            )
        }
    )
    posts = instagram.fetch_posts(
        "cafe.test", user_id="178", token=TOKEN, limit=6, api_version="v24.0", get=graph
    )
    assert [p.id for p in posts] == ["1", "2", "3"]
    assert posts[0].caption.startswith("Samstag") and posts[0].posted.date() == date(2026, 11, 10)
    assert posts[0].image_urls == ["https://cdn.example/1.jpg"]
    assert posts[1].image_urls == ["https://cdn.example/c1.jpg", "https://cdn.example/c2.jpg"]
    assert posts[2].image_urls == ["https://cdn.example/r.jpg"]
    url = graph.urls[0]
    assert url.startswith("https://graph.facebook.com/v24.0/178?")
    fields = parse_qs(urlsplit(url).query)["fields"][0]
    assert fields.startswith("business_discovery.username(cafe.test){username,media.limit(6){")


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (NOT_BUSINESS["error"], "not_business"),
        ({"message": f"Error validating access token {TOKEN}", "code": 190}, "auth"),
        ({"message": "Application request limit reached", "code": 4}, "rate_limit"),
        ({"message": "Cannot find User with username x", "code": 100}, "not_found"),
        ({"message": "Something else", "code": 1}, "other"),
    ],
)
def test_errors_are_classified_and_never_show_the_token(error: dict[str, Any], kind: str) -> None:
    with pytest.raises(instagram.AccountError) as info:
        instagram.fetch_posts(
            "x", user_id="178", token=TOKEN, limit=6, get=lambda _u: {"error": error}
        )
    assert info.value.kind == kind
    assert TOKEN not in str(info.value)


def test_account_names_from_text_and_links() -> None:
    assert instagram.normalize("@Cafe.Test") == "cafe.test"
    assert instagram.normalize("https://www.instagram.com/juze_schongau/?hl=de") == "juze_schongau"
    assert instagram.normalize("https://www.instagram.com/p/Cx1/") is None  # a post, no account
    assert instagram.normalize("not an account!") is None
    page = 'Folgt uns: <a href="https://instagram.com/Bar.Test">IG</a> instagram.com/reel/abc'
    assert instagram.handles_in(page) == {"bar.test"}


# --- the crawl step -----------------------------------------------------------------------


def test_instagram_step_reads_new_posts_once_and_mines_accounts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, data_dir = setup(tmp_path, [{"username": "@Cafe.Test", "places": ["Schongau"]}])
    graph = Graph(
        {
            "cafe.test": discovery(
                "cafe.test",
                [
                    media("101", "2026-11-11", "Samstag 20 Uhr: The Testers live im Café!"),
                    media("102", "2026-10-01", "Old post from October"),  # older than 21 days
                ],
            )
        }
    )
    runner = Runner(mined=["@Bar.Test", "https://www.instagram.com/p/xyz/"])
    result = crawl.crawl(
        config, runner, data_dir=data_dir, today=TODAY, fetch=fetch, graph_get=graph, env=ENV
    )
    insta = [r for r in runner.requests if "(instagram)" in r.step]
    assert len(insta) == 1
    request = insta[0]
    assert request.web is False and request.max_searches == 0
    assert "### post 1: @cafe.test, published 2026-11-11, https://www.instagram.com/p/101/" in (
        request.prompt
    )
    assert "Samstag 20 Uhr: The Testers live im Café!" in request.prompt
    assert "Old post from October" not in request.prompt
    assert [Path(f).name for f in request.files] == ["post1-1.jpg"]
    assert "### post 2" not in request.prompt
    # the mined account was found by the web call before the Instagram step and read too
    assert graph.asked == ["cafe.test", "bar.test"]
    cache = result.instagram_cache
    assert cache is not None
    assert cache.accounts["cafe.test"].origin == "whitelist"
    assert cache.accounts["cafe.test"].status == "active"
    assert cache.accounts["bar.test"].origin == "mined"
    assert cache.accounts["bar.test"].status == "not_business"
    assert "p" not in cache.accounts  # links to posts are not accounts
    assert list(cache.posts) == ["101"]
    live = [e for e in result.events if e.title.de == "Live: The Testers"]
    assert len(live) == 1
    assert live[0].sources[0].url == "https://www.instagram.com/p/101/"
    assert not any("bar.test" in w for w in result.warnings)  # mined: no warning

    # second run: the post is cached, no Instagram AI call; the personal account is not asked
    (data_dir / "instagram.json").write_text(cache.model_dump_json(), encoding="utf-8")
    graph.asked.clear()
    runner = Runner()
    result = crawl.crawl(
        config, runner, data_dir=data_dir, today=TODAY, fetch=fetch, graph_get=graph, env=ENV
    )
    assert not any("(instagram)" in r.step for r in runner.requests)
    assert graph.asked == ["cafe.test"]
    assert any(e.title.de == "Live: The Testers" for e in result.events)
    assert TOKEN not in capsys.readouterr().err


def test_without_token_instagram_is_skipped_with_one_warning(tmp_path: Path) -> None:
    config, data_dir = setup(tmp_path, [{"username": "cafe.test"}])
    graph = Graph({})
    result = crawl.crawl(
        config, Runner(), data_dir=data_dir, today=TODAY, fetch=fetch, graph_get=graph, env={}
    )
    assert graph.asked == []
    warnings = [w for w in result.warnings if "instagram" in w]
    assert len(warnings) == 1 and instagram.TOKEN_ENV in warnings[0]


def test_expired_token_stops_reading_and_is_not_logged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, data_dir = setup(tmp_path, [{"username": "a.test"}, {"username": "b.test"}])
    expired = {"error": {"message": f"Error validating access token {TOKEN}", "code": 190}}
    graph = Graph({"a.test": expired, "b.test": discovery("b.test", [])})
    result = crawl.crawl(
        config, Runner(), data_dir=data_dir, today=TODAY, fetch=fetch, graph_get=graph, env=ENV
    )
    assert graph.asked == ["a.test"]  # stopped after the auth error
    assert any("no more accounts are read" in w for w in result.warnings)
    everything = json.dumps(result.warnings) + capsys.readouterr().err
    assert result.instagram_cache is not None
    everything += result.instagram_cache.model_dump_json()
    assert TOKEN not in everything


def test_instagram_off_without_list_file(tmp_path: Path) -> None:
    config, data_dir = setup(tmp_path, [{"username": "cafe.test"}])
    config = config.model_copy(
        update={"sources": config.sources.model_copy(update={"instagram_file": ""})}
    )
    runner = Runner(mined=["bar.test"])
    result = crawl.crawl(config, runner, data_dir=data_dir, today=TODAY, fetch=fetch, env=ENV)
    assert result.instagram_cache is None
    assert not any("(instagram)" in r.step for r in runner.requests)
    assert all('instagram_accounts", list' not in r.prompt for r in runner.requests)


def test_instagram_file_round_trip() -> None:
    file = InstagramFile(updated_at="2026-11-12T00:00:00Z")
    assert InstagramFile.model_validate_json(file.model_dump_json()) == file
