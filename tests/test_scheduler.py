"""Tests for the job schedule parser."""

from __future__ import annotations

from datetime import date

import pytest

from letsgau.scheduler import Schedule

MONDAY = date(2026, 11, 9)


@pytest.mark.parametrize(
    ("value", "day", "expected"),
    [
        ("daily", MONDAY, True),
        ("off", MONDAY, False),
        ("weekly:mon,thu", MONDAY, True),
        ("weekly:mon,thu", date(2026, 11, 12), True),
        ("weekly:mon,thu", date(2026, 11, 10), False),
        ("monthly:1", date(2026, 12, 1), True),
        ("monthly:1", MONDAY, False),
    ],
)
def test_schedule_matches(value: str, day: date, expected: bool) -> None:
    assert Schedule.parse(value).matches(day) is expected


def test_invalid_schedule() -> None:
    with pytest.raises(ValueError):
        Schedule.parse("hourly")


# --- due check and execution (Phase 6) ------------------------------------------------------

from datetime import UTC, datetime, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from letsgau import scheduler  # noqa: E402
from letsgau.config import load_config  # noqa: E402
from letsgau.models import State  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG = load_config(REPO_ROOT / "instance" / "config.yaml")  # events weekly:mon,thu, build daily
BERLIN = ZoneInfo("Europe/Berlin")


def at(day: date, hour: int = 4) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=UTC)


def test_last_due_day_and_is_due() -> None:
    weekly = Schedule.parse("weekly:mon,thu")
    assert scheduler.last_due_day(weekly, date(2026, 11, 11)) == MONDAY  # Wednesday
    assert scheduler.last_due_day(Schedule.parse("off"), MONDAY) is None
    assert scheduler.is_due(weekly, None, MONDAY, BERLIN)
    assert not scheduler.is_due(weekly, at(MONDAY), MONDAY, BERLIN)  # already ran today
    # Monday's run failed: caught up on Tuesday
    assert scheduler.is_due(weekly, at(MONDAY - timedelta(days=4)), date(2026, 11, 10), BERLIN)
    assert not scheduler.is_due(weekly, at(MONDAY), date(2026, 11, 11), BERLIN)
    assert not scheduler.is_due(Schedule.parse("off"), None, MONDAY, BERLIN)


class Stages:
    """Fake stage functions that record their calls."""

    def __init__(self, fail: str | None = None, geo_forces: bool = False) -> None:
        self.calls: list[str] = []
        self.fail = fail
        self.geo_forces = geo_forces
        self.data_dir: Path | None = None

    def make(self, name: str) -> Any:
        def run(config: Any, dry_run: bool = False) -> Any:
            self.calls.append(name)
            if name == self.fail:
                raise RuntimeError(f"{name} broke")
            if name == "geo" and self.geo_forces and self.data_dir is not None:
                state = scheduler.load_state(self.data_dir)
                state.force_discovery = True
                (self.data_dir / "state.json").write_text(state.model_dump_json())
            return None

        return run

    def runners(self) -> dict[str, Any]:
        return {name: self.make(name) for name in scheduler.STAGES}


def write_state(data_dir: Path, **last_run: datetime) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    state = State(last_run=last_run)
    (data_dir / "state.json").write_text(state.model_dump_json())


def test_auto_runs_only_due_jobs(tmp_path: Path) -> None:
    data = tmp_path / "data"
    tuesday = date(2026, 11, 10)
    write_state(data, events=at(MONDAY), build=at(MONDAY), discovery=at(date(2026, 11, 1)))
    stages = Stages()
    report = scheduler.execute(CONFIG, "auto", stages.runners(), data_dir=data, now=at(tuesday))
    assert stages.calls == ["geo", "build"]  # events ran Monday, discovery on the 1st
    assert [s.status for s in report.stages] == ["ok", "skipped", "skipped", "ok"]
    saved = scheduler.load_state(data)
    assert saved.last_run["build"] == at(tuesday) and saved.last_run["events"] == at(MONDAY)

    thursday = date(2026, 11, 12)
    stages = Stages()
    scheduler.execute(CONFIG, "auto", stages.runners(), data_dir=data, now=at(thursday))
    assert stages.calls == ["geo", "events", "build"]


def test_frequency_change_applies_on_next_run(tmp_path: Path) -> None:
    data = tmp_path / "data"
    tuesday = date(2026, 11, 10)
    write_state(data, events=at(MONDAY), build=at(MONDAY), discovery=at(date(2026, 11, 1)))
    daily = CONFIG.model_copy(update={"jobs": CONFIG.jobs.model_copy(update={"events": "daily"})})
    stages = Stages()
    scheduler.execute(daily, "auto", stages.runners(), data_dir=data, now=at(tuesday))
    assert "events" in stages.calls
    off = CONFIG.model_copy(update={"jobs": CONFIG.jobs.model_copy(update={"events": "off"})})
    stages = Stages()
    scheduler.execute(off, "auto", stages.runners(), data_dir=data, now=at(date(2026, 11, 12)))
    assert "events" not in stages.calls


