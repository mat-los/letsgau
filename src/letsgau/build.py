"""Site build stage: Jinja2 renders the static website into ``site/``.

* ``/`` redirects to the stored language (or the default language).
* ``/<lang>/`` shows today, ``/<lang>/<YYYY-MM-DD>/`` one page per day for
  ``window.display_days`` days. Every event is complete in the HTML (no
  JavaScript needed to read it); ``static/app.js`` only adds filters, search,
  grouping and swiping.
* Links between pages are relative, so the site works on a custom domain and on
  ``<owner>.github.io/<repo>/``; canonical and hreflang URLs use ``site.base_url``.

The build aborts if placeholders are left in ``config.yaml`` or ``site.owner``
does not match ``GITHUB_REPOSITORY_OWNER`` (when that variable is set, i.e. in
GitHub Actions), so a copied configuration never goes live in a fork.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from letsgau import feeds
from letsgau.config import DEFAULT_CONFIG_PATH, Config, find_placeholders
from letsgau.models import Event, EventsFile, EventStatus, PlacesFile

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = REPO_ROOT / "templates"
STATIC_DIR = REPO_ROOT / "static"
I18N_DIR = REPO_ROOT / "i18n"
DATA_DIR = Path("data")
SITE_DIR = Path("site")
WEEKDAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class BuildError(Exception):
    """The site must not be built (placeholders, wrong owner, invalid data)."""


# --- guards -------------------------------------------------------------------------


def guard(config: Config, config_text: str, config_path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Abort on unfilled placeholders or a foreign ``site.owner``."""
    placeholders = find_placeholders(config_text)
    if placeholders:
        listed = ", ".join(f"line {line}: {token}" for line, token in placeholders)
        raise BuildError(f"{config_path} still contains placeholders ({listed}); run letsgau init")
    repo_owner = os.environ.get("GITHUB_REPOSITORY_OWNER", "").strip()
    if repo_owner and repo_owner.casefold() != config.site.owner.casefold():
        raise BuildError(
            f"site.owner is {config.site.owner!r} but this repository belongs to "
            f"{repo_owner!r}; set your own site.owner (and the rest of instance/) first"
        )


# --- days and events ----------------------------------------------------------------


def weekdays_in(spec: str) -> set[int]:
    """``mon-fri`` -> {0..4}, ``sat,sun`` -> {5, 6}."""
    result: set[int] = set()
    for part in spec.split(","):
        if "-" in part:
            start, end = (WEEKDAY_KEYS.index(p) for p in part.split("-", 1))
            span = range(start, end + 1) if start <= end else [*range(start, 7), *range(end + 1)]
            result.update(span)
        else:
            result.add(WEEKDAY_KEYS.index(part))
    return result


def active_on(event: Event, day: date) -> bool:
    s = event.schedule
    if not s.start_date <= day <= s.last_date:
        return False
    if s.dates:
        return day in s.dates
    return True


def is_multi_day(event: Event) -> bool:
    return event.schedule.last_date > event.schedule.start_date


def section_of(event: Event) -> str:
    s = event.schedule
    if s.all_day or s.start_time is None or is_multi_day(event):
        return "all_day"
    hour = int(s.start_time[:2])
    if hour < 12:
        return "morning"
    if hour < 18:
        return "afternoon"
    return "evening"


def hours_today(event: Event, day: date) -> str | None:
    """Opening hours that apply on ``day`` (``16:00–20:00``), if known."""
    for hours in event.schedule.opening_hours or []:
        if day.weekday() in weekdays_in(hours.days):
            return f"{hours.from_}–{hours.to}"
    return None


SECTIONS = ("all_day", "morning", "afternoon", "evening")


@dataclass
class Row:
    """One event row on a day page."""

    event: Event
    children: list[Event] = field(default_factory=list)
    parent: Event | None = None
    """Set for a program item rendered on its own (its main event is not on this day)."""
    standalone_copy_of: Event | None = None
    """Set for the hidden copy of a program item that filters can reveal."""

    @property
    def section(self) -> str:
        return section_of(self.event)


@dataclass
class DayView:
    day: date
    rows: list[Row]
    count: int

    def section(self, name: str) -> list[Row]:
        return [r for r in self.rows if r.section == name]


def sort_key(event: Event) -> tuple[str, str]:
    return (event.schedule.start_time or "", event.title.de.casefold())


def day_view(events: Sequence[Event], day: date) -> DayView:
    """Rows for one day: main events with their program items of that day."""
    today = [e for e in events if active_on(e, day)]
    by_id = {e.id: e for e in today}
    children: dict[str, list[Event]] = {}
    rows: list[Row] = []
    copies: list[Row] = []
    for event in sorted(today, key=sort_key):
        if event.parent_id and event.parent_id in by_id:
            children.setdefault(event.parent_id, []).append(event)
            copies.append(Row(event=event, standalone_copy_of=by_id[event.parent_id]))
        else:
            rows.append(Row(event=event))
    for row in rows:
        row.children = children.get(row.event.id, [])
    count = len(rows) + sum(len(r.children) for r in rows)
    return DayView(day=day, rows=rows + copies, count=count)


