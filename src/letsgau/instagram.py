"""Read public posts of Instagram business and creator accounts (Graph API Business Discovery).

Many cafés, bars and clubs announce events only on Instagram, often only as a poster. The
official route is the Graph API's *Business Discovery*: through the operator's own Instagram
professional account (``INSTAGRAM_USER_ID``, linked to a Facebook page, with a token in
``INSTAGRAM_ACCESS_TOKEN``) it returns the public posts of other **business or creator**
accounts by username: caption, image URLs, time and permalink. Personal and private accounts,
Stories and the text inside videos are not available.

Discovery also proposes *candidate* account names that it saw on directory sites or in search
results but could not confirm; :func:`fetch_profile` and :func:`belongs_to` check them against
the profile (name, biography, website) before they are kept.

This module only talks to the API and parses its answers; the event crawl
(:func:`letsgau.crawl._run_instagram_call`) decides which accounts to read, downloads the
images, lets the AI read new posts and caches the results in ``data/instagram.json``.

The access token is sent as a query parameter and never appears in an exception or log
message: errors are built from the API's error object with the token masked.
"""

from __future__ import annotations

import json
import re
import unicodedata
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

GRAPH_URL = "https://graph.facebook.com"
TOKEN_ENV = "INSTAGRAM_ACCESS_TOKEN"
USER_ENV = "INSTAGRAM_USER_ID"
TIMEOUT_S = 30
MAX_IMAGES_PER_POST = 4
"""Images read per post (carousel posts can have up to 20)."""

USERNAME_RE = re.compile(r"^[a-z0-9._]{1,30}$")
NOT_HANDLES = frozenset(
    {"p", "reel", "reels", "explore", "accounts", "stories", "tv", "direct", "about"}
    | {"developer", "legal", "web", "privacy", "terms", "help", "instagram"}
)
"""Path segments of instagram.com links that are not account names."""
_LINK_RE = re.compile(r"instagram\.com/([A-Za-z0-9._]{1,30})(?![A-Za-z0-9._])", re.IGNORECASE)

Get = Callable[[str], dict[str, Any]]
"""URL -> parsed JSON answer (also for HTTP errors, whose body is the API's error object)."""


class AccountError(Exception):
    """An account could not be read.

    ``kind``: ``not_business`` (personal account, or not a professional account),
    ``not_found``, ``auth`` (token invalid or expired), ``permission`` (the app or token lacks
    a permission; affects every account), ``rate_limit``, ``network`` or ``other``.
    """

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass
class Post:
    id: str
    username: str
    caption: str
    posted: datetime
    permalink: str
    image_urls: list[str] = field(default_factory=list)


def normalize(handle: str) -> str | None:
    """``@Cafe.Bar`` or an instagram.com link -> ``cafe.bar``; None if it is no account name."""
    value = handle.strip().lstrip("@").rstrip("/").lower()
    if value.startswith(("http://", "https://", "www.", "instagram.com")):
        found = handles_in(value)
        return next(iter(found)) if found else None
    if not USERNAME_RE.match(value) or value in NOT_HANDLES or value.endswith("."):
        return None
    return value


def handles_in(text: str) -> set[str]:
    """Account names in ``instagram.com/<name>`` links of a text or page."""
    found = set()
    for match in _LINK_RE.finditer(text):
        name = match.group(1).lower()
        if name not in NOT_HANDLES and not name.endswith(".") and USERNAME_RE.match(name):
            found.add(name)
    return found