def test_geo_change_forces_discovery_and_resets_flag(tmp_path: Path) -> None:
    data = tmp_path / "data"
    write_state(data, events=at(MONDAY), build=at(MONDAY), discovery=at(date(2026, 11, 1)))
    stages = Stages(geo_forces=True)
    stages.data_dir = data
    report = scheduler.execute(CONFIG, "auto", stages.runners(), data_dir=data, now=at(MONDAY, 6))
    assert stages.calls[:2] == ["geo", "discovery"]
    assert report.stages[1].reason == "forced after a geo change"
    assert scheduler.load_state(data).force_discovery is False


def test_failed_stage_still_builds_and_marks_failure(tmp_path: Path) -> None:
    data = tmp_path / "data"
    stages = Stages(fail="events")
    report = scheduler.execute(CONFIG, "all", stages.runners(), data_dir=data, now=at(MONDAY))
    assert stages.calls == ["geo", "discovery", "events", "build"]
    assert report.failed
    assert "events" not in scheduler.load_state(data).last_run
    assert "build" in scheduler.load_state(data).last_run


def test_events_job_also_rebuilds_the_site(tmp_path: Path) -> None:
    stages = Stages()
    report = scheduler.execute(
        CONFIG, "events", stages.runners(), data_dir=tmp_path / "data", now=at(MONDAY)
    )
    assert stages.calls == ["events", "build"]
    assert [s.status for s in report.stages] == ["ok", "ok"]
    stages = Stages()
    scheduler.execute(CONFIG, "build", stages.runners(), data_dir=tmp_path / "d2", now=at(MONDAY))
    assert stages.calls == ["build"]


def test_dry_run_does_not_touch_state(tmp_path: Path) -> None:
    data = tmp_path / "data"
    scheduler.execute(
        CONFIG, "build", Stages().runners(), data_dir=data, dry_run=True, now=at(MONDAY)
    )
    assert not (data / "state.json").exists()


def test_job_summary_is_written(tmp_path: Path, monkeypatch: Any) -> None:
    from letsgau.crawl import CrawlResult
    from letsgau.merge import MergeReport
    from letsgau.models import Usage

    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    result = CrawlResult(
        events=[],
        batches=[],
        warnings=[],
        usage=Usage(
            requests=3,
            web_searches=17,
            input_tokens=120000,
            output_tokens=9000,
            cost_usd=0.0,
            duration_s=240,
        ),
    )
    result.merge = MergeReport(added=["2026-11-20-a"], cancelled=["2026-11-21-b"])
    runners = Stages().runners()
    runners["events"] = lambda config, dry_run=False: result
    scheduler.execute(CONFIG, "events", runners, data_dir=tmp_path / "data", now=at(MONDAY))
    text = summary.read_text(encoding="utf-8")
    assert "## Let's Gau run (events)" in text
    assert "| events | ok |" in text
    assert "| Web searches / fetches | 17 / 0 |" in text
    assert "| Tokens in / out | 120,000 / 9,000 |" in text
    assert "- New: 1 (`2026-11-20-a`)" in text and "- Cancelled: 1 (`2026-11-21-b`)" in text


def test_run_workflow_matches_the_cli() -> None:
    import yaml

    workflow = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "run.yml").read_text())
    triggers = workflow[True]  # YAML 1.1 reads the key "on" as True
    assert triggers["schedule"][0]["cron"].split()[2:] == ["*", "*", "*"]  # daily
    options = triggers["workflow_dispatch"]["inputs"]["job"]["options"]
    assert tuple(options) == scheduler.JOBS
    assert workflow["concurrency"]["cancel-in-progress"] is False
    assert workflow["permissions"] == {"contents": "write", "pages": "write", "id-token": "write"}
    # deploy only when the run job uploaded a site (e.g. not after a failed build)
    assert "needs.run.outputs.site == 'true'" in workflow["jobs"]["deploy"]["if"]
    assert workflow["jobs"]["run"]["outputs"]["site"] == "${{ steps.upload.outcome == 'success' }}"
    steps = " ".join(str(s) for s in workflow["jobs"]["run"]["steps"])
    assert "git push origin data" in steps and "upload-pages-artifact" in steps
    assert "ANTHROPIC_API_KEY" in str(workflow["jobs"]["run"]["env"])