# --- rendering ----------------------------------------------------------------------


def load_i18n(lang: str) -> dict[str, Any]:
    return yaml.safe_load((I18N_DIR / f"{lang}.yaml").read_text(encoding="utf-8"))


class Texts(dict[str, Any]):
    """UI strings with ``t('key', name=value)`` formatting."""

    def __call__(self, key: str, **values: Any) -> str:
        text = self[key]
        return str(text).format(**values) if values else str(text)


def format_date(t: Texts, day: date, style: str = "long") -> str:
    return t(
        f"date_{style}",
        weekday=t["weekdays"][day.weekday()],
        day=day.day,
        month=t["months"][day.month - 1],
        month_num=day.month,
        year=day.year,
    )


@dataclass
class Site:
    """Everything the templates need besides the page itself."""

    config: Config
    events: list[Event]
    places: PlacesFile | None
    today: date
    days: list[date]
    generated_at: datetime
    content_pages: dict[str, dict[str, str]] = field(default_factory=dict)
    """lang -> path -> title of the content pages, in footer order."""
    warnings: list[str] = field(default_factory=list)

    def drive_time(self, place: str) -> float | None:
        if not self.places:
            return None
        for p in self.places.places:
            if place in (p.name, p.municipality):
                return p.drive_time_minutes
        return None


