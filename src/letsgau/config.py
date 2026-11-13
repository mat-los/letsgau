"""Load and validate ``instance/config.yaml``.

The configuration is parsed with PyYAML and validated with Pydantic. Unknown
keys are rejected, so a typo such as ``municipalitys`` fails loudly. Every
validation error is reported with the file, the line number and the dotted
path of the offending field, e.g.::

    instance/config.yaml:16: region.municipalitys: unknown field
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

DEFAULT_CONFIG_PATH = Path("instance/config.yaml")

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_SCHEDULE_RE = re.compile(
    r"^(off|daily|weekly:(mon|tue|wed|thu|fri|sat|sun)(,(mon|tue|wed|thu|fri|sat|sun))*"
    r"|monthly:([1-9]|[12][0-9]|3[01]))$"
)
_PLACEHOLDER_RE = re.compile(r"\{\{[A-Z0-9_]+\}\}|<github-owner>")

TagId = Annotated[str, Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")]
Language = Literal["de", "en"]


class ConfigError(Exception):
    """Raised when the configuration file is missing, malformed or invalid.

    ``problems`` holds one human-readable line per error.
    """

    def __init__(self, path: Path, problems: list[str]) -> None:
        self.path = path
        self.problems = problems
        super().__init__(f"Invalid configuration in {path}:\n" + "\n".join(problems))


class _Strict(BaseModel):
    """Base model: unknown keys are errors, values are immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SiteConfig(_Strict):
    name: str = Field(min_length=1)
    base_url: str = Field(pattern=r"^(https?://\S+|\{\{[A-Z_]+\}\})$")
    languages: list[Language] = Field(min_length=1)
    owner: str = Field(min_length=1)
    contact_email: str = ""
    """Shown on the pages (imprint, about, ...) only if set; optional with a form service."""
    feedback_form_endpoint: str = ""
    timezone: str = "Europe/Berlin"
    """IANA time zone of the region; decides what "today" is on the website."""
    indexing: bool = True
    """False adds ``noindex`` to every page and stops inviting crawlers in robots.txt."""
    goatcounter: str = Field(default="", pattern=r"^([a-z0-9-]+|\{\{[A-Z_]+\}\})?$")
    """GoatCounter site code (``<code>.goatcounter.com``); empty = no visitor counting."""

    @model_validator(mode="after")
    def _some_contact(self) -> SiteConfig:
        if not self.contact_email and not self.feedback_form_endpoint:
            raise ValueError("set contact_email or feedback_form_endpoint (or both)")
        if self.contact_email and "@" not in self.contact_email and "{{" not in self.contact_email:
            raise ValueError("contact_email needs an @")
        return self

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown time zone {value!r}") from exc
        return value

    @field_validator("languages")
    @classmethod
    def _unique_languages(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("languages must not contain duplicates")
        return value


class Municipality(_Strict):
    """A municipality (Gemeinde, Markt, Stadt) in the area, by name and/or postcode.

    The postcode tells apart municipalities of the same name (there are several Altenstadts);
    with only a postcode, every municipality in that postcode area is taken.
    """

    name: str = ""
    postcode: str = Field(default="", pattern=r"^(\d{4,5})?$")

    @model_validator(mode="after")
    def _name_or_postcode(self) -> Municipality:
        if not self.name.strip() and not self.postcode:
            raise ValueError("a municipality needs a name, a postcode or both")
        return self

    @property
    def label(self) -> str:
        return self.name.strip() or self.postcode


class RegionConfig(_Strict):
    label: str = Field(min_length=1)
    """Name of the region in page titles ("Was ist los in und um <label>")."""
    municipalities: list[Municipality] = Field(min_length=1)
    """The municipalities whose events are collected, in this order (the first is the main
    one). Their districts (Ortsteile) are found automatically."""


class WindowConfig(_Strict):
    display_days: int = Field(ge=1, le=90)
    crawl_days: int = Field(ge=1, le=90)
    deep_check_days: int = Field(ge=0, le=90)
    store_days: int = Field(default=180, ge=1, le=400)
    """Events seen up to this far ahead are kept (searching stops at ``crawl_days``)."""

    @model_validator(mode="after")
    def _consistent(self) -> WindowConfig:
        if self.crawl_days < self.display_days:
            raise ValueError("crawl_days must be >= display_days")
        if self.store_days < self.crawl_days:
            raise ValueError("store_days must be >= crawl_days")
        if self.deep_check_days > self.crawl_days:
            raise ValueError("deep_check_days must be <= crawl_days")
        return self


class JobsConfig(_Strict):
    """Job frequencies. Format: ``off | daily | weekly:mon,thu | monthly:1``."""

    discovery: str
    events: str
    build: str

    @field_validator("discovery", "events", "build")
    @classmethod
    def _valid_schedule(cls, value: str) -> str:
        if not _SCHEDULE_RE.match(value):
            raise ValueError(
                f"invalid schedule {value!r}; expected off, daily, "
                "weekly:<day>[,<day>...] (mon..sun) or monthly:<1-31>"
            )
        return value


class ModelsConfig(_Strict):
    discovery: str = Field(min_length=1)
    events: str = Field(min_length=1)
    merge: str = Field(min_length=1)


class AiConfig(_Strict):
    auth: Literal["subscription", "api_key"]


class LimitsConfig(_Strict):
    max_web_searches_per_run: int = Field(ge=1)
    places_per_request: int = Field(ge=1, le=50)
    max_runtime_minutes: int = Field(ge=1, le=360)


class DepthConfig(_Strict):
    """How thorough discovery and the event crawl are (more AI calls = more complete)."""

    discovery_focus_passes: bool = True
    """Three focused discovery calls per batch (official, clubs, venues) instead of one."""
    discovery_places_per_request: int = Field(default=2, ge=1, le=50)
    discovery_searches_per_request: int = Field(default=20, ge=3, le=100)
    crawl_sources_per_request: int = Field(default=4, ge=0, le=50)
    """Known sources per "source sweep" call; 0 = one combined call per batch."""
    crawl_searches_per_request: int = Field(default=13, ge=3, le=100)
    """Search limit of the open-search call (and of the combined call)."""
    open_search: Literal["every_run", "alternate", "off"] = "alternate"
    """When sweep mode runs the open search: every run, every other run, or never."""


class EventsConfig(_Strict):
    unconfirmed_after_misses: int = Field(ge=1)
    description_max_chars: int = Field(ge=20, le=2000)


class SourcesConfig(_Strict):
    whitelist_file: str
    blacklist_file: str
    flyers_file: str = ""
    """Pages whose images/PDFs are downloaded and read by the AI; empty = none."""
    instagram_file: str = ""
    """Instagram accounts read on every events run (``{username, places, note}``); empty =
    Instagram off (no reading, no mining of account names)."""
    instagram_candidates_file: str = ""
    """Candidate Instagram accounts (``{username, name, places, website, found_on}``), e.g.
    written by the scheduled lookup session; each is checked against its profile before it is
    read. Empty = none."""


class InstagramConfig(_Strict):
    """Reading Instagram business accounts (needs INSTAGRAM_ACCESS_TOKEN / INSTAGRAM_USER_ID)."""

    posts_per_account: int = Field(default=6, ge=1, le=25)
    """Latest posts fetched per account and run."""
    max_post_age_days: int = Field(default=21, ge=1, le=120)
    """Older posts are not read (announcements are usually posted shortly before)."""
    max_accounts_per_run: int = Field(default=40, ge=0, le=200)
    """Accounts read per run: the list first, then mined accounts."""
    read_mined: bool = True
    """Also read accounts found in links on pages the crawl reads, not only the list."""
    api_version: str = Field(default="", pattern=r"^(v\d+\.\d+)?$")
    """Graph API version such as ``v24.0``; empty = the app's default version."""
    prescreen: bool = True
    """Before reading a post's images, a text-only call (``models.merge``) drops posts whose
    caption is clearly not about an upcoming event; when in doubt, a post is read."""


class Tag(_Strict):
    id: TagId
    de: str = Field(min_length=1)
    en: str = Field(min_length=1)


class Config(_Strict):
    """Root of ``config.yaml``."""

    site: SiteConfig
    region: RegionConfig
    window: WindowConfig
    jobs: JobsConfig
    models: ModelsConfig
    ai: AiConfig
    limits: LimitsConfig
    depth: DepthConfig = DepthConfig()
    events: EventsConfig
    sources: SourcesConfig
    instagram: InstagramConfig = InstagramConfig()
    excluded_topics: list[str] = []
    excluded_title_patterns: list[str] = []
    """Case-insensitive regular expressions; an event whose German title matches one is
    dropped without asking the AI (the obvious members-only events)."""
    tags: list[Tag] = Field(min_length=1)

    @field_validator("excluded_title_patterns")
    @classmethod
    def _valid_patterns(cls, value: list[str]) -> list[str]:
        for pattern in value:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid regular expression {pattern!r}: {exc}") from exc
        return value

    def excluded_title(self, title: str) -> str | None:
        """The first ``excluded_title_patterns`` entry that matches ``title``, or None."""
        return next(
            (p for p in self.excluded_title_patterns if re.search(p, title, re.IGNORECASE)), None
        )

    @field_validator("tags")
    @classmethod
    def _unique_tags(cls, value: list[Tag]) -> list[Tag]:
        ids = [tag.id for tag in value]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate tag ids: {', '.join(duplicates)}")
        return value

    @property
    def default_language(self) -> str:
        """First entry of ``site.languages``."""
        return self.site.languages[0]

    @property
    def tag_ids(self) -> list[str]:
        return [tag.id for tag in self.tags]


def _node_for_path(root: yaml.Node | None, loc: tuple[int | str, ...]) -> yaml.Node | None:
    """Return the deepest YAML node along ``loc`` (key node for the last mapping key)."""
    node = root
    best = root
    for part in loc:
        if isinstance(node, yaml.MappingNode):
            for key_node, value_node in node.value:
                if key_node.value == part:
                    best = key_node
                    node = value_node
                    break
            else:
                return best
        elif isinstance(node, yaml.SequenceNode) and isinstance(part, int):
            if part >= len(node.value):
                return best
            node = node.value[part]
            best = node
        else:
            return best
    # Prefer pointing at the value for scalars, at the key otherwise.
    return node if isinstance(node, yaml.ScalarNode) else best


def _format_error(err: dict[str, Any], root: yaml.Node | None, path: Path) -> str:
    loc = tuple(err["loc"])
    field = ".".join(str(p) for p in loc) or "<root>"
    node = _node_for_path(root, loc)
    line = node.start_mark.line + 1 if node is not None else 1
    kind = err["type"]
    if kind == "extra_forbidden":
        message = "unknown field (typo?)"
    elif kind == "missing":
        message = "required field is missing"
    else:
        message = err["msg"].removeprefix("Value error, ")
    return f"{path}:{line}: {field}: {message}"


def parse_config(text: str, path: Path = DEFAULT_CONFIG_PATH) -> Config:
    """Parse and validate configuration text. Raises :class:`ConfigError`."""
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = mark.line + 1 if mark is not None else 1
        problem = getattr(exc, "problem", None) or str(exc)
        raise ConfigError(path, [f"{path}:{line}: YAML syntax error: {problem}"]) from exc

    if not isinstance(data, dict):
        raise ConfigError(path, [f"{path}:1: top level must be a mapping"])

    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        problems = [_format_error(e, root, path) for e in exc.errors()]
        raise ConfigError(path, problems) from exc


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> Config:
    """Load and validate the configuration file at ``path``."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(path, [f"{path}: file not found"]) from exc
    return parse_config(text, path)


def find_placeholders(text: str) -> list[tuple[int, str]]:
    """Return ``(line, placeholder)`` pairs for unfilled template placeholders."""
    hits: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        hits.extend((number, m.group(0)) for m in _PLACEHOLDER_RE.finditer(line))
    return hits
