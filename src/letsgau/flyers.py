"""Download flyer images and PDFs from the pages in ``instance/sources/flyers.yaml``.

Some venues publish their programme only as posters, brochure images or PDFs,
which a text fetch cannot read. For pages on the flyer list (and only those) the
event crawl downloads the files here and lets the AI look at them
(``AiRequest.files``; Claude reads images and PDFs).

* The page itself may be an image or a PDF; otherwise its HTML is parsed for
  ``<img>`` (``src``, lazy-load attributes, the largest ``srcset`` entry),
  ``og:image`` and links to image or PDF files, in page order.
* Logos, icons, social buttons, SVG/GIF and tiny images (< ``MIN_IMAGE_BYTES``,
  thumbnails) are skipped; images above ``MAX_IMAGE_BYTES`` and PDFs above
  ``MAX_PDF_BYTES`` too. At most ``max_files`` files per page.
* ``robots.txt`` is respected; requests identify as ``letsgau``.

:func:`run_flyer_call` is the event crawl's call for one flyer page: only files not in the
flyer cache (``data/flyers.json``, keyed by SHA-256) are sent to the AI.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
import urllib.error
import urllib.request
import urllib.robotparser
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from letsgau import crawl  # circular on purpose: used only inside functions
from letsgau.ai_runner import FILES_DIR, AiError, AiRequest, Runner
from letsgau.config import Config
from letsgau.models import Event, FlyerCacheEntry, FlyerCacheFile, Usage
from letsgau.util import logger

USER_AGENT = "letsgau/0.1 (+https://github.com/mat-los/letsgau)"
TIMEOUT_S = 20
MIN_IMAGE_BYTES = 20_000
MAX_IMAGE_BYTES = 5_000_000
MAX_PDF_BYTES = 20_000_000
MAX_PAGE_BYTES = 3_000_000
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")
SKIP_EXT = (".svg", ".gif", ".ico", ".bmp", ".avif")
SKIP_NAME = re.compile(
    r"logo|icon|favicon|sprite|avatar|emoji|placeholder|spinner|loader|button|arrow|"
    r"social|facebook|instagram|whatsapp|twitter|youtube|tiktok|cookie|badge|pixel",
    re.IGNORECASE,
)

Fetch = Callable[[str, int], tuple[bytes, str]]
"""(url, max_bytes) -> (body, content type); raises ``OSError`` on failure."""


@dataclass
class FlyerFile:
    path: Path
    url: str
    kind: str  # "image" | "pdf"
    sha256: str = ""
    """Fingerprint of the file contents (key of the flyer cache)."""


def http_fetch(url: str, max_bytes: int) -> tuple[bytes, str]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise OSError(f"{url}: larger than {max_bytes} bytes")
        return body, response.headers.get("Content-Type", "")


class Robots:
    """Cached ``robots.txt`` checks per host (unreachable robots.txt = allowed)."""

    def __init__(self, fetch: Fetch) -> None:
        self.fetch = fetch
        self.cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
        if host not in self.cache:
            try:
                body, _ = self.fetch(host + "/robots.txt", 500_000)
                parser = urllib.robotparser.RobotFileParser()
                parser.parse(body.decode("utf-8", "replace").splitlines())
                self.cache[host] = parser
            except (OSError, urllib.error.URLError, ValueError):
                self.cache[host] = None
        parser = self.cache[host]
        return parser is None or parser.can_fetch("letsgau", url)


class _Links(HTMLParser):
    """Image and document URLs of a page, in page order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "img":
            best = _largest_srcset(a.get("srcset", "") or a.get("data-srcset", ""))
            for url in (best, a.get("data-src"), a.get("data-lazy-src"), a.get("src")):
                if url:
                    self.urls.append(url)
                    break
        elif tag == "meta" and a.get("property") == "og:image" and a.get("content"):
            self.urls.append(a["content"])
        elif tag == "a" and a.get("href"):
            path = urlsplit(a["href"]).path.lower()
            if path.endswith((*IMAGE_EXT, ".pdf")):
                self.urls.append(a["href"])


