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

from letsgau import __version__, build, crawl, discovery, geo
from letsgau import init as init_cmd
from letsgau.ai_runner import AiError
from letsgau.config import DEFAULT_CONFIG_PATH, ConfigError, find_placeholders, load_config
from letsgau.crawl import CrawlError
from letsgau.geo import GeoError
from letsgau.scheduler import JOBS, STAGES

# Each stage runs one or more modules in order; "events" = crawl (which calls merge).
STAGE_MODULES = {
    "geo": (geo,),
    "discovery": (discovery,),
    "events": (crawl,),
    "build": (build,),
}


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
    if job == "auto":
        print("Scheduler not implemented yet (Phase 6): no jobs run.")
        return 0
    stages = STAGES if job == "all" else (job,)
    for stage in stages:
        for module in STAGE_MODULES[stage]:
            kwargs = {"force": force} if module is geo else {}
            try:
                module.run(config, dry_run=dry_run, limit_places=limit_places, **kwargs)
            except NotImplementedError as exc:
                print(f"error: stage {stage!r}: {exc}", file=sys.stderr)
                return 2
            except (GeoError, CrawlError, AiError) as exc:
                print(f"error: stage {stage!r}: {exc}", file=sys.stderr)
                return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "check":
            return _cmd_check(args.config)
        if args.command == "run":
            return _cmd_run(args.config, args.job, args.dry_run, args.limit_places, args.force)
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
    return 1  # pragma: no cover - argparse enforces a command


if __name__ == "__main__":
    raise SystemExit(main())