def http_get(url: str) -> dict[str, Any]:
    """GET a Graph API URL and return its JSON, also for error answers (HTTP 4xx)."""
    request = urllib.request.Request(url, headers={"User-Agent": "letsgau"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except (ValueError, OSError):
            return {"error": {"message": f"HTTP {exc.code}", "code": exc.code}}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        # the URL contains the token: report only the kind of failure
        raise AccountError("network", f"Graph API not reachable ({type(exc).__name__})") from None


def discovery_fields(username: str, limit: int) -> str:
    media = (
        "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,"
        "children{media_type,media_url,thumbnail_url}"
    )
    return f"business_discovery.username({username}){{username,media.limit({limit}){{{media}}}}}"


def fetch_posts(
    username: str,
    *,
    user_id: str,
    token: str,
    limit: int,
    api_version: str = "",
    get: Get = http_get,
) -> list[Post]:
    """The latest ``limit`` posts of a business or creator account, newest first."""
    path = f"{api_version}/{user_id}" if api_version else user_id
    query = urlencode({"fields": discovery_fields(username, limit), "access_token": token})
    data = get(f"{GRAPH_URL}/{path}?{query}")
    if not isinstance(data, dict):
        raise AccountError("other", "unexpected answer from the Graph API")
    if "error" in data:
        raise _error(data.get("error") or {}, token)
    account = data.get("business_discovery") or {}
    media = (account.get("media") or {}).get("data") or []
    return [p for p in (_post(item, username) for item in media) if p is not None]


@dataclass
class Profile:
    username: str
    name: str = ""
    biography: str = ""
    website: str = ""


def fetch_profile(
    username: str,
    *,
    user_id: str,
    token: str,
    api_version: str = "",
    get: Get = http_get,
) -> Profile:
    """Name, biography and website of a business or creator account."""
    path = f"{api_version}/{user_id}" if api_version else user_id
    fields = f"business_discovery.username({username}){{username,name,biography,website}}"
    data = get(f"{GRAPH_URL}/{path}?{urlencode({'fields': fields, 'access_token': token})}")
    if not isinstance(data, dict):
        raise AccountError("other", "unexpected answer from the Graph API")
    if "error" in data:
        raise _error(data.get("error") or {}, token)
    account = data.get("business_discovery") or {}
    return Profile(
        username=str(account.get("username") or username),
        name=str(account.get("name") or ""),
        biography=str(account.get("biography") or ""),
        website=str(account.get("website") or ""),
    )


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower().replace("ß", "ss"))
    return "".join(c for c in text if not unicodedata.combining(c))


def belongs_to(profile: Profile, places: Iterable[str], website: str = "") -> bool:
    """True if the profile names one of the places, or links the organiser's own website.

    ``places`` are municipality (or district) names; ``website`` is the organiser's site as
    discovery saw it. The check is deliberately strict: a generic name such as ``jeanscafe``
    may belong to a café anywhere, so the profile itself must point to the place.
    """
    text = _fold(" ".join((profile.name, profile.biography, profile.website)))
    user = _fold(profile.username)
    for place in places:
        word = _fold(place).strip()
        if len(word) < 4:
            continue
        pattern = rf"(?<![a-z]){re.escape(word)}(?![a-z])"
        if re.search(pattern, text):
            return True
        # a username counts only if it is more than the place name (cafe_express_schongau,
        # not "schongau": that may be any account about any Schongau)
        if re.search(pattern, user) and re.sub(r"[^a-z]", "", user) != re.sub(r"[^a-z]", "", word):
            return True
    domain = _domain(website)
    return bool(domain) and domain in _fold(profile.website + " " + profile.biography)


def _domain(url: str) -> str:
    host = url.strip().lower()
    host = re.sub(r"^[a-z]+://", "", host).split("/")[0].split("?")[0]
    return host.removeprefix("www.") if "." in host else ""


def _post(item: Any, username: str) -> Post | None:
    if not isinstance(item, dict) or not item.get("id") or not item.get("timestamp"):
        return None
    try:
        posted = datetime.strptime(str(item["timestamp"]), "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None
    return Post(
        id=str(item["id"]),
        username=username,
        caption=str(item.get("caption") or ""),
        posted=posted,
        permalink=str(item.get("permalink") or f"https://www.instagram.com/{username}/"),
        image_urls=images_of(item)[:MAX_IMAGES_PER_POST],
    )


def images_of(item: dict[str, Any]) -> list[str]:
    """Image URLs of a post: the image, a video's cover, or the images of a carousel."""

    def one(media: dict[str, Any]) -> str | None:
        if media.get("media_type") == "VIDEO":
            return media.get("thumbnail_url")
        return media.get("media_url")

    if item.get("media_type") == "CAROUSEL_ALBUM":
        children = (item.get("children") or {}).get("data") or []
        urls: Iterable[str | None] = (one(c) for c in children if isinstance(c, dict))
    else:
        urls = [one(item)]
    return [u for u in urls if u]


def _error(error: dict[str, Any], token: str) -> AccountError:
    message = str(error.get("message") or "unknown error")
    if token:
        message = message.replace(token, "***")
    message = message[:200]
    code = error.get("code")
    subcode = error.get("error_subcode")
    lower = message.lower()
    if code == 190 or "access token" in lower:
        kind = "auth"
    elif code == 10 or (isinstance(code, int) and 200 <= code <= 299):
        kind = "permission"
    elif code in (4, 17, 32, 613) or "limit" in lower:
        kind = "rate_limit"
    elif code == 110 or subcode == 2207013 or "business" in lower or "professional" in lower:
        kind = "not_business"
    elif "cannot find" in lower or "does not exist" in lower or "not found" in lower:
        kind = "not_found"
    else:
        kind = "other"
    return AccountError(kind, f"{message} (code {code})")
