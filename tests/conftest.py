"""Shared pytest fixtures. Tests never call an AI; AI responses are recorded fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def instance_config_text() -> str:
    return (REPO_ROOT / "instance" / "config.yaml").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def no_flyer_downloads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never download flyers; a test that needs pages passes its own ``fetch``."""
    from letsgau import flyers

    def refuse(url: str, max_bytes: int) -> tuple[bytes, str]:
        raise OSError(f"network disabled in tests: {url}")

    monkeypatch.setattr(flyers, "http_fetch", refuse)
