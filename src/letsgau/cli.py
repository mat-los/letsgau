"""Command-line interface: ``python -m letsgau <command>`` or ``letsgau <command>``.

Commands:

* ``check``  validate ``instance/config.yaml`` and report placeholders
* ``run``    run due jobs (``--job auto``) or a specific stage
* ``init``   set up a fresh instance for a new region
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from letsgau import __version__, build, crawl, discovery, geo, scheduler
from letsgau import init as init_cmd
from letsgau.ai_runner import AiError
from letsgau.build import BuildError
from letsgau.config import DEFAULT_CONFIG_PATH, ConfigError, find_placeholders, load_config
from letsgau.crawl import CrawlError
from letsgau.geo import GeoError
from letsgau.scheduler import JOBS


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="letsgau",
        description="Let's Gau: AI-curated event calendar for a configurable region.",
    )
    parser.add_argument("--version", action="version", version=f"letsgau {__version__}")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"path to the instance config (default: {DEFAULT_CONFIG_PATH})",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    sub.add_parser("check", help="validate the config file and report placeholders")

    run = sub.add_parser("run", help="run due jobs or a single stage")
    run.add_argument(
        "--job",
        choices=JOBS,
        default="auto",
        help="auto = only due jobs (default); all = every stage; or a single stage",
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="do everything except writing data/ and site/",
    )
    run.add_argument(
        "--limit-places",
        type=int,
        metavar="N",
        help="events: only crawl the N nearest municipalities (for testing; geo lists all)",
    )
    run.add_argument(
        "--force",
        action="store_true",
        help="recompute geo even if center, drive time and place filters are unchanged",
    )

    sources = sub.add_parser(
        "sources", help="list whitelist sources (templates expanded per municipality)"
    )
    sources.add_argument("--check", action="store_true", help="fetch every URL, show status")
    sources.add_argument(
        "--places",
        type=Path,
        default=Path("data/places.json"),
        help="places file used to expand templates (default: data/places.json)",
    )

    sub.add_parser("init", help="set up a fresh instance in instance/ for a new region")
    return parser


def _cmd_check(config_path: Path) -> int:
    config = load_config(config_path)
    placeholders = find_placeholders(config_path.read_text(encoding="utf-8"))
    for line, token in placeholders:
        print(f"warning: {config_path}:{line}: unfilled placeholder {token}", file=sys.stderr)
    print(
        f"OK: {config_path} is valid ({config.region.label}, "
        f"{config.region.drive_time_minutes} min drive time, "
        f"{len(config.tags)} tags, auth={config.ai.auth})"
    )
    return 0


def load_dotenv(path: Path = Path(".env")) -> None:
    """Load ``KEY=value`` lines from ``.env`` without overriding existing variables."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)


def _cmd_run(
    config_path: Path, job: str, dry_run: bool, limit_places: int | None, force: bool
) -> int:
    config = load_config(config_path)
    load_dotenv()
    runners = {
        "geo": lambda cfg, dry_run: geo.run(cfg, dry_run=dry_run, force=force),
        "discovery": lambda cfg, dry_run: discovery.run(
            cfg, dry_run=dry_run, limit_places=limit_places
        ),
        "events": lambda cfg, dry_run: crawl.run(cfg, dry_run=dry_run, limit_places=limit_places),
        "build": lambda cfg, dry_run: build.run(cfg, dry_run=dry_run, config_path=config_path),
    }
    report = scheduler.execute(
        config,
        job,
        runners,
        dry_run=dry_run,
        errors=(GeoError, CrawlError, AiError, BuildError),
    )
    if report.failed:
        return 1
    if job not in ("auto", "all") and report.stages and report.stages[0].status == "skipped":
        print(f"error: stage {job!r}: {report.stages[0].reason}", file=sys.stderr)
        return 2
    return 0


def _cmd_sources(config_path: Path, places: Path, check: bool) -> int:
    config = load_config(config_path)
    entries = crawl.list_sources(config, places)
    ok = 0
    for entry in entries:
        status = crawl.check_url(entry.url) if check else ""
        ok += status.startswith("2")
        where = ", ".join(entry.places) or "first batch"
        print(f"{status:>5}  {entry.url}  ({where})" if check else f"{entry.url}  ({where})")
    if check:
        print(f"{ok} of {len(entries)} sources reachable")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "check":
            return _cmd_check(args.config)
        if args.command == "run":
            return _cmd_run(args.config, args.job, args.dry_run, args.limit_places, args.force)
        if args.command == "sources":
            return _cmd_sources(args.config, args.places, args.check)
        if args.command == "init":
            init_cmd.run()
            return 0
    except ConfigError as exc:
        print(f"error: invalid configuration in {exc.path}", file=sys.stderr)
        for problem in exc.problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    except NotImplementedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except CrawlError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 1  # pragma: no cover - argparse enforces a command


if __name__ == "__main__":
    raise SystemExit(main())
