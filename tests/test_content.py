"""Tests for Phase 8: content pages, feedback, changelog, CNAME, `letsgau init`, fork test."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from letsgau import build
from letsgau import init as init_cmd
from letsgau.cli import main
from letsgau.config import find_placeholders, load_config

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "instance" / "config.yaml"
NOW = datetime(2026, 11, 13, 20, 0, tzinfo=UTC)
MURNAU = {
    "label": "Murnau",
    "lat": 47.6798,
    "lon": 11.2006,
    "minutes": 25,
    "name": "Was geht in Murnau",
    "base_url": "https://example-fork.github.io/letsgau/",
    "contact": "hallo@example.org",
    "owner": "example-fork",
}


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("content")
    build.build(
        load_config(CONFIG_PATH),
        data_dir=root / "none",
        site_dir=root / "site",
        now=NOW,
        content_dir=REPO_ROOT / "instance" / "content",
    )
    return root / "site"


def read(site: Path, rel: str) -> str:
    return (site / rel / "index.html").read_text(encoding="utf-8")


# --- acceptance #26: content pages DE and EN ----------------------------------------------


@pytest.mark.parametrize(
    ("de", "en"),
    [
        ("about", "about"),
        ("fehlt-mein-event", "missing-event"),
        ("impressum", "imprint"),
        ("datenschutz", "privacy"),
        ("feedback", "feedback"),
        ("changelog", "changelog"),
    ],
)
def test_content_pages_exist_in_both_languages(site: Path, de: str, en: str) -> None:
    german, english = read(site, f"de/{de}"), read(site, f"en/{en}")
    assert '<html lang="de">' in german and '<html lang="en">' in english
    assert f'href="https://letsgau.de/en/{en}/"' in german  # hreflang to the English page
    assert f'<a href="../../en/{en}/" hreflang="en"' in german  # language switch


def test_content_values_come_from_the_config(site: Path) -> None:
    about = read(site, "de/about")
    assert "<h1>Über Let&#39;s Gau</h1>" in about
    assert "bis\n5 Fahrminuten um Schongau" in about or "5 Fahrminuten um Schongau" in about
    assert 'href="mailto:hallo@letsgau.de"' in about
    missing = read(site, "de/fehlt-mein-event")
    assert "nicht</strong> Facebook oder Instagram" in missing
    assert "als Text schreiben" in missing
    privacy = read(site, "en/privacy")
    assert "no cookies" in privacy.lower() and "localStorage" in privacy
    assert "form service" not in privacy  # no feedback endpoint configured


def test_footer_links_every_content_page(site: Path) -> None:
    day = read(site, "de/2026-11-14")
    for label in (
        "Über die Seite",
        "Fehlt mein Event?",
        "Impressum",
        "Datenschutz",
        "Feedback",
        "Änderungen",
    ):
        assert f">{label}</a>" in day


def test_feedback_without_endpoint_uses_mailto(site: Path) -> None:
    page = read(site, "de/feedback")
    assert (
        "mailto:hallo@letsgau.de?subject=Let%27s%20Gau%3A%20Event%20oder%20Quelle%20fehlt" in page
    )
    assert "<form" not in page
    assert "/issues/new/choose" in page


def test_feedback_form_with_endpoint(tmp_path: Path) -> None:
    config = load_config(CONFIG_PATH)
    config = config.model_copy(
        update={
            "site": config.site.model_copy(
                update={"feedback_form_endpoint": "https://forms.example.eu/f/abc"}
            )
        }
    )
    build.build(
        config,
        data_dir=tmp_path / "none",
        site_dir=tmp_path / "site",
        now=NOW,
        content_dir=REPO_ROOT / "instance" / "content",
    )
    page = read(tmp_path / "site", "en/feedback")
    assert '<form method="post" action="https://forms.example.eu/f/abc"' in page
    assert 'name="website"' in page  # honeypot
    assert '<option value="missing">Event or source missing</option>' in page
    privacy = read(tmp_path / "site", "en/privacy")
    assert "form service" in privacy


def test_changelog_page(site: Path) -> None:
    page = read(site, "de/changelog")
    assert "Der Text kommt direkt aus dem Code-Repository" in page
    assert "<h2>[Unreleased]</h2>" in page


def test_todo_markers_are_reported(capsys: pytest.CaptureFixture[str]) -> None:
    todos = build.content_todos(load_config(CONFIG_PATH), REPO_ROOT / "instance" / "content")
    files = {name for name, _ in todos}
    assert {"imprint.de.md", "imprint.en.md", "about.de.md"} <= files
    assert "privacy.de.md" not in files  # its TODO only applies with a form service
    assert main(["--config", str(CONFIG_PATH), "check"]) == 0
    assert "imprint.de.md: [TODO: Vor- und Nachname]" in capsys.readouterr().err


# --- acceptance #27: CNAME from base_url ---------------------------------------------------


def test_cname_for_custom_domain_only(site: Path) -> None:
    assert (site / "CNAME").read_text() == "letsgau.de\n"
    assert build.custom_domain("https://example-fork.github.io/letsgau") is None
    assert build.custom_domain("https://events.example.org/") == "events.example.org"


# --- acceptance #28: fresh fork with another center ----------------------------------------


def test_init_creates_a_fresh_instance(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "instance"
    target.mkdir()
    (target / "old.txt").write_text("Schongau leftovers")
    config_path = init_cmd.run(dict(MURNAU), target=target, yes=True)

    assert not (target / "old.txt").exists()
    config = load_config(config_path)
    assert config.region.label == "Murnau"
    assert (config.region.center.lat, config.region.center.lon) == (47.6798, 11.2006)
    assert config.region.drive_time_minutes == 25
    assert config.site.base_url == "https://example-fork.github.io/letsgau"
    assert config.site.owner == "example-fork"
    assert find_placeholders(config_path.read_text(encoding="utf-8")) == []
    for path in target.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            for word in ("Schongau", "letsgau.de", "mat-los", "Weilheim", "Landsberg"):
                assert word not in text, f"{word} in {path}"
    assert "wrote" in capsys.readouterr().out


def test_fork_builds_without_original_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The README steps after `letsgau init`: check, then the build in GitHub Actions."""
    monkeypatch.chdir(tmp_path)
    shutil.copytree(REPO_ROOT / "examples" / "instance", tmp_path / "instance")
    args = ["init", "--yes"] + [
        x for k, v in MURNAU.items() for x in (f"--{k.replace('_', '-')}", str(v))
    ]
    assert main(args) == 0
    assert main(["check"]) == 0
    monkeypatch.setenv("GITHUB_REPOSITORY_OWNER", "example-fork")
    assert main(["run", "--job", "build"]) == 0
    site = tmp_path / "site"
    day = (site / "de" / "index.html").read_text(encoding="utf-8")
    assert "Bis 25 Fahrminuten um Murnau" in day
    assert "Für diesen Tag sind noch keine Events bekannt." in day  # no data copied
    assert not (site / "CNAME").exists()  # github.io address
    for path in site.rglob("*.html"):
        assert "Schongau" not in path.read_text(encoding="utf-8"), path
    assert "Murnau" in (site / "llms.txt").read_text(encoding="utf-8")


def test_init_validates_and_aborts(tmp_path: Path) -> None:
    target = tmp_path / "instance"
    target.mkdir()
    with pytest.raises(init_cmd.InitError, match="aborted"):
        init_cmd.run(dict(MURNAU), target=target, prompt=lambda _q: "n")
    assert target.exists()
    with pytest.raises(init_cmd.InitError, match="http"):
        init_cmd.run({**MURNAU, "base_url": "example.org"}, target=target, yes=True)
    answers = iter(["Murnau", "47.68", "11.2", "", "X", "https://x.github.io", "a@b.c", "x"])
    config = init_cmd.run({}, target=tmp_path / "fresh", prompt=lambda _q: next(answers))
    assert load_config(config).region.drive_time_minutes == 30  # default
