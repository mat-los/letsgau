"""Run AI steps either via Claude Code headless (subscription) or the Claude API (api_key).

* ``ai.auth: subscription``: runs the ``claude`` CLI in print mode with
  ``CLAUDE_CODE_OAUTH_TOKEN``. Only the WebSearch and WebFetch tools are
  available, blacklisted domains are denied for WebFetch, and the answer is
  forced into a JSON schema with ``--json-schema`` (``structured_output``).
  ``ANTHROPIC_API_KEY`` / ``ANTHROPIC_AUTH_TOKEN`` are removed from the CLI's
  environment so the run never bills an API key by accident. The pipeline never
  calls the Claude API directly with the OAuth token.
* ``ai.auth: api_key``: calls the Claude API with the server-side tools
  ``web_search_20260209`` / ``web_fetch_20260209`` (``blocked_domains``,
  ``max_uses``) and a strict client tool ``submit_result`` whose input is the
  structured answer.

Both runners return an :class:`AiResult` with the parsed JSON, the usage and
the URLs fetched (as far as the mode reports them). Validation of the content
happens in the calling stage. With ``LETSGAU_RECORD_DIR`` set, every raw answer
is also saved there (used to record test fixtures).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from letsgau.config import Config
from letsgau.models import Usage

REQUIRED_ENV = {
    "subscription": "CLAUDE_CODE_OAUTH_TOKEN",
    "api_key": "ANTHROPIC_API_KEY",
}

CLAUDE_MAX_TURNS = 80
"""Upper bound of agent turns per CLI call (search, fetch, answer)."""
CLAUDE_TIMEOUT_S = 30 * 60
API_MAX_ROUNDS = 12
"""Upper bound of API round trips per request (pause_turn resumes, reminders)."""
API_MAX_TOKENS = 64000
SUBMIT_TOOL = "submit_result"

# Estimates for the job summary (USD per million tokens), see docs/OPERATIONS.md.
PRICES_PER_MTOK = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}
WEB_SEARCH_USD_PER_1000 = 10.0


def required_secret(config: Config) -> str:
    """Name of the environment variable the configured auth mode needs."""
    return REQUIRED_ENV[config.ai.auth]


class AiError(Exception):
    """An AI call failed or returned no usable structured result."""


@dataclass
class AiRequest:
    """One AI call: prompt, JSON schema of the answer and tool limits."""

    step: str
    model: str
    prompt: str
    schema: dict[str, Any]
    system: str = ""
    blocked_domains: Sequence[str] = ()
    max_searches: int = 20


@dataclass
class AiResult:
    data: dict[str, Any]
    usage: Usage
    fetched_urls: list[str] = field(default_factory=list)
    """URLs fetched during the call; empty if the mode does not report them."""
    raw: Any = None


class Runner(Protocol):
    def run(self, request: AiRequest) -> AiResult: ...


def make_runner(config: Config) -> Runner:
    """Runner for the configured ``ai.auth`` mode."""
    if config.ai.auth == "api_key":
        return ApiRunner()
    return ClaudeCodeRunner()


def estimate_cost(model: str, input_tokens: int, output_tokens: int, searches: int) -> float:
    price_in, price_out = PRICES_PER_MTOK.get(model, (0.0, 0.0))
    return round(
        input_tokens / 1e6 * price_in
        + output_tokens / 1e6 * price_out
        + searches / 1000 * WEB_SEARCH_USD_PER_1000,
        4,
    )


def _log(message: str) -> None:
    print(f"ai: {message}", file=sys.stderr, flush=True)


def _record(step: str, data: Any) -> None:
    """Save a raw answer to ``$LETSGAU_RECORD_DIR`` (no-op if unset)."""
    target = os.environ.get("LETSGAU_RECORD_DIR")
    if not target:
        return
    from pathlib import Path

    directory = Path(target)
    directory.mkdir(parents=True, exist_ok=True)
    name = "".join(c if c.isalnum() else "-" for c in step).strip("-")
    path = directory / f"{name}-{len(list(directory.iterdir())) + 1}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# --- subscription: Claude Code headless ---------------------------------------------

RunFn = Callable[..., subprocess.CompletedProcess[str]]


class ClaudeCodeRunner:
    """Runs ``claude -p`` with only WebSearch/WebFetch and a JSON schema."""

    def __init__(self, binary: str = "claude", run: RunFn = subprocess.run) -> None:
        self.binary = binary
        self._run = run

    def command(self, request: AiRequest) -> list[str]:
        cmd = [
            self.binary,
            "-p",
            "--model",
            request.model,
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(request.schema, separators=(",", ":")),
            "--tools",
            "WebSearch,WebFetch",
            "--allowedTools",
            "WebSearch",
            "WebFetch",
            "--permission-mode",
            "dontAsk",
            "--max-turns",
            str(CLAUDE_MAX_TURNS),
            "--no-session-persistence",
            "--strict-mcp-config",
            "--setting-sources",
            "user",
        ]
        if request.system:
            cmd += ["--append-system-prompt", request.system]
        denied = [
            rule
            for domain in request.blocked_domains
            for rule in (f"WebFetch(domain:{domain})", f"WebFetch(domain:*.{domain})")
        ]
        if denied:
            cmd += ["--disallowedTools", *denied]
        return cmd

    @staticmethod
    def environment() -> dict[str, str]:
        env = dict(os.environ)
        for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            env.pop(name, None)  # never bill an API key in subscription mode
        env.setdefault("DISABLE_AUTOUPDATER", "1")
        return env

    def run(self, request: AiRequest) -> AiResult:
        if self._run is subprocess.run and shutil.which(self.binary) is None:
            raise AiError(
                f"{self.binary!r} not found; install Claude Code "
                "(npm install -g @anthropic-ai/claude-code) or set ai.auth: api_key"
            )
        started = time.monotonic()
        _log(f"{request.step}: claude -p --model {request.model}")
        # Run outside the repository so its CLAUDE.md and settings are not loaded.
        with tempfile.TemporaryDirectory(prefix="letsgau-") as workdir:
            try:
                proc = self._run(
                    self.command(request),
                    input=request.prompt,
                    capture_output=True,
                    text=True,
                    cwd=workdir,
                    env=self.environment(),
                    timeout=CLAUDE_TIMEOUT_S,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise AiError(f"claude timed out after {CLAUDE_TIMEOUT_S}s") from exc
        return self.parse(proc.stdout, proc.stderr, proc.returncode, request, started)

    @staticmethod
    def parse(
        stdout: str, stderr: str, returncode: int, request: AiRequest, started: float
    ) -> AiResult:
        try:
            result = json.loads(stdout)
        except json.JSONDecodeError as exc:
            detail = (stderr or stdout).strip()[-500:]
            raise AiError(f"claude returned no JSON (exit {returncode}): {detail}") from exc
        _record(request.step, result)
        if result.get("is_error") or result.get("subtype") != "success":
            raise AiError(
                f"claude failed: subtype={result.get('subtype')!r}, "
                f"result={str(result.get('result'))[:300]!r}"
            )
        data = result.get("structured_output")
        if data is None:
            data = _json_from_text(result.get("result") or "")
        if not isinstance(data, dict):
            raise AiError("claude returned no structured output")
        usage_raw = result.get("usage") or {}
        server = usage_raw.get("server_tool_use") or {}
        input_tokens = (
            int(usage_raw.get("input_tokens") or 0)
            + int(usage_raw.get("cache_read_input_tokens") or 0)
            + int(usage_raw.get("cache_creation_input_tokens") or 0)
        )
        usage = Usage(
            requests=1,
            web_searches=int(server.get("web_search_requests") or 0),
            web_fetches=int(server.get("web_fetch_requests") or 0),
            input_tokens=input_tokens,
            output_tokens=int(usage_raw.get("output_tokens") or 0),
            cost_usd=0.0,  # subscription: no per-token billing (total_cost_usd is notional)
            duration_s=round(
                (result.get("duration_ms") or (time.monotonic() - started) * 1000) / 1000, 1
            ),
        )
        _log(
            f"{request.step}: {result.get('num_turns')} turns, {usage.duration_s}s, "
            f"notional cost ${result.get('total_cost_usd') or 0:.2f}"
        )
        return AiResult(data=data, usage=usage, raw=result)


def _json_from_text(text: str) -> Any:
    """Last resort: the first JSON object in a text answer (```json fences allowed)."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


# --- api_key: Claude API with server-side web tools ---------------------------------


class ApiRunner:
    """Claude API with web_search/web_fetch and a strict ``submit_result`` tool."""

    def __init__(self, client: Any = None) -> None:
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise AiError("ANTHROPIC_API_KEY is not set (ai.auth: api_key)")
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    @staticmethod
    def tools(request: AiRequest) -> list[dict[str, Any]]:
        blocked: dict[str, Any] = {}
        if request.blocked_domains:
            blocked["blocked_domains"] = list(request.blocked_domains)
        return [
            {
                "type": "web_search_20260209",
                "name": "web_search",
                "max_uses": max(1, request.max_searches),
                **blocked,
            },
            {"type": "web_fetch_20260209", "name": "web_fetch", **blocked},
            {
                "name": SUBMIT_TOOL,
                "description": "Submit the final result. Call exactly once, at the end.",
                "strict": True,
                "input_schema": request.schema,
            },
        ]

    def run(self, request: AiRequest) -> AiResult:
        started = time.monotonic()
        messages: list[dict[str, Any]] = [{"role": "user", "content": request.prompt}]
        usage = Usage()
        fetched: list[str] = []
        _log(f"{request.step}: Claude API --model {request.model}")
        for _ in range(API_MAX_ROUNDS):
            with self.client.beta.messages.stream(
                model=request.model,
                max_tokens=API_MAX_TOKENS,
                system=request.system or None,
                tools=self.tools(request),
                messages=messages,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                message = stream.get_final_message()
            _record(request.step, message.model_dump(mode="json"))
            usage = usage.add(_api_usage(message, request.model))
            for block in message.content:
                if block.type == "server_tool_use" and block.name == "web_fetch":
                    url = (block.input or {}).get("url")
                    if url:
                        fetched.append(url)
                if block.type == "tool_use" and block.name == SUBMIT_TOOL:
                    usage = usage.model_copy(
                        update={"duration_s": round(time.monotonic() - started, 1)}
                    )
                    return AiResult(data=dict(block.input), usage=usage, fetched_urls=fetched)
            if message.stop_reason == "refusal":
                raise AiError(f"{request.step}: the model refused ({message.stop_details})")
            if message.stop_reason == "max_tokens":
                raise AiError(f"{request.step}: answer exceeded {API_MAX_TOKENS} tokens")
            messages.append({"role": "assistant", "content": message.content})
            if message.stop_reason != "pause_turn":
                messages.append(
                    {"role": "user", "content": f"Now call {SUBMIT_TOOL} with your result."}
                )
        raise AiError(f"{request.step}: no {SUBMIT_TOOL} call after {API_MAX_ROUNDS} rounds")


def _api_usage(message: Any, model: str) -> Usage:
    u = message.usage
    server = getattr(u, "server_tool_use", None)
    searches = int(getattr(server, "web_search_requests", 0) or 0)
    fetches = int(getattr(server, "web_fetch_requests", 0) or 0)
    input_tokens = (
        int(u.input_tokens or 0)
        + int(getattr(u, "cache_read_input_tokens", 0) or 0)
        + int(getattr(u, "cache_creation_input_tokens", 0) or 0)
    )
    output_tokens = int(u.output_tokens or 0)
    return Usage(
        requests=1,
        web_searches=searches,
        web_fetches=fetches,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=estimate_cost(model, input_tokens, output_tokens, searches),
    )
