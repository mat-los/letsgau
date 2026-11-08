"""Shared pytest fixtures. Tests never call an AI; AI responses are recorded fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def instance_config_text() -> str:
    return (REPO_ROOT / "instance" / "config.yaml").read_text(encoding="utf-8")
