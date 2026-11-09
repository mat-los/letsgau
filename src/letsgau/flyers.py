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
"""

from __future__ import annotations

import re
import sys
import urllib.error
import urllib.request
import urllib.robotparser
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

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
            if (file_kind == "image" and len(data) >= MIN_IMAGE_BYTES) or file_kind == "pdf":
                targets.append((link, data, file_kind))
    else:
        return [], [f"{url}: neither a page, an image nor a PDF ({content_type})"]
    files = []
    for number, (link, data, file_kind) in enumerate(targets[:max_files], 1):
        path = dest / f"{number:02d}{_ext(data, file_kind)}"
        path.write_bytes(data)
        files.append(FlyerFile(path=path, url=link, kind=file_kind))
    if not files:
        warnings.append(f"{url}: no flyer images or PDFs found")
    return files, warnings


def _log(message: str) -> None:
    print(f"flyers: {message}", file=sys.stderr, flush=True)