def _largest_srcset(srcset: str) -> str:
    best, width = "", -1
    for part in srcset.split(","):
        bits = part.strip().split()
        if not bits:
            continue
        size = bits[1] if len(bits) > 1 else ""
        w = int(size[:-1]) if size.endswith("w") and size[:-1].isdigit() else 0
        if w > width:
            best, width = bits[0], w
    return best


def _kind(body: bytes, content_type: str, url: str) -> str | None:
    if body.startswith(b"%PDF"):
        return "pdf"
    if body.startswith(b"\xff\xd8\xff") or body.startswith(b"\x89PNG"):
        return "image"
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image"
    head = body.lstrip()[:200].lower()
    if "html" in content_type or head.startswith((b"<!doctype html", b"<html")):
        return "html"
    return None


def _ext(body: bytes, kind: str) -> str:
    if kind == "pdf":
        return ".pdf"
    if body.startswith(b"\x89PNG"):
        return ".png"
    if body[:4] == b"RIFF":
        return ".webp"
    return ".jpg"


def candidates(html: str, base_url: str) -> list[str]:
    """Image and PDF URLs of a page worth downloading, in page order, without duplicates."""
    parser = _Links()
    parser.feed(html)
    seen: set[str] = set()
    result = []
    for raw in parser.urls:
        if raw.startswith("data:"):
            continue
        url = urljoin(base_url, raw.strip())
        parts = urlsplit(url)
        path = parts.path.lower()
        key = f"{parts.netloc}{parts.path}"
        if parts.scheme not in ("http", "https") or key in seen:
            continue
        if path.endswith(SKIP_EXT) or SKIP_NAME.search(path.rsplit("/", 1)[-1]):
            continue
        seen.add(key)
        result.append(url)
    return result


def collect(
    url: str,
    dest: Path,
    *,
    max_files: int,
    fetch: Fetch | None = None,
    robots: Robots | None = None,
) -> tuple[list[FlyerFile], list[str]]:
    """Download the flyer files of one page into ``dest``; returns files and warnings."""
    fetch = fetch or http_fetch
    robots = robots or Robots(fetch)
    warnings: list[str] = []
    dest.mkdir(parents=True, exist_ok=True)
    if not robots.allowed(url):
        return [], [f"{url}: disallowed by robots.txt"]
    try:
        body, content_type = fetch(url, max(MAX_PAGE_BYTES, MAX_PDF_BYTES))
    except (OSError, urllib.error.URLError, ValueError) as exc:
        return [], [f"{url}: {exc}"]
    kind = _kind(body, content_type, url)
    if kind in ("image", "pdf"):
        targets = [(url, body, kind)]
    elif kind == "html":
        targets = []
        for link in candidates(body.decode("utf-8", "replace"), url):
            if len(targets) >= max_files:
                break
            if not robots.allowed(link):
                continue
            is_pdf = urlsplit(link).path.lower().endswith(".pdf")
            try:
                data, ctype = fetch(link, MAX_PDF_BYTES if is_pdf else MAX_IMAGE_BYTES)
            except (OSError, urllib.error.URLError, ValueError) as exc:
                warnings.append(f"{link}: {exc}")
                continue
            file_kind = _kind(data, ctype, link)
            if any(data == t[1] for t in targets):
                continue  # the same file under another URL (e.g. og:image and <img>)
            if (file_kind == "image" and len(data) >= MIN_IMAGE_BYTES) or file_kind == "pdf":
                targets.append((link, data, file_kind))
    else:
        return [], [f"{url}: neither a page, an image nor a PDF ({content_type})"]
    files = []
    for number, (link, data, file_kind) in enumerate(targets[:max_files], 1):
        path = dest / f"{number:02d}{_ext(data, file_kind)}"
        path.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        files.append(FlyerFile(path=path, url=link, kind=file_kind, sha256=digest))
    if not files:
        warnings.append(f"{url}: no flyer images or PDFs found")
    return files, warnings


# --- the crawl's flyer calls ----------------------------------------------------------


def load_flyers(config: Config) -> list[crawl.FlyerEntry]:
    """The flyer list; empty if ``sources.flyers_file`` is unset or the file is missing."""
    path = Path(config.sources.flyers_file) if config.sources.flyers_file else None
    if path is None or not path.exists():
        return []
    return crawl._load_list(path, crawl.FlyerEntry)


