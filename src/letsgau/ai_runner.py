"""Run AI steps either via Claude Code headless (subscription) or the Claude API (api_key).

* ``ai.auth: subscription``: invokes the ``claude`` CLI in headless mode with
  ``CLAUDE_CODE_OAUTH_TOKEN``; the model is chosen per step with ``--model``.
  The pipeline never calls the Claude API directly with this token.
* ``ai.auth: api_key``: calls the Claude API with the server-side
  ``web_search`` / ``web_fetch`` tools using ``ANTHROPIC_API_KEY``.

Not implemented yet: planned for Phase 2.
"""

from __future__ import annotations

from letsgau.config import Config

PHASE = 2

REQUIRED_ENV = {
    "subscription": "CLAUDE_CODE_OAUTH_TOKEN",
    "api_key": "ANTHROPIC_API_KEY",
}


def required_secret(config: Config) -> str:
    """Name of the environment variable the configured auth mode needs."""
    return REQUIRED_ENV[config.ai.auth]


def run_prompt(config: Config, *, step: str, prompt: str) -> str:
    """Run ``prompt`` for ``step`` (discovery, events, merge) and return raw JSON text."""
    raise NotImplementedError(f"{__name__} is planned for Phase {PHASE}")
