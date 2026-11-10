"""Tests for the AI runner. No AI is called: CLI output and API messages are replayed."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from anthropic.types.beta import BetaMessage

from letsgau import ai_runner
from letsgau.ai_runner import AiError, AiRequest, ApiRunner, ClaudeCodeRunner
from letsgau.crawl import answer_schema

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "crawl"
TAGS = ["concert", "sports"]


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _request(**kw: Any) -> AiRequest:
    values: dict[str, Any] = {
        "step": "events batch 1/1",
        "model": "claude-sonnet-5-5",
        "prompt": "Find events.",
        "schema": answer_schema(TAGS),
        "blocked_domains": ["example-spam.de"],
        "max_searches": 25,
    }
    values.update(kw)
    return AiRequest(**values)


# --- schema ---------------------------------------------------------------------------


def _objects(schema: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            found.append(schema)
        for value in schema.values():
            found.extend(_objects(value))
    elif isinstance(schema, list):
        for value in schema:
            found.extend(_objects(value))
    return found


def test_answer_schema_is_strict_and_uses_tag_enum() -> None:
    schema = answer_schema(TAGS)
    objects = _objects(schema)
    assert len(objects) > 5
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert obj["required"] == list(obj["properties"])
    event = schema["properties"]["events"]["items"]
    assert event["properties"]["primary_tag"]["enum"] == TAGS
    assert event["properties"]["tags"]["items"]["enum"] == TAGS


# --- subscription: Claude Code CLI ----------------------------------------------------


def test_cli_command_restricts_tools_and_blocks_domains() -> None:
    cmd = ClaudeCodeRunner().command(_request(system="Be thorough."))
    assert cmd[:2] == ["claude", "-p"]
    assert cmd[cmd.index("--model") + 1] == "claude-sonnet-5-5"
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == answer_schema(TAGS)
    assert cmd[cmd.index("--tools") + 1] == "WebSearch,WebFetch"
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert "--no-session-persistence" in cmd
    assert "--strict-mcp-config" in cmd
    assert "--bare" not in cmd  # --bare ignores CLAUDE_CODE_OAUTH_TOKEN
    denied = cmd[cmd.index("--disallowedTools") + 1 :]
    assert denied == ["WebFetch(domain:example-spam.de)", "WebFetch(domain:*.example-spam.de)"]
    assert cmd[cmd.index("--append-system-prompt") + 1] == "Be thorough."


def test_cli_environment_never_contains_api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth")
    env = ClaudeCodeRunner.environment()
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth"


def _fake_run(stdout: str, returncode: int = 0, stderr: str = "") -> Any:
    calls: list[dict[str, Any]] = []

    def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"cmd": cmd, **kwargs})
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)

    run.calls = calls  # type: ignore[attr-defined]
    return run


def test_cli_run_parses_structured_output_and_usage() -> None:
    fixture = _fixture("claude_cli_result.json")
    run = _fake_run(json.dumps(fixture))
    result = ClaudeCodeRunner(run=run).run(_request())

    assert result.data == fixture["structured_output"]
    assert result.usage.requests == 1
    assert result.usage.web_searches == 12
    assert result.usage.web_fetches == 9
    assert result.usage.input_tokens == 2311 + 40211 + 310544
    assert result.usage.output_tokens == 9876
    assert result.usage.cost_usd == 1.2345  # API-equivalent, not billed in subscription mode
    assert result.usage.duration_s == 184.2
    call = run.calls[0]
    assert call["input"] == "Find events."
    assert call["cwd"] != str(Path.cwd())  # outside the repo: no CLAUDE.md
    assert "ANTHROPIC_API_KEY" not in call["env"]


def test_cli_falls_back_to_json_in_result_text() -> None:
    fixture = _fixture("claude_cli_result.json")
    data = fixture.pop("structured_output")
    fixture["result"] = "Here you go:\n```json\n" + json.dumps(data) + "\n```"
    result = ClaudeCodeRunner(run=_fake_run(json.dumps(fixture))).run(_request())
    assert result.data == data


@pytest.mark.parametrize(
    ("stdout", "match"),
    [
        ("not json", "no JSON"),
        (json.dumps({"subtype": "error_max_turns", "is_error": True}), "error_max_turns"),
        (json.dumps({"subtype": "success", "is_error": False, "result": "sorry"}), "no structured"),
    ],
)
def test_cli_errors(stdout: str, match: str) -> None:
    with pytest.raises(AiError, match=match):
        ClaudeCodeRunner(run=_fake_run(stdout, returncode=1)).run(_request())


def test_cli_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai_runner.shutil, "which", lambda _name: None)
    with pytest.raises(AiError, match="not found"):
        ClaudeCodeRunner().run(_request())


# --- api_key: Claude API --------------------------------------------------------------


class _Stream:
    def __init__(self, message: BetaMessage) -> None:
        self.message = message

    def __enter__(self) -> _Stream:
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def get_final_message(self) -> BetaMessage:
        return self.message


class FakeClient:
    """Mimics ``client.beta.messages.stream`` with recorded messages."""

    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.queue = [BetaMessage.model_validate(m) for m in messages]
        self.calls: list[dict[str, Any]] = []
        self.beta = self
        self.messages = self

    def stream(self, **kwargs: Any) -> _Stream:
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return _Stream(self.queue.pop(0))


def test_api_runner_resumes_pause_turn_and_returns_submitted_result() -> None:
    client = FakeClient(_fixture("api_messages.json"))
    result = ApiRunner(client=client).run(_request())

    assert result.data == _fixture("claude_cli_result.json")["structured_output"]
    assert result.fetched_urls == ["https://www.schongau.de/veranstaltungen"]
    assert result.usage.requests == 2
    assert result.usage.web_searches == 6
    assert result.usage.web_fetches == 4
    assert result.usage.input_tokens == 5000 + 12000 + 3000
    assert result.usage.output_tokens == 4800
    # 20000 in * $2 + 4800 out * $10 per MTok + 6 searches * $0.01
    assert result.usage.cost_usd == pytest.approx(0.04 + 0.048 + 0.06)

    first, second = client.calls
    tools = {t["name"]: t for t in first["tools"]}
    assert tools["web_search"]["type"] == "web_search_20260209"
    assert tools["web_search"]["max_uses"] == 25
    assert tools["web_search"]["blocked_domains"] == ["example-spam.de"]
    assert tools["web_fetch"]["type"] == "web_fetch_20260209"
    assert tools["web_fetch"]["blocked_domains"] == ["example-spam.de"]
    assert tools["submit_result"]["strict"] is True
    assert first["model"] == "claude-sonnet-5-5"
    assert first["fallbacks"] == "default"
    # pause_turn: the paused assistant turn is sent back without an extra user message.
    assert len(second["messages"]) == 2
    assert second["messages"][1]["role"] == "assistant"


def test_api_runner_reminds_to_submit_after_plain_answer() -> None:
    plain = {
        "id": "msg_x",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5-5",
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "content": [{"type": "text", "text": "I found some events."}],
        "usage": {"input_tokens": 10, "output_tokens": 10},
    }
    client = FakeClient([plain, _fixture("api_messages.json")[1]])
    result = ApiRunner(client=client).run(_request())
    assert result.data["events"]
    assert client.calls[1]["messages"][-1] == {
        "role": "user",
        "content": "Now call submit_result with your result.",
    }


def test_api_runner_refusal_raises() -> None:
    refusal = {
        "id": "msg_r",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5-5",
        "stop_reason": "refusal",
        "stop_sequence": None,
        "content": [],
        "usage": {"input_tokens": 10, "output_tokens": 0},
    }
    with pytest.raises(AiError, match="refused"):
        ApiRunner(client=FakeClient([refusal])).run(_request())


def test_api_runner_needs_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(AiError, match="ANTHROPIC_API_KEY"):
        ApiRunner().run(_request())


def test_record_dir_saves_raw_answers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LETSGAU_RECORD_DIR", str(tmp_path / "rec"))
    fixture = _fixture("claude_cli_result.json")
    ClaudeCodeRunner(run=_fake_run(json.dumps(fixture))).run(_request())
    ApiRunner(client=FakeClient(_fixture("api_messages.json"))).run(_request())
    files = sorted(p.name for p in (tmp_path / "rec").iterdir())
    assert files == [
        "events-batch-1-1-1.json",
        "events-batch-1-1-2.json",
        "events-batch-1-1-3.json",
    ]
    assert json.loads((tmp_path / "rec" / files[0]).read_text("utf-8")) == fixture


def test_cli_recorded_run_counts_helper_model_searches() -> None:
    """Real answer of `claude -p` (Crawl check, 2026-11-08, Schongau): WebSearch runs on Haiku."""
    fixture = _fixture("claude_cli_recorded_schongau.json")
    result = ClaudeCodeRunner(run=_fake_run(json.dumps(fixture))).run(_request())
    assert result.usage.web_searches == 5  # from modelUsage, top-level usage says 0
    assert result.usage.input_tokens == 14 + 59797 + 18227 + 156354
    assert result.usage.output_tokens == 2394 + 9598
    assert result.usage.duration_s == 57.9
    assert [e["title"]["de"] for e in result.data["events"]] == [
        "Singkreis",
        "Ökumenische Fahrzeugsegnung der neuen RVO-Busse",
    ]


def test_transcript_is_copied_and_searches_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from letsgau import ai_runner

    monkeypatch.setenv("LETSGAU_RECORD_DIR", str(tmp_path / "rec"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    session = tmp_path / "claude" / "projects" / "-tmp-letsgau-x" / "abc-123.jsonl"
    session.parent.mkdir(parents=True)
    lines = [
        {"type": "user", "message": {"content": "prompt"}},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "searching"},
                    {"type": "tool_use", "name": "WebSearch", "input": {"query": "Juze Schongau"}},
                    {"type": "tool_use", "name": "WebFetch", "input": {"url": "https://x.example"}},
                ]
            },
        },
        "not json",
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "WebSearch",
                        "input": {"query": "Schongau Konzert"},
                    }
                ]
            },
        },
    ]
    session.write_text(
        "\n".join(x if isinstance(x, str) else json.dumps(x) for x in lines), encoding="utf-8"
    )
    fixture = {**_fixture("claude_cli_result.json"), "session_id": "abc-123"}
    runner = ClaudeCodeRunner(run=_fake_run(json.dumps(fixture)))
    assert "--no-session-persistence" not in runner.command(_request())  # kept for the copy
    runner.run(_request())
    copied = sorted(p.name for p in (tmp_path / "rec").iterdir())
    assert copied == ["events-batch-1-1-1.json", "events-batch-1-1-2.transcript.jsonl"]
    assert "2 searches: Juze Schongau | Schongau Konzert" in capsys.readouterr().err
    assert ai_runner.search_queries_in("") == []