FLYER_CACHE_VERSION = 1
"""Bump when the flyer extraction changes, so cached files are read again."""
FLYER_CACHE_KEEP_DAYS = 30
"""Cache entries of files not seen on their page for this long are dropped."""


def rekey(item: dict[str, Any], sha: str) -> dict[str, Any]:
    """Make ``ref``/``parent_ref`` unique across files that are combined into one answer."""
    item = {k: v for k, v in item.items() if k != "file"}
    item["ref"] = f"{sha[:10]}-{item.get('ref', '')}"
    if item.get("parent_ref"):
        item["parent_ref"] = f"{sha[:10]}-{item['parent_ref']}"
    return item


def run_flyer_call(
    config: Config,
    runner: Runner,
    batch: crawl.Batch,
    call: crawl.CrawlCall,
    report: crawl.BatchReport,
    events: dict[str, Event],
    *,
    today: date,
    blocked: Sequence[str],
    prompts_dir: Path,
    index: crawl.PlaceIndex,
    fetch: Fetch,
    robots: Robots,
    warnings: list[str],
    step: str,
    cache: FlyerCacheFile,
) -> None:
    """Download the flyers of one page; the AI reads only files not in ``cache``."""
    flyer = call.flyer
    assert flyer is not None
    report.last_usage = Usage()
    items: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="letsgau-flyers-") as tmp:
        files, problems = collect(
            flyer.url, Path(tmp), max_files=flyer.max_files, fetch=fetch, robots=robots
        )
        warnings.extend(f"flyers: {p}" for p in problems)
        if not files:
            return
        fresh = []
        for f in files:
            entry = cache.files.get(f.sha256)
            if entry is not None and entry.version == FLYER_CACHE_VERSION:
                cache.files[f.sha256] = entry.model_copy(update={"last_seen": today})
                items += [rekey(item, f.sha256) for item in entry.events]
            else:
                fresh.append(f)
        crawl._log(
            f"batch {batch.number}: {len(files)} flyer file(s) from {flyer.url}, "
            f"{len(files) - len(fresh)} already read, {len(fresh)} new"
        )
        if fresh:
            names = [f"{FILES_DIR}/{f.path.name}" for f in fresh]
            request = AiRequest(
                step=step,
                model=config.models.events,
                prompt=crawl.render_prompt(
                    config,
                    batch,
                    today=today,
                    blocked=blocked,
                    max_searches=0,
                    prompts_dir=prompts_dir,
                    call=call,
                    files=fresh,
                ),
                schema=crawl.answer_schema(config.tag_ids, files=names),
                blocked_domains=blocked,
                max_searches=0,
                web=False,
                files=[f.path for f in fresh],
            )
            try:
                result = runner.run(request)
            except AiError as exc:
                crawl._skip(report, f"[flyers {flyer.url}] {exc}")
                crawl._log(f"batch {batch.number}: [flyers] {exc}")
                result = None
            if result is not None:
                report.calls += 1
                report.last_usage = result.usage
                report.usage = report.usage.add(result.usage)
                note = str(result.data.get("notes") or "").strip()
                if note:
                    report.notes = "\n".join(filter(None, [report.notes, f"[flyers] {note}"]))
                by_file: dict[str, list[dict[str, Any]]] = {f.sha256: [] for f in fresh}
                for item in result.data.get("events") or []:
                    if not isinstance(item, dict):
                        continue
                    urls = {str(s.get("url", "")) for s in item.get("sources") or []}
                    owner = next(
                        (f for f in fresh if f"{FILES_DIR}/{f.path.name}" == item.get("file")),
                        next((f for f in fresh if f.url in urls), fresh[0]),
                    )
                    by_file[owner.sha256].append({k: v for k, v in item.items() if k != "file"})
                for f in fresh:
                    cache.files[f.sha256] = FlyerCacheEntry(
                        page_url=flyer.url,
                        file_url=f.url,
                        kind="pdf" if f.kind == "pdf" else "image",
                        version=FLYER_CACHE_VERSION,
                        first_seen=today,
                        last_seen=today,
                        events=by_file[f.sha256],
                    )
                    items += [rekey(item, f.sha256) for item in by_file[f.sha256]]
    crawl._add_items(
        items, report, events, config=config, index=index, blocked=blocked, today=today
    )


_log = logger("flyers")
