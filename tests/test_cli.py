"""Tests for the command-line interface."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from letsgau.cli import main

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG = str(REPO_ROOT / "instance" / "config.yaml")


def test_help_lists_all_commands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        main(["--help"])
    assert info.value.code == 0
    out = capsys.readouterr().out
    for command in ("check", "run", "init"):
        assert command in out


def test_run_help_lists_options(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["run", "--help"])
    out = capsys.readouterr().out
    for option in ("--job", "--dry-run", "--limit-places", "discovery", "events", "build"):
        assert option in out


def test_module_entry_point() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "letsgau", "--help"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0
    assert "check" in result.stdout


def test_check_ok(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--config", CONFIG, "check"]) == 0
    assert "is valid" in capsys.readouterr().out


def test_check_reports_typo(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "config.yaml"
    bad.write_text(
        Path(CONFIG).read_text(encoding="utf-8").replace("crawl_days:", "crawl_dayz:"),
        encoding="utf-8",
    )
    assert main(["--config", str(bad), "check"]) == 1
    err = capsys.readouterr().err
    assert "window.crawl_dayz: unknown field" in err


def test_discovery_without_places_fails_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["--config", CONFIG, "run", "--job", "discovery", "--dry-run"]) == 1
    assert "run `letsgau run --job geo` first" in capsys.readouterr().err
