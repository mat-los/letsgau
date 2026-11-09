"""Scheduler: decide which jobs are due and run them in order.

The workflow starts once a day (``--job auto``). This module reads
``config.yaml`` and ``data/state.json`` and runs only what is due:

* ``geo`` always runs; it does nothing unless center, drive time or the place
  filters changed (then it sets ``force_discovery``).
* ``discovery`` and ``events`` are due when their most recent scheduled day
  (``jobs`` in ``config.yaml``, today or earlier) is after their last successful
  run, so a missed run is caught up and a changed frequency applies at once.
  ``force_discovery`` makes discovery due regardless of its schedule.
* ``build`` is due by its schedule (default daily) and after every events run.

A failing stage does not stop the build (the website must show the right
"today"), but the run ends with a non-zero exit code. Every run writes a
summary (stages, event changes, searches, tokens, cost) to stdout and, in
GitHub Actions, to the job summary.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from letsgau.config import WEEKDAYS, Config
from letsgau.models import State, Usage

JOBS = ("auto", "geo", "discovery", "events", "build", "all")
"""Values accepted by ``letsgau run --job`` and the workflow_dispatch input."""

STAGES = ("geo", "discovery", "events", "build")
"""Stages in execution order. ``events`` includes crawl + merge."""


@dataclass(frozen=True)
class Schedule:
    """Parsed job frequency (``off | daily | weekly:mon,thu | monthly:1``)."""

    kind: str
    weekdays: tuple[int, ...] = ()
    day_of_month: int | None = None

    @classmethod
    def parse(cls, value: str) -> Schedule:
        if value in ("off", "daily"):
            return cls(kind=value)
        kind, _, arg = value.partition(":")
        if kind == "weekly" and arg:
            return cls(kind=kind, weekdays=tuple(WEEKDAYS.index(d) for d in arg.split(",")))
        if kind == "monthly" and arg.isdigit():
            return cls(kind=kind, day_of_month=int(arg))
        raise ValueError(f"invalid schedule: {value!r}")

    def matches(self, day: date) -> bool:
        """True if the job is scheduled on ``day`` (ignores previous runs)."""
        if self.kind == "daily":
            return True
        if self.kind == "weekly":
            return day.weekday() in self.weekdays
        if self.kind == "monthly":
            return day.day == self.day_of_month
        return False


def last_due_day(schedule: Schedule, today: date, horizon: int = 62) -> date | None:
    """Most recent day up to ``today`` on which the job is scheduled."""
    for offset in range(horizon):
        day = today - timedelta(days=offset)
        if schedule.matches(day):
            return day
    return None


def is_due(schedule: Schedule, last_run: datetime | None, today: date, tz: ZoneInfo) -> bool:
    due_day = last_due_day(schedule, today)
    if due_day is None:
        return False
    if last_run is None:
        return True
    return last_run.astimezone(tz).date() < due_day


# --- execution ------------------------------------------------------------------------

DATA_DIR = Path("data")
StageFn = Callable[..., Any]


@dataclass
class StageResult:
    name: str
    status: str  # "ok", "failed", "skipped"
    reason: str = ""
    result: Any = None
    seconds: float = 0.0


@dataclass
class RunReport:
    job: str
    started: datetime
    stages: list[StageResult] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return any(s.status == "failed" for s in self.stages)

    def usage(self) -> Usage:
        total = Usage()
        for stage in self.stages:
            usage = getattr(stage.result, "usage", None)
            if isinstance(usage, Usage):
                total = total.add(usage)
        return total


def load_state(data_dir: Path = DATA_DIR) -> State:
    path = data_dir / "state.json"
    if not path.exists():
        return State()
    return State.model_validate_json(path.read_text(encoding="utf-8"))


def save_run(stage: str, now: datetime, data_dir: Path = DATA_DIR) -> None:
    state = load_state(data_dir)
    state.last_run[stage] = now
    if stage == "discovery":
        state.force_discovery = False
    path = data_dir / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")


def decide(stage: str, config: Config, state: State, today: date, events_ran: bool) -> str | None:
    """Why ``stage`` is due, or ``None`` if it is not."""
    tz = ZoneInfo(config.site.timezone)
    last = state.last_run.get(stage)
    if stage == "geo":
        return "always checked (no-op without changes)"
    if stage == "discovery":
        if state.force_discovery:
            return "forced after a geo change"
        schedule = Schedule.parse(config.jobs.discovery)
    elif stage == "events":
        schedule = Schedule.parse(config.jobs.events)
    elif stage == "build":
        if events_ran:
            return "events changed"
        schedule = Schedule.parse(config.jobs.build)
    else:  # pragma: no cover - STAGES is fixed
        raise ValueError(stage)
    if is_due(schedule, last, today, tz):
        due_day = last_due_day(schedule, today)
        return f"scheduled ({getattr(config.jobs, stage)}, due {due_day})"
    return None


def execute(
    config: Config,
    job: str,
    runners: Mapping[str, StageFn],
    *,
    dry_run: bool = False,
    data_dir: Path = DATA_DIR,
    now: datetime | None = None,
    errors: tuple[type[BaseException], ...] = (Exception,),
) -> RunReport:
    """Run ``job`` (``auto``, ``all`` or a stage) with the given stage functions."""
    now = (now or datetime.now(UTC)).replace(microsecond=0)
    today = now.astimezone(ZoneInfo(config.site.timezone)).date()
    report = RunReport(job=job, started=now)
    wanted: Sequence[str] = STAGES if job in ("auto", "all") else (job,)
    events_ran = False
    failed_before_build = False
    for stage in wanted:
        if job == "auto":
            reason = decide(stage, config, load_state(data_dir), today, events_ran)
            if reason is None:
                report.stages.append(StageResult(stage, "skipped", "not due"))
                continue
        else:
            reason = "requested"
        started = datetime.now(UTC)
        _log(f"stage {stage}: {reason}")
        try:
            result = runners[stage](config, dry_run=dry_run)
        except NotImplementedError as exc:
            report.stages.append(StageResult(stage, "skipped", f"not implemented: {exc}"))
            continue
        except errors as exc:
            print(f"error: stage {stage!r}: {exc}", file=sys.stderr)
            report.stages.append(StageResult(stage, "failed", str(exc)))
            failed_before_build = True
            continue
        seconds = (datetime.now(UTC) - started).total_seconds()
        report.stages.append(StageResult(stage, "ok", reason, result, round(seconds, 1)))
        if stage == "events":
            events_ran = True
        if not dry_run:
            save_run(stage, now, data_dir)
    if failed_before_build:
        _log("a stage failed; see the errors above")
    write_summary(report, dry_run=dry_run)
    return report


# --- summary ----------------------------------------------------------------------------


def summary_markdown(report: RunReport, dry_run: bool = False) -> str:
    lines = [
        f"## Let's Gau run ({report.job}{', dry run' if dry_run else ''})",
        "",
        f"Started {report.started.isoformat()}",
        "",
        "| Stage | Status | Details |",
        "| --- | --- | --- |",
    ]
    for stage in report.stages:
        lines.append(f"| {stage.name} | {stage.status} | {_details(stage)} |")
    usage = report.usage()
    if usage.requests:
        lines += [
            "",
            "| AI usage | |",
            "| --- | --- |",
            f"| Requests | {usage.requests} |",
            f"| Web searches / fetches | {usage.web_searches} / {usage.web_fetches} |",
            f"| Tokens in / out | {usage.input_tokens:,} / {usage.output_tokens:,} |",
            f"| Cost at API prices | ${usage.cost_usd:.2f} (subscription: not billed, "
            "counts against the plan's usage limits) |",
            f"| AI time | {usage.duration_s:.0f} s |",
        ]
    merge = getattr(_result(report, "events"), "merge", None)
    if merge is not None:
        lines += ["", "### Event changes", ""]
        for label, items in (
            ("New", merge.added),
            ("Updated", merge.updated),
            ("Cancelled", merge.cancelled),
            ("Postponed", merge.postponed),
            ("Now unconfirmed", merge.unconfirmed),
            ("Excluded by topic", list(merge.excluded)),
            ("Past, deleted", merge.deleted),
        ):
            shown = ", ".join(f"`{i}`" for i in items[:15]) + (" …" if len(items) > 15 else "")
            lines.append(f"- {label}: {len(items)}" + (f" ({shown})" if items else ""))
    return "\n".join(lines) + "\n"


def _result(report: RunReport, stage: str) -> Any:
    return next((s.result for s in report.stages if s.name == stage and s.status == "ok"), None)


def _details(stage: StageResult) -> str:
    result = stage.result
    if stage.status != "ok" or result is None:
        return stage.reason.replace("|", "/")
    if stage.name == "geo":
        places = result.places.places if result.places else []
        changed = "recomputed" if result.recomputed else "unchanged"
        return f"{len(places)} places, {changed}"
    if stage.name == "events":
        return (
            f"{len(result.events)} events from {len(result.batches)} batches, {stage.seconds:.0f} s"
        )
    if stage.name == "build":
        hidden = "" if result.config.site.indexing else ", noindex (hidden from search)"
        return f"{len(result.days)} days, {len(result.events)} events, today {result.today}{hidden}"
    return stage.reason


def write_summary(report: RunReport, *, dry_run: bool = False) -> None:
    text = summary_markdown(report, dry_run)
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(text)
    status = ", ".join(f"{s.name}: {s.status}" for s in report.stages)
    print(f"run: {status}")


def _log(message: str) -> None:
    print(f"run: {message}", file=sys.stderr, flush=True)