def make_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(["html", "xml"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["tojson_compact"] = lambda v: json.dumps(
        v, ensure_ascii=False, separators=(",", ":")
    )
    return env


def page_path(lang: str, day: date | None = None) -> str:
    """Site-relative path of a page, e.g. ``de/2026-11-14/``."""
    return f"{lang}/{day.isoformat()}/" if day else f"{lang}/"


def base_context(site: Site, lang: str, depth: int) -> dict[str, Any]:
    config = site.config
    t = Texts(load_i18n(lang))
    local = site.generated_at.astimezone(ZoneInfo(config.site.timezone))
    return {
        "site": site,
        "config": config,
        "lang": lang,
        "langs": config.site.languages,
        "t": t,
        "root": "../" * depth,
        "base_url": config.site.base_url.rstrip("/") + "/",
        "coverage": t(
            "coverage",
            minutes=config.region.drive_time_minutes,
            label=config.region.label,
            days=config.window.display_days,
        ),
        "updated": f"{format_date(t, local.date(), 'short')} {local:%H:%M}",
        "updated_iso": site.generated_at.isoformat(),
        "tag_label": {tag.id: getattr(tag, lang) for tag in config.tags},
        "fmt": lambda d, style="long": format_date(t, d, style),
        "hours_today": hours_today,
        "is_multi_day": is_multi_day,
        "Status": EventStatus,
    }


def render_day(env: Environment, site: Site, lang: str, day: date, *, is_index: bool) -> str:
    view = day_view(site.events, day)
    ctx = base_context(site, lang, depth=1 if is_index else 2)
    t: Texts = ctx["t"]
    index = site.days.index(day)
    weekend = site.today + timedelta(days=(5 - site.today.weekday()) % 7)
    date_text = format_date(t, day)
    counts = {d: day_view(site.events, d).count for d in site.days}
    ctx.update(
        view=view,
        day=day,
        is_index=is_index,
        canonical=page_path(lang, day),
        title=t("day_title", label=site.config.region.label, date=date_text),
        description=t(
            "day_description", count=view.count, label=site.config.region.label, date=date_text
        ),
        prev_day=site.days[index - 1] if index > 0 else None,
        next_day=site.days[index + 1] if index + 1 < len(site.days) else None,
        date_bar=[(d, counts[d]) for d in site.days],
        quick={
            "today": site.today,
            "tomorrow": site.today + timedelta(days=1),
            "weekend": weekend if weekend in site.days else None,
        },
        sections=SECTIONS,
        drive_time=site.drive_time,
        ld=feeds.json_ld(
            view.rows,
            lang,
            ctx["base_url"] + page_path(lang, day),
            ZoneInfo(site.config.site.timezone),
        ),
    )
    return env.get_template("day.html").render(**ctx)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _load_events(data_dir: Path) -> list[Event]:
    path = data_dir / "events.json"
    if not path.exists():
        return []
    try:
        return EventsFile.model_validate_json(path.read_text(encoding="utf-8")).events
    except ValueError as exc:
        raise BuildError(f"{path} is invalid: {exc}") from exc


def _load_places(data_dir: Path) -> PlacesFile | None:
    path = data_dir / "places.json"
    if not path.exists():
        return None
    return PlacesFile.model_validate_json(path.read_text(encoding="utf-8"))


def local_today(config: Config, now: datetime | None = None) -> date:
    now = now or datetime.now(UTC)
    return now.astimezone(ZoneInfo(config.site.timezone)).date()


def build(
    config: Config,
    *,
    data_dir: Path = DATA_DIR,
    site_dir: Path = SITE_DIR,
    now: datetime | None = None,
    content_dir: Path | None = None,
    changelog: Path | None = None,
) -> Site:
    """Render the whole site into ``site_dir`` (replacing it)."""
    content_dir = content_dir or CONTENT_DIR
    now = (now or datetime.now(UTC)).replace(microsecond=0)
    today = local_today(config, now)
    site = Site(
        config=config,
        events=_load_events(data_dir),
        places=_load_places(data_dir),
        today=today,
        days=[today + timedelta(days=i) for i in range(config.window.display_days)],
        generated_at=now,
    )
    env = make_env()
    pages = load_content(config, content_dir)
    nav = {
        "about": "nav_about",
        "missing-event": "nav_missing",
        "imprint": "nav_imprint",
        "privacy": "nav_privacy",
    }
    for page in pages:
        label = Texts(load_i18n(page.lang))(nav[page.name]) if page.name in nav else page.title
        site.content_pages.setdefault(page.lang, {})[page.path] = label
    for lang in config.site.languages:
        t = Texts(load_i18n(lang))
        site.content_pages.setdefault(lang, {})[FEEDBACK_PATH] = t("nav_feedback")
        site.content_pages[lang]["changelog"] = t("nav_changelog")
    if site_dir.exists():
        shutil.rmtree(site_dir)
    site_dir.mkdir(parents=True)
    shutil.copytree(STATIC_DIR, site_dir / "assets", ignore=shutil.ignore_patterns("*.md"))

    for lang in config.site.languages:
        for day in site.days:
            write(
                site_dir / page_path(lang, day) / "index.html",
                render_day(env, site, lang, day, is_index=False),
            )
        write(
            site_dir / page_path(lang) / "index.html",
            render_day(env, site, lang, today, is_index=True),
        )
    write(
        site_dir / "index.html",
        env.get_template("redirect.html").render(
            langs=config.site.languages, default=config.default_language, site=site
        ),
    )
    for page in pages:
        write(site_dir / page.lang / page.path / "index.html", render_content(env, site, page))
    intros = {p.lang: p for p in load_content(config, content_dir, only=("feedback",))}
    for lang in config.site.languages:
        write(
            site_dir / lang / FEEDBACK_PATH / "index.html",
            render_feedback(env, site, lang, intros.get(lang)),
        )
        write(
            site_dir / lang / "changelog" / "index.html",
            render_changelog(env, site, lang, changelog or REPO_ROOT / "CHANGELOG.md"),
        )
    (site_dir / ".nojekyll").write_text("", encoding="utf-8")
    cname = custom_domain(config.site.base_url)
    if cname:
        (site_dir / "CNAME").write_text(cname + "\n", encoding="utf-8")
    coverage = {lang: base_context(site, lang, 0)["coverage"] for lang in config.site.languages}
    by_name: dict[str, dict[str, str]] = {}
    for page in pages:
        by_name.setdefault(page.name, {})[page.lang] = f"{page.path}/"
    extra = [paths for paths in by_name.values() if len(paths) == len(config.site.languages)]
    extra.append({lang: f"{FEEDBACK_PATH}/" for lang in config.site.languages})
    feeds.write_all(site, site_dir, coverage, extra)
    if not config.site.indexing:
        site.warnings.append("site.indexing is false: pages carry noindex (hidden from search)")
    for warning in site.warnings:
        print(f"warning: build: {warning}", file=sys.stderr)
    return site


# --- content pages (Phase 8) -------------------------------------------------------------

CONTENT_DIR = Path("instance/content")
CONTENT_PAGES = {  # page name -> path per language
    "about": {"de": "about", "en": "about"},
    "missing-event": {"de": "fehlt-mein-event", "en": "missing-event"},
    "imprint": {"de": "impressum", "en": "imprint"},
    "privacy": {"de": "datenschutz", "en": "privacy"},
}
FEEDBACK_PATH = "feedback"
TODO_RE = re.compile(r"\[TODO:[^\]]*\]")


@dataclass
class ContentPage:
    name: str
    lang: str
    path: str
    title: str
    html: str
    todos: list[str]


def content_context(config: Config) -> dict[str, Any]:
    repo = os.environ.get("GITHUB_REPOSITORY") or f"{config.site.owner}/letsgau"
    return {
        "site_name": config.site.name,
        "region_label": config.region.label,
        "drive_time_minutes": config.region.drive_time_minutes,
        "display_days": config.window.display_days,
        "contact_email": config.site.contact_email,
        "base_url": config.site.base_url.rstrip("/"),
        "repo_url": f"https://github.com/{repo}",
        "feedback_endpoint": config.site.feedback_form_endpoint,
    }


def render_markdown(text: str, config: Config) -> tuple[str, str, list[str]]:
    """Fill config values (Jinja) and convert to HTML; returns (title, html, todos)."""
    import markdown

    filled = Environment(undefined=StrictUndefined, autoescape=False).from_string(text)
    body = filled.render(**content_context(config)).strip()
    title = ""
    if body.startswith("# "):
        first, _, body = body.partition("\n")
        title = first[2:].strip()
    html = markdown.markdown(body, extensions=["extra", "sane_lists"], output_format="html")
    return title, html, TODO_RE.findall(body)


def load_content(
    config: Config, content_dir: Path, only: Sequence[str] | None = None
) -> list[ContentPage]:
    """Content pages that exist for the configured languages."""
    names = only or list(CONTENT_PAGES)
    pages: list[ContentPage] = []
    for name in names:
        for lang in config.site.languages:
            path = content_dir / f"{name}.{lang}.md"
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            title, html, todos = render_markdown(text, config)
            slug = CONTENT_PAGES.get(name, {}).get(lang, name)
            pages.append(ContentPage(name, lang, slug, title or name, html, todos))
    return pages


def content_todos(config: Config, content_dir: Path = CONTENT_DIR) -> list[tuple[str, str]]:
    """``[TODO: ...]`` markers left in the rendered content pages: (file name, marker)."""
    names = [*CONTENT_PAGES, "feedback"]
    return [
        (f"{page.name}.{page.lang}.md", todo)
        for page in load_content(config, content_dir, only=names)
        for todo in page.todos
    ]


def custom_domain(base_url: str) -> str | None:
    """Host for ``CNAME``, or ``None`` for ``*.github.io`` addresses."""
    host = base_url.split("://", 1)[-1].split("/", 1)[0].lower()
    if not host or host.endswith(".github.io") or "{" in host:
        return None
    return host


def render_content(env: Environment, site: Site, page: ContentPage) -> str:
    ctx = base_context(site, page.lang, depth=2)
    if page.todos:
        site.warnings.append(
            f"{page.name}.{page.lang}.md still has {len(page.todos)} [TODO] marker(s)"
        )
    alternates = {
        lang: CONTENT_PAGES.get(page.name, {}).get(lang, page.name)
        for lang in site.config.site.languages
    }
    ctx.update(
        title=page.title,
        description=ctx["coverage"],
        canonical=f"{page.lang}/{page.path}/",
        alternates={lang: f"{lang}/{path}/" for lang, path in alternates.items()},
        content=page.html,
    )
    return env.get_template("page.html").render(**ctx)


FEEDBACK_KINDS = ("missing", "error", "idea")


def render_feedback(env: Environment, site: Site, lang: str, intro: ContentPage | None) -> str:
    ctx = base_context(site, lang, depth=2)
    t: Texts = ctx["t"]
    ctx.update(
        title=intro.title if intro else t("nav_feedback"),
        description=ctx["coverage"],
        canonical=f"{lang}/{FEEDBACK_PATH}/",
        alternates={other: f"{other}/{FEEDBACK_PATH}/" for other in site.config.site.languages},
        intro=intro.html if intro else "",
        endpoint=site.config.site.feedback_form_endpoint,
        kinds=FEEDBACK_KINDS,
        repo_url=content_context(site.config)["repo_url"],
    )
    return env.get_template("feedback.html").render(**ctx)


def render_changelog(env: Environment, site: Site, lang: str, path: Path) -> str:
    import markdown

    ctx = base_context(site, lang, depth=2)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    text = re.sub(r"^# .*\n", "", text, count=1)
    ctx.update(
        title=ctx["t"]("nav_changelog"),
        description=ctx["coverage"],
        canonical=f"{lang}/changelog/",
        alternates={other: f"{other}/changelog/" for other in site.config.site.languages},
        intro=ctx["t"]("changelog_intro"),
        content=markdown.markdown(text, extensions=["extra"], output_format="html"),
    )
    return env.get_template("page.html").render(**ctx)


def run(
    config: Config,
    *,
    dry_run: bool = False,
    limit_places: int | None = None,
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> Site:
    """CLI entry point for ``--job build``."""
    text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    guard(config, text, config_path)
    target = SITE_DIR if not dry_run else Path(os.environ.get("TMPDIR", "/tmp")) / "letsgau-site"
    site = build(config, site_dir=target)
    pages = len(config.site.languages) * (len(site.days) + 1)
    print(
        f"build: {pages} day pages, {len(site.events)} events, today {site.today} "
        f"({config.site.timezone}) -> {target}" + ("; dry run" if dry_run else "")
    )
    return site
