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

The first part talks to the API and parses its answers. The second part checks candidate
names (from discovery and ``sources.instagram_candidates_file``) against their profile
(:func:`verify_candidates`). The third part is the event crawl's Instagram step
(:func:`run_instagram_call`): it decides which accounts to read, downloads the images,
lets the AI read new posts and caches the results in ``data/instagram.json``.

The access token is sent as a query parameter and never appears in an exception or log
message: errors are built from the API's error object with the token masked.
"""

from __future__ import annotations

import json
import re
import tempfile
import unicodedata
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from letsgau import crawl, flyers  # circular on purpose: used only inside functions
from letsgau.ai_runner import FILES_DIR, AiError, AiRequest, Runner
from letsgau.config import Config
from letsgau.models import (
    Event,
    InstagramAccount,
    InstagramFile,
    InstagramPost,
    RejectedCandidate,
    Usage,
)
from letsgau.util import prompt_env

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


# --- candidate accounts (discovery and the candidates file) ----------------------------


MAX_CANDIDATES_PER_RUN = 40
"""Candidate Instagram names checked against their profile per discovery run."""
CANDIDATE_RETRY_DAYS = 180


@dataclass
class Candidate:
    username: str
    name: str
    places: list[str]
    website: str


def collect_candidates(
    data: Mapping[str, Any],
    *,
    batch_places: Sequence[str],
    resolve: Any,
    key: str = "instagram_candidates",
) -> list[Candidate]:
    """The account names of one AI answer under ``key`` (not checked yet)."""
    found = []
    for item in data.get(key) or []:
        if not isinstance(item, Mapping):
            continue
        handle = normalize(str(item.get("username", "")))
        if not handle:
            continue
        places = [p for p in (resolve(n) for n in item.get("places") or []) if p]
        found.append(
            Candidate(
                username=handle,
                name=str(item.get("name") or ""),
                places=sorted(set(places)) or list(batch_places),
                website=str(item.get("website") or ""),
            )
        )
    return found


class ListedCandidate(BaseModel):
    """An entry of ``sources.instagram_candidates_file`` (written by the lookup session)."""

    model_config = ConfigDict(extra="ignore")
    username: str
    name: str = ""
    places: list[str] = []
    website: str = ""
    found_on: str = ""
    """Where the name was seen (search result or directory page); for the reviewer."""


def listed_candidates(
    config: Config, *, resolve: Any, default_places: Sequence[str] = ()
) -> list[Candidate]:
    """The entries of ``sources.instagram_candidates_file`` as candidates (not checked yet)."""
    path = Path(config.sources.instagram_candidates_file or "")
    if not config.sources.instagram_candidates_file or not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    items = []
    for entry in raw if isinstance(raw, list) else []:
        try:
            items.append(ListedCandidate.model_validate(entry).model_dump())
        except ValidationError:
            continue
    return collect_candidates(
        {"instagram_candidates": items}, batch_places=default_places, resolve=resolve
    )


def verify_candidates(
    cache: InstagramFile,
    candidates: Sequence[Candidate],
    *,
    names_of: Callable[[str], list[str]],
    today: date,
    env: Mapping[str, str],
    get: Get,
    api_version: str = "",
    warnings: list[str],
) -> tuple[list[str], list[str], list[str]]:
    """Keep the candidates whose profile points to their place; returns the accepted, the
    rejected and the kept names (known accounts that passed the check, with the evidence).

    A candidate is accepted if it is a business or creator account whose name, biography or
    website names one of its places (``names_of`` gives a municipality's names, its districts
    included) or links the organiser's own website. Others go to ``cache.rejected`` and are
    not checked again for :data:`CANDIDATE_RETRY_DAYS`.
    """
    token, user_id = env.get(TOKEN_ENV, ""), env.get(USER_ENV, "")
    retry_after = today - timedelta(days=CANDIDATE_RETRY_DAYS)
    todo: dict[str, Candidate] = {}
    # mined accounts that were never read yet are checked too (e.g. found before this check
    # existed); a rejected one is removed from the accounts
    unread = {
        u: Candidate(username=u, name="", places=list(a.places), website="")
        for u, a in cache.accounts.items()
        if a.origin == "mined" and a.status == "new" and a.last_checked is None
    }
    todo.update(unread)
    for c in candidates:
        if c.username in unread:
            todo[c.username] = c  # the answer's website and name help the check
            continue
        old = cache.rejected.get(c.username)
        if c.username in cache.accounts or c.username in todo:
            continue
        if old is not None and old.checked > retry_after:
            continue
        todo[c.username] = c
    if not todo:
        return [], [], []
    if not token or not user_id:
        if not candidates:
            return [], [], []  # only unread accounts: nothing new to report
        warnings.append(
            f"instagram: {len(todo)} candidate account(s) not checked: "
            f"{TOKEN_ENV} or {USER_ENV} not set"
        )
        return [], [], []
    accepted: list[str] = []
    rejected: list[str] = []
    kept: list[str] = []
    for checked, c in enumerate(todo.values()):
        if checked >= MAX_CANDIDATES_PER_RUN:
            warnings.append("instagram: candidate limit reached; the rest is checked next run")
            break
        try:
            profile = fetch_profile(
                c.username, user_id=user_id, token=token, api_version=api_version, get=get
            )
        except AccountError as exc:
            if exc.kind in ("auth", "permission", "rate_limit", "network"):
                warnings.append(f"instagram: candidates not checked further: {exc}")
                break
            cache.rejected[c.username] = RejectedCandidate(
                checked=today, reason=exc.kind, name=c.name
            )
            cache.accounts.pop(c.username, None)
            rejected.append(c.username)
            continue
        names = [n for place in c.places for n in names_of(place)]
        if belongs_to(profile, names, c.website):
            if c.username not in cache.accounts:
                cache.accounts[c.username] = InstagramAccount(
                    origin="mined", places=c.places, first_seen=today
                )
                accepted.append(c.username)
            else:
                evidence = profile.name or profile.biography or profile.website
                kept.append(f"{c.username} ({evidence[:40]})")
            cache.rejected.pop(c.username, None)
        else:
            cache.rejected[c.username] = RejectedCandidate(
                checked=today, reason="profile does not name the place", name=c.name
            )
            cache.accounts.pop(c.username, None)
            rejected.append(c.username)
    return accepted, rejected, kept


# --- the crawl's Instagram step -----------------------------------------------------------


def load_instagram(config: Config) -> list[crawl.InstagramEntry]:
    """The Instagram list; empty if ``sources.instagram_file`` is unset or missing."""
    path = Path(config.sources.instagram_file) if config.sources.instagram_file else None
    if path is None or not path.exists():
        return []
    return crawl._load_list(path, crawl.InstagramEntry)


INSTAGRAM_VERSION = 1
"""Bump when the Instagram extraction changes, so cached posts are read again."""
INSTAGRAM_POSTS_PER_CALL = 6
"""New posts read in one AI call (caption plus up to 4 images each)."""
INSTAGRAM_RETRY_DAYS = 30
"""Accounts that were not readable (personal account, not found) are tried again after this."""


def check_listed_candidates(
    config: Config,
    cache: InstagramFile,
    *,
    index: crawl.PlaceIndex,
    municipality_of: Mapping[str, str],
    today: date,
    env: Mapping[str, str],
    get: Get,
    warnings: list[str],
) -> None:
    """Check the entries of ``sources.instagram_candidates_file`` against their profile, so
    that a merged lookup pull request takes effect in the next events run."""
    names_in: dict[str, list[str]] = {}
    for place, municipality in municipality_of.items():
        names_in.setdefault(municipality, []).append(place)

    def resolve(name: str) -> str | None:
        place = index.resolve(name)
        return municipality_of.get(place, place) if place else None

    found = listed_candidates(config, resolve=resolve, default_places=sorted(names_in))
    if not found:
        return
    accepted, rejected, _ = verify_candidates(
        cache,
        found,
        names_of=lambda m: names_in.get(m, [m]),
        today=today,
        env=env,
        get=get,
        api_version=config.instagram.api_version,
        warnings=warnings,
    )
    if accepted or rejected:
        crawl._log(
            f"instagram candidates: confirmed {', '.join(accepted) or 'none'}; "
            f"rejected {', '.join(rejected) or 'none'}"
        )


def mine_instagram(
    data: dict[str, Any], batch: crawl.Batch, cache: InstagramFile, today: date
) -> list[str]:
    """Add the account names an AI call found on the pages it read; returns the new ones."""
    added = []
    for raw in data.get("instagram_accounts") or []:
        handle = normalize(str(raw))
        if handle and handle not in cache.accounts:
            cache.accounts[handle] = InstagramAccount(
                origin="mined", places=[g.name for g in batch.groups], first_seen=today
            )
            added.append(handle)
    return added


def prune_instagram(cache: InstagramFile, today: date, config: Config) -> InstagramFile:
    keep_after = today - timedelta(days=config.instagram.max_post_age_days + 30)
    return cache.model_copy(
        update={
            "updated_at": datetime.now(UTC).replace(microsecond=0),
            "posts": {k: v for k, v in cache.posts.items() if v.posted >= keep_after},
        }
    )


SCREEN_MIN_CAPTION = 40
"""Posts with a shorter caption skip the pre-screen: the picture probably carries the text."""


def screen_schema() -> dict[str, Any]:
    item = {
        "type": "object",
        "properties": {"post": {"type": "integer"}, "may_be_event": {"type": "boolean"}},
        "required": ["post", "may_be_event"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"posts": {"type": "array", "items": item}},
        "required": ["posts"],
        "additionalProperties": False,
    }


def prescreen_posts(
    config: Config,
    runner: Runner,
    posts: list[Post],
    cache: InstagramFile,
    *,
    today: date,
    report: crawl.BatchReport,
) -> list[Post]:
    """Drop posts whose caption is clearly not about an upcoming event, before their images
    are read (``instagram.prescreen``; one text-only call to the merge model).

    Dropped posts are cached without events, so they are not read again. Posts with a short
    caption, and all posts if the call fails, go on to the image reading.
    """
    candidates = [p for p in posts if len(p.caption.strip()) >= SCREEN_MIN_CAPTION]
    if not candidates:
        return posts
    env = prompt_env(crawl.PROMPTS_DIR)
    prompt = env.get_template("instagram_screen.md").render(
        today=today,
        posts=[
            {"n": n, "username": p.username, "posted": p.posted.date(), "caption": p.caption[:1500]}
            for n, p in enumerate(candidates, 1)
        ],
    )
    request = AiRequest(
        step="instagram pre-screen",
        model=config.models.merge,
        prompt=prompt,
        schema=screen_schema(),
        web=False,
        max_searches=0,
    )
    try:
        result = runner.run(request)
    except AiError as exc:
        crawl._log(f"instagram pre-screen failed, all posts are read: {exc}")
        return posts
    report.usage = report.usage.add(result.usage)
    report.last_usage = report.last_usage.add(result.usage)
    no_event = {
        int(x["post"])
        for x in result.data.get("posts") or []
        if isinstance(x, dict)
        and x.get("may_be_event") is False
        and str(x.get("post", "")).isdigit()
    }
    dropped = {candidates[n - 1].id for n in no_event if 1 <= n <= len(candidates)}
    for post in posts:
        if post.id in dropped:
            cache.posts[post.id] = InstagramPost(
                username=post.username,
                permalink=post.permalink,
                posted=post.posted.date(),
                version=INSTAGRAM_VERSION,
                first_seen=today,
                last_seen=today,
                events=[],
            )
    if dropped:
        crawl._log(f"instagram pre-screen: {len(dropped)} of {len(posts)} post(s) are no event")
    return [p for p in posts if p.id not in dropped]


def run_instagram_call(
    config: Config,
    runner: Runner,
    batch: crawl.Batch,
    report: crawl.BatchReport,
    events: dict[str, Event],
    *,
    today: date,
    blocked: Sequence[str],
    prompts_dir: Path,
    index: crawl.PlaceIndex,
    fetch: flyers.Fetch,
    warnings: list[str],
    step: str,
    cache: InstagramFile,
    entries: Sequence[crawl.InstagramEntry],
    state: dict[str, Any],
    env: Mapping[str, str],
    get: Get,
) -> None:
    """Read the recent posts of the batch's Instagram accounts; only new posts go to the AI."""
    report.last_usage = Usage()
    settings = config.instagram
    names = {p.name.casefold() for p in batch.places} | {g.name.casefold() for g in batch.groups}

    def here(places: Sequence[str]) -> bool:
        return bool({p.casefold() for p in places} & names) or (not places and batch.number == 1)

    listed = [e for e in entries if here(e.places)]
    for entry in listed:
        old = cache.accounts.get(entry.username)
        cache.accounts[entry.username] = (
            old.model_copy(update={"origin": "whitelist", "places": entry.places})
            if old
            else InstagramAccount(origin="whitelist", places=entry.places, first_seen=today)
        )
    accounts = [e.username for e in listed]
    if settings.read_mined:
        accounts += sorted(
            (
                u
                for u, a in cache.accounts.items()
                if a.origin == "mined" and a.places and here(a.places)
            ),
            # least recently read first, so that all are read when there are more than the limit
            key=lambda u: (cache.accounts[u].last_checked or date.min, u),
        )
    if not accounts:
        return
    token, user_id = env.get(TOKEN_ENV, ""), env.get(USER_ENV, "")
    if not token or not user_id:
        if not state["warned"]:
            warnings.append(
                f"instagram: {TOKEN_ENV} or {USER_ENV} not set; Instagram accounts are not read"
            )
            state["warned"] = True
        return

    cutoff = today - timedelta(days=settings.max_post_age_days)
    items: list[dict[str, Any]] = []
    fresh: list[Post] = []
    read = 0
    for username in accounts:
        if state["stop"]:
            break
        if state["read"] >= settings.max_accounts_per_run:
            crawl._log(f"batch {batch.number}: instagram: max_accounts_per_run reached")
            break
        account = cache.accounts[username]
        if (
            account.status in ("not_business", "not_found")
            and account.last_checked is not None
            and (today - account.last_checked).days < INSTAGRAM_RETRY_DAYS
        ):
            continue
        state["read"] += 1
        read += 1
        try:
            posts = fetch_posts(
                username,
                user_id=user_id,
                token=token,
                limit=settings.posts_per_account,
                api_version=settings.api_version,
                get=get,
            )
        except AccountError as exc:
            status = exc.kind if exc.kind in ("not_business", "not_found") else "error"
            cache.accounts[username] = account.model_copy(
                update={"status": status, "last_checked": today, "last_error": str(exc)}
            )
            crawl._log(f"batch {batch.number}: instagram @{username}: {exc.kind}: {exc}")
            if exc.kind in ("auth", "permission", "rate_limit", "network"):
                state["stop"] = True
                warnings.append(f"instagram: {exc}; no more accounts are read in this run")
            elif account.origin == "whitelist":
                warnings.append(f"instagram: @{username} cannot be read ({exc.kind}): {exc}")
            continue
        cache.accounts[username] = account.model_copy(
            update={"status": "active", "last_checked": today, "last_error": ""}
        )
        for post in posts:
            if post.posted.date() < cutoff:
                continue
            cached = cache.posts.get(post.id)
            if cached is not None and cached.version == INSTAGRAM_VERSION:
                cache.posts[post.id] = cached.model_copy(update={"last_seen": today})
                items += [flyers.rekey(item, f"ig{post.id}") for item in cached.events]
            else:
                fresh.append(post)
    crawl._log(f"batch {batch.number}: instagram: {read} account(s) read, {len(fresh)} new post(s)")
    if fresh and settings.prescreen:
        fresh = prescreen_posts(config, runner, fresh, cache, today=today, report=report)

    for start in range(0, len(fresh), INSTAGRAM_POSTS_PER_CALL):
        chunk = fresh[start : start + INSTAGRAM_POSTS_PER_CALL]
        number = start // INSTAGRAM_POSTS_PER_CALL + 1
        with tempfile.TemporaryDirectory(prefix="letsgau-instagram-") as tmp:
            infos: list[dict[str, Any]] = []
            paths: list[Path] = []
            for n, post in enumerate(chunk, 1):
                files = []
                for k, url in enumerate(post.image_urls, 1):
                    try:
                        body, content_type = fetch(url, flyers.MAX_IMAGE_BYTES)
                    except OSError:
                        continue
                    if flyers._kind(body, content_type, url) != "image":
                        continue
                    path = Path(tmp) / f"post{n}-{k}{flyers._ext(body, 'image')}"
                    path.write_bytes(body)
                    paths.append(path)
                    files.append(f"{FILES_DIR}/{path.name}")
                infos.append(
                    {
                        "ref": f"post {n}",
                        "username": post.username,
                        "posted": post.posted.date().isoformat(),
                        "permalink": post.permalink,
                        "caption": post.caption.strip()[:2200],
                        "files": files,
                    }
                )
            result = None
            if any(i["caption"] or i["files"] for i in infos):
                request = AiRequest(
                    step=step + (f" {number}" if len(fresh) > INSTAGRAM_POSTS_PER_CALL else ""),
                    model=config.models.events,
                    prompt=crawl.render_prompt(
                        config,
                        batch,
                        today=today,
                        blocked=blocked,
                        max_searches=0,
                        prompts_dir=prompts_dir,
                        call=crawl.CrawlCall("instagram"),
                        posts=infos,
                    ),
                    schema=crawl.answer_schema(config.tag_ids, files=[i["ref"] for i in infos]),
                    blocked_domains=blocked,
                    max_searches=0,
                    web=False,
                    files=paths,
                )
                try:
                    result = runner.run(request)
                except AiError as exc:
                    crawl._skip(report, f"[instagram] {exc}")
                    crawl._log(f"batch {batch.number}: [instagram] {exc}")
                    continue  # not cached: read again next run
            by_post: dict[str, list[dict[str, Any]]] = {p.id: [] for p in chunk}
            if result is not None:
                report.calls += 1
                report.last_usage = report.last_usage.add(result.usage)
                report.usage = report.usage.add(result.usage)
                note = str(result.data.get("notes") or "").strip()
                if note:
                    report.notes = "\n".join(filter(None, [report.notes, f"[instagram] {note}"]))
                for item in result.data.get("events") or []:
                    if not isinstance(item, dict):
                        continue
                    urls = {str(s.get("url", "")) for s in item.get("sources") or []}
                    owner = next(
                        (
                            p
                            for p, i in zip(chunk, infos, strict=True)
                            if i["ref"] == item.get("file")
                        ),
                        next((p for p in chunk if p.permalink in urls), chunk[0]),
                    )
                    by_post[owner.id].append({k: v for k, v in item.items() if k != "file"})
            for post in chunk:
                cache.posts[post.id] = InstagramPost(
                    username=post.username,
                    permalink=post.permalink,
                    posted=post.posted.date(),
                    version=INSTAGRAM_VERSION,
                    first_seen=today,
                    last_seen=today,
                    events=by_post[post.id],
                )
                items += [flyers.rekey(item, f"ig{post.id}") for item in by_post[post.id]]
    crawl._add_items(
        items, report, events, config=config, index=index, blocked=blocked, today=today
    )
