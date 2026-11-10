"""Tests for config loading and error messages."""

from __future__ import annotations

from pathlib import Path

import pytest

from letsgau.config import ConfigError, find_placeholders, load_config, parse_config

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_instance_config_is_valid() -> None:
    config = load_config(REPO_ROOT / "instance" / "config.yaml")
    assert config.region.label == "Schongau"
    assert config.default_language == "de"
    assert "concert" in config.tag_ids
    assert config.ai.auth == "subscription"


def test_example_config_is_valid_but_has_placeholders() -> None:
    path = REPO_ROOT / "examples" / "instance" / "config.yaml"
    load_config(path)
    tokens = {token for _, token in find_placeholders(path.read_text(encoding="utf-8"))}
    assert {"{{SITE_NAME}}", "{{GITHUB_OWNER}}", "{{BASE_URL}}"} <= tokens


def test_instance_config_has_no_placeholders(instance_config_text: str) -> None:
    assert find_placeholders(instance_config_text) == []


def _problems(text: str) -> list[str]:
    with pytest.raises(ConfigError) as info:
        parse_config(text, Path("config.yaml"))
    return info.value.problems


def _line_of(text: str, needle: str) -> int:
    return next(i for i, line in enumerate(text.splitlines(), 1) if needle in line)


def test_typo_reports_line_and_field(instance_config_text: str) -> None:
    text = instance_config_text.replace("drive_time_minutes:", "drive_time_minuts:")
    line = _line_of(text, "drive_time_minuts")
    problems = _problems(text)
    assert f"config.yaml:{line}: region.drive_time_minuts: unknown field (typo?)" in problems
    assert any("region.drive_time_minutes: required field is missing" in p for p in problems)


def test_invalid_value_reports_line_of_value(instance_config_text: str) -> None:
    text = instance_config_text.replace('"weekly:mon,thu"', '"weekly:mon,thr"')
    line = _line_of(text, "weekly:mon,thr")
    (problem,) = _problems(text)
    assert problem.startswith(f"config.yaml:{line}: jobs.events: invalid schedule")


def test_wrong_type_in_list_item(instance_config_text: str) -> None:
    text = instance_config_text.replace("languages: [de, en]", "languages: [de, fr]")
    line = _line_of(text, "languages:")
    (problem,) = _problems(text)
    assert problem.startswith(f"config.yaml:{line}: site.languages.1:")


def test_invalid_auth_mode(instance_config_text: str) -> None:
    text = instance_config_text.replace('auth: "subscription"', 'auth: "password"')
    (problem,) = _problems(text)
    assert "ai.auth" in problem


def test_window_consistency(instance_config_text: str) -> None:
    text = instance_config_text.replace("crawl_days: 28", "crawl_days: 14")
    (problem,) = _problems(text)
    assert "crawl_days must be >= display_days" in problem


def test_duplicate_tag_ids(instance_config_text: str) -> None:
    text = instance_config_text.replace("id: party,  ", "id: concert,")
    (problem,) = _problems(text)
    assert "duplicate tag ids: concert" in problem


def test_yaml_syntax_error_reports_line() -> None:
    problems = _problems("site:\n  name: [unclosed\n")
    assert problems[0].startswith("config.yaml:")
    assert "YAML syntax error" in problems[0]


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="file not found"):
        load_config(tmp_path / "nope.yaml")


def test_contact_email_or_form_is_required(instance_config_text: str) -> None:
    from letsgau.config import parse_config

    text = instance_config_text.replace(
        'feedback_form_endpoint: "https://formhook.app/f/fh_JNXIIbcjfUatEDM3yXzTWygLzvbkKTeo"',
        'feedback_form_endpoint: ""',
    )
    assert 'feedback_form_endpoint: ""' in text
    (problem,) = _problems(text)
    assert "set contact_email or feedback_form_endpoint" in problem
    with_mail = text.replace('feedback_form_endpoint: ""', 'contact_email: "a@b.de"')
    assert parse_config(with_mail).site.contact_email == "a@b.de"
