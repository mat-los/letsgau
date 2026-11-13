"""Read event source pages as plain text, so unchanged pages cost no AI call.

The source sweeps of the event crawl used to let the agent fetch every known source on
every run (several turns each, through Claude Code's summarising WebFetch). Instead the
crawl downloads each source here (with its next pages), turns the HTML into plain text and
fingerprints it (SHA-256):

* unchanged since the last run → the events read from it then are used again, no AI call;
* changed → the text goes to the AI in a call without web access (``prompts/events.md``,
  mode ``pages``);
* not readable as text (JavaScript calendar, PDF, blocked download, no dates in the text)
  → the page stays with the agent sweep, and text mode is tried again after
  :data:`RETRY_TEXT_DAYS`.

The cache is ``data/pages.json`` (:class:`letsgau.models.PagesFile`). ``robots.txt`` is
respected; requests identify as ``letsgau`` (:mod:`letsgau.flyers`).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

from letsgau import flyers
from letsgau.models import PageCacheEntry, PagesFile

PAGE_CACHE_VERSION = 1
"""Bump when the text extraction or the ``pages`` prompt changes, so pages are read again."""
MAX_PAGES = 5
"""Pages of one source followed via "next" links."""
MAX_CHARS = 40_000
"""Text kept per source (all its pages); the rest is cut."""
MIN_CHARS = 200
"""Less text than this means the events are loaded by JavaScript: agent sweep."""
RETRY_TEXT_DAYS = 28
"""A page that could not be read as text is tried again after this many days."""
KEEP_DAYS = 30
"""Cache entries of pages no longer read for this long are dropped."""

DATE_RE = re.compile(
    r"\b\d{1,2}\.\s?\d{1,2}\.|\b\d{4}-\d{2}-\d{2}\b|"
    r"\b\d{1,2}\.\s?(jan|feb|mär|maer|apr|mai|jun|jul|aug|sep|okt|nov|dez)",
    re.IGNORECASE,
)
SKIP_TAGS = frozenset(
    {"script", "style", "noscript", "svg", "template", "head", "nav", "footer", "form", "iframe"}
)
BLOCK_TAGS = frozenset(
    {
        *("p", "div", "br", "li", "tr", "td", "th", "section", "article", "header", "main"),
        *("h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd", "time", "table", "ul", "ol"),
    }
)
NEXT_WORDS = ("weiter", "nächste", "naechste", "next", "»", "\u203a", ">")
SESSION_PARAM = re.compile(r"^(phpsessid|sid|session\w*|token|_t|ts|cache\w*)$", re.IGNORECASE)


class PageError(Exception):
    """The page cannot be read as text; the reason goes into the cache entry."""


@dataclass
class PageText:
    url: str
    text: str
    sha256: str


class _Text(HTMLParser):
    """Visible text of a page with link targets, and its "next page" link."""

    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base
        self.parts: list[str] = []
        self.skip = 0
        self.href: str | None = None
        self.anchor: list[str] = []
        self.next_url: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag in SKIP_TAGS:
            self.skip += 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "a" and a.get("href"):
            self.href, self.anchor = urljoin(self.base, a["href"]), []
            if "next" in a.get("rel", "").split() and self.next_url is None:
                self.next_url = self.href
        if tag == "link" and "next" in a.get("rel", "").split() and a.get("href"):
            self.next_url = self.next_url or urljoin(self.base, a["href"])

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS and self.skip:
            self.skip -= 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "a" and self.href is not None:
            label = " ".join("".join(self.anchor).split())
            word = label.lower().strip(" .:")
            if self.next_url is None and word and word.startswith(NEXT_WORDS) and len(word) < 20:
                self.next_url = self.href
            link = _clean_link(self.href, self.base)
            if link and len(label) >= 3 and not self.skip:
                self.parts.append(f" <{link}>")
            self.href = None

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        self.parts.append(data)
        if self.href is not None:
            self.anchor.append(data)

    def text(self) -> str:
        lines = (" ".join(line.split()) for line in "".join(self.parts).splitlines())
        return "\n".join(line for line in lines if line)


def _clean_link(url: str, base: str) -> str | None:
    """A link worth keeping in the text (a detail page on the same site, or an Instagram
    account), without session parameters, so the fingerprint does not change on every
    download."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return None
    if parts.netloc != urlsplit(base).netloc:
        # other sites only for Instagram (the crawl collects the accounts pages link to)
        return url if parts.netloc.lower().endswith("instagram.com") else None
    query = "&".join(
        p for p in parts.query.split("&") if p and not SESSION_PARAM.match(p.split("=")[0])
    )
    clean = urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))
    return None if clean.rstrip("/") == base.split("#")[0].rstrip("/") else clean


def _decode(body: bytes, content_type: str) -> str:
    match = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE)
    if match is None:
        match = re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', body[:4000], re.IGNORECASE)
    charset = match.group(1) if match else "utf-8"
    name = charset.decode() if isinstance(charset, bytes) else charset
    try:
        return body.decode(name)
    except (LookupError, UnicodeDecodeError):
        return body.decode("cp1252", "replace")


def read(url: str, fetch: flyers.Fetch, robots: flyers.Robots) -> PageText:
    """The text of ``url`` and up to :data:`MAX_PAGES` - 1 following pages.

    Raises :class:`PageError` if the page cannot be read as text."""
    texts: list[str] = []
    seen: set[str] = set()
    current: str | None = url
    while current and current not in seen and len(seen) < MAX_PAGES:
        seen.add(current)
        if not robots.allowed(current):
            if current == url:
                raise PageError("robots.txt disallows it")
            break
        try:
            body, content_type = fetch(current, flyers.MAX_PAGE_BYTES)
        except Exception as exc:  # any download problem: agent sweep instead
            if current == url:
                raise PageError(f"download failed: {exc}") from exc
            break
        if "html" not in content_type.lower() and body.lstrip()[:1] != b"<":
            if current == url:
                raise PageError(f"not HTML ({content_type or 'unknown type'})")
            break
        parser = _Text(current)
        parser.feed(_decode(body, content_type))
        texts.append(
            parser.text() if current == url else f"--- next page: {current}\n" + parser.text()
        )
        current = parser.next_url
    text = "\n".join(texts)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n[text cut]"
    if len(text) < MIN_CHARS or not DATE_RE.search(text):
        raise PageError("no dates in the text (loaded by JavaScript?)")
    return PageText(url=url, text=text, sha256=hashlib.sha256(text.encode()).hexdigest())


def needs_agent(entry: PageCacheEntry | None, today: date) -> bool:
    """True if the page was not readable as text recently, so it is not downloaded again."""
    return (
        entry is not None
        and entry.mode == "agent"
        and entry.version == PAGE_CACHE_VERSION
        and entry.last_changed > today - timedelta(days=RETRY_TEXT_DAYS)
    )


def unchanged(entry: PageCacheEntry | None, page: PageText) -> bool:
    return (
        entry is not None
        and entry.mode == "text"
        and entry.version == PAGE_CACHE_VERSION
        and entry.sha256 == page.sha256
    )


def prune(cache: PagesFile, today: date) -> PagesFile:
    keep_after = today - timedelta(days=KEEP_DAYS)
    return cache.model_copy(
        update={"pages": {u: e for u, e in cache.pages.items() if e.last_seen >= keep_after}}
    )
