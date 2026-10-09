"""core.llm: provider routing and the transient-retry helper (SDK clients mocked, no network).

Ported from langgraph_tagger/tests/test_llm_provider.py (all of it) and the
generic retry/timeout part of analytics/llm_summary/tests/test_llm.py.
"""
from __future__ import annotations

import asyncio
import json
import sys
import warnings
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, Optional
from unittest.mock import AsyncMock, MagicMock

import anthropic
import httpx
import openai
import pytest
from pydantic import BaseModel, Field, ValidationError

from research_desk.core import llm
from research_desk.core.llm import (
    CodexExecError, LLMClient, StructuredResult, TransientLLMError, api_key_env,
    call_with_retry, is_anthropic, is_codex,
)


# A stand-in with the shapes of the tagger's schema: Korean enum, nested
# model, optional fields with limits, lists with defaults.
class Signals(BaseModel):
    foreign_primary_coverage: bool
    etf_or_fund: bool


class Extraction(BaseModel):
    report_type: Literal["단일종목", "산업", "섹터", "IR자료", "전략·시황", "기타"]
    title: Optional[str] = Field(default=None, max_length=120)
    published_at: Optional[str] = Field(default=None, description="YYYY-MM-DD or null")
    stock_codes_raw: list[str] = Field(default_factory=list)
    publisher_type: Optional[Literal["broker", "data_provider", "ir_agency", "other"]] = None
    signals: Signals
    self_confidence: Literal["high", "medium", "low"]
    notes: Optional[str] = Field(default=None, max_length=200)


def make_extraction(**overrides) -> Extraction:
    values = dict(
        report_type="단일종목", title="삼성전자 1Q26 Preview", published_at="2026-05-01",
        stock_codes_raw=["005930"], publisher_type="broker",
        signals=Signals(foreign_primary_coverage=False, etf_or_fund=False),
        self_confidence="high", notes=None,
    )
    values.update(overrides)
    return Extraction(**values)


def _anthropic_message(*, content, stop_reason="end_turn", stop_details=None,
                       input_tokens=100, cache_write=0, cache_read=0, output_tokens=50):
    return SimpleNamespace(
        content=content, stop_reason=stop_reason, stop_details=stop_details,
        usage=SimpleNamespace(
            input_tokens=input_tokens, output_tokens=output_tokens,
            cache_creation_input_tokens=cache_write, cache_read_input_tokens=cache_read,
        ),
    )


def _client_with_anthropic(message) -> tuple[LLMClient, AsyncMock]:
    client = LLMClient(anthropic_api_key="sk-ant-test", effort="low")
    create = AsyncMock(return_value=message)
    client._anthropic = MagicMock()
    client._anthropic.messages.create = create
    return client, create


def _client_with_openai(*, parsed, refusal=None) -> tuple[LLMClient, AsyncMock]:
    client = LLMClient(openai_api_key="sk-test")
    msg = SimpleNamespace(parsed=parsed, refusal=refusal)
    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=msg)],
        usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=200),
    )
    parse = AsyncMock(return_value=completion)
    client._openai = MagicMock()
    client._openai.chat.completions.parse = parse
    return client, parse


# ── routing ──────────────────────────────────────────────────────────────────

def test_routing_by_model_name():
    assert is_anthropic("claude-haiku-5-5")
    assert not is_anthropic("gpt-6-luna")
    assert is_codex("codex:gpt-6-luna")
    assert not is_codex("gpt-6-luna")
    assert api_key_env("claude-haiku-5-5") == "ANTHROPIC_API_KEY"
    assert api_key_env("gpt-5.4") == "OPENAI_API_KEY"
    assert api_key_env("codex:gpt-6-luna") is None


# ── Anthropic ────────────────────────────────────────────────────────────────

async def test_anthropic_request_shape_and_parse():
    extraction = make_extraction()
    message = _anthropic_message(
        content=[
            SimpleNamespace(type="thinking", thinking="", signature="s"),
            SimpleNamespace(type="text", text=extraction.model_dump_json()),
        ],
        input_tokens=10, cache_write=0, cache_read=3000, output_tokens=80,
    )
    client, create = _client_with_anthropic(message)

    result = await client.parse(model="claude-haiku-5-5", system="SYS", user="USER",
                                schema=Extraction, temperature=0)

    assert result.parsed == extraction
    assert result.refusal is None
    assert result.input_tokens == 3010  # uncached + cache reads
    assert result.output_tokens == 80
    kwargs = create.call_args.kwargs
    assert kwargs["model"] == "claude-haiku-5-5"
    assert kwargs["max_tokens"] == llm.ANTHROPIC_MAX_TOKENS == 16000
    assert kwargs["system"] == [{"type": "text", "text": "SYS",
                                 "cache_control": {"type": "ephemeral"}}]
    assert kwargs["messages"] == [{"role": "user", "content": "USER"}]
    assert kwargs["output_config"]["effort"] == "low"
    fmt = kwargs["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    # enum must survive the schema transform so the API enforces it
    assert fmt["schema"]["properties"]["report_type"]["enum"][0] == "단일종목"
    # Claude models reject non-default sampling params
    assert "temperature" not in kwargs


async def test_anthropic_token_count_includes_cache_writes_and_tolerates_missing_fields():
    text = make_extraction().model_dump_json()
    message = _anthropic_message(content=[SimpleNamespace(type="text", text=text)],
                                 input_tokens=100, cache_write=20, cache_read=30, output_tokens=7)
    client, _ = _client_with_anthropic(message)
    result = await client.parse(model="claude-haiku-5-5", system="S", user="U", schema=Extraction)
    assert (result.input_tokens, result.output_tokens) == (150, 7)

    message = _anthropic_message(content=[SimpleNamespace(type="text", text=text)],
                                 input_tokens=100, cache_write=None, cache_read=None)
    client, _ = _client_with_anthropic(message)
    result = await client.parse(model="claude-haiku-5-5", system="S", user="U", schema=Extraction)
    assert result.input_tokens == 100


async def test_anthropic_refusal_returns_reason():
    message = _anthropic_message(
        content=[], stop_reason="refusal",
        stop_details=SimpleNamespace(category="cyber", explanation="declined"),
    )
    client, _ = _client_with_anthropic(message)
    result = await client.parse(model="claude-haiku-5-5", system="S", user="U",
                                schema=Extraction)
    assert result.parsed is None
    assert result.refusal == "refusal (cyber): declined"


async def test_anthropic_refusal_without_details():
    message = _anthropic_message(content=[], stop_reason="refusal", stop_details=None)
    client, _ = _client_with_anthropic(message)
    result = await client.parse(model="claude-haiku-5-5", system="S", user="U",
                                schema=Extraction)
    assert (result.parsed, result.refusal) == (None, "refusal")


async def test_anthropic_truncated_json_raises_validation_error():
    message = _anthropic_message(
        content=[SimpleNamespace(type="text", text='{"report_type": "산')],
        stop_reason="max_tokens",
    )
    client, _ = _client_with_anthropic(message)
    with pytest.raises(ValidationError):
        await client.parse(model="claude-haiku-5-5", system="S", user="U",
                           schema=Extraction)


async def test_anthropic_unconstrained_puts_schema_in_system_and_parses_json():
    extraction = make_extraction()
    fenced = f"```json\n{extraction.model_dump_json()}\n```"
    message = _anthropic_message(content=[SimpleNamespace(type="text", text=fenced)])
    client, create = _client_with_anthropic(message)

    result = await client.parse(model="claude-haiku-5-5", system="SYS", user="USER",
                                schema=Extraction, constrained=False)

    assert result.parsed == extraction
    kwargs = create.call_args.kwargs
    assert kwargs["output_config"] == {"effort": "low"}  # no grammar format
    system_block = kwargs["system"][0]
    assert system_block["text"].startswith("SYS\n\n<output_format>")
    assert '"report_type"' in system_block["text"]
    assert "단일종목" in system_block["text"]  # schema embedded without ASCII escaping
    assert system_block["cache_control"] == {"type": "ephemeral"}
    assert "tools" not in kwargs


async def test_anthropic_unconstrained_refusal():
    message = _anthropic_message(
        content=[], stop_reason="refusal",
        stop_details=SimpleNamespace(category="general_harms", explanation=None),
    )
    client, _ = _client_with_anthropic(message)
    result = await client.parse(model="claude-haiku-5-5", system="S", user="U",
                                schema=Extraction, constrained=False)
    assert result.parsed is None
    assert result.refusal == "refusal (general_harms):"


# ── OpenAI ───────────────────────────────────────────────────────────────────

async def test_openai_path_passes_temperature_and_returns_usage():
    extraction = make_extraction()
    client, parse = _client_with_openai(parsed=extraction)
    result = await client.parse(model="gpt-5.4", system="S", user="U",
                                schema=Extraction, temperature=0)
    assert result.parsed == extraction
    assert (result.input_tokens, result.output_tokens) == (1000, 200)
    kwargs = parse.call_args.kwargs
    assert kwargs["temperature"] == 0
    assert kwargs["response_format"] is Extraction
    assert kwargs["messages"] == [{"role": "system", "content": "S"},
                                  {"role": "user", "content": "U"}]


async def test_openai_omits_temperature_when_none():
    client, parse = _client_with_openai(parsed=make_extraction())
    await client.parse(model="gpt-6-luna", system="S", user="U", schema=Extraction)
    assert "temperature" not in parse.call_args.kwargs


async def test_openai_refusal():
    client, _ = _client_with_openai(parsed=None, refusal="nope")
    result = await client.parse(model="gpt-5.4", system="S", user="U", schema=Extraction)
    assert result.parsed is None
    assert result.refusal == "nope"


async def test_openai_ignores_constrained_flag():
    client, parse = _client_with_openai(parsed=make_extraction())
    await client.parse(model="gpt-5.4", system="S", user="U", schema=Extraction,
                       constrained=False)
    assert parse.call_args.kwargs["response_format"] is Extraction


# ── keys ─────────────────────────────────────────────────────────────────────

def test_missing_key_for_model_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = LLMClient(openai_api_key="sk-test")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY is required"):
        client.require_key("claude-haiku-5-5")
    assert client.require_key("gpt-5.4") == "sk-test"


def test_require_key_message_and_environment_fallback(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError) as exc:
        LLMClient().require_key("gpt-5.4")
    assert str(exc.value) == "OPENAI_API_KEY is required for model gpt-5.4"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    assert LLMClient().require_key("claude-haiku-5-5") == "sk-ant-env"
    assert LLMClient(anthropic_api_key="sk-ant-arg").require_key("claude-haiku-5-5") == "sk-ant-arg"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY is required for model claude-haiku-5-5"):
        LLMClient().require_key("claude-haiku-5-5")


def test_effort_settings(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_EFFORT", raising=False)
    monkeypatch.delenv("CODEX_REASONING_EFFORT", raising=False)
    client = LLMClient()
    assert (client.effort, client.codex_effort) == ("medium", "high")

    monkeypatch.setenv("ANTHROPIC_EFFORT", "low")
    monkeypatch.setenv("CODEX_REASONING_EFFORT", "medium")
    client = LLMClient()
    assert (client.effort, client.codex_effort) == ("low", "medium")
    assert LLMClient(effort="max").effort == "max"


# ── SDK clients and LangSmith ────────────────────────────────────────────────

@pytest.fixture
def no_tracing(monkeypatch):
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)


async def test_sdk_clients_get_key_retries_and_timeout(no_tracing):
    client = LLMClient(openai_api_key="sk-test", anthropic_api_key="sk-ant-test",
                       timeout=60.0, max_retries=2)
    oa = client._openai_client("gpt-5.4")
    an = client._anthropic_client("claude-haiku-5-5")
    try:
        assert isinstance(oa, openai.AsyncOpenAI) and isinstance(an, anthropic.AsyncAnthropic)
        assert (oa.api_key, oa.max_retries, oa.timeout) == ("sk-test", 2, 60.0)
        assert (an.api_key, an.max_retries, an.timeout) == ("sk-ant-test", 2, 60.0)
        # created once, then reused
        assert client._openai_client("gpt-5.4") is oa
        assert client._anthropic_client("claude-haiku-5-5") is an
    finally:
        await client.close()

    default = LLMClient(openai_api_key="sk-test")  # no timeout given → SDK default
    oa = default._openai_client("gpt-5.4")
    try:
        assert oa.max_retries == 2
        assert oa.timeout == openai.DEFAULT_TIMEOUT
    finally:
        await default.close()


async def test_langsmith_wraps_sdk_clients_only_when_tracing(monkeypatch):
    with warnings.catch_warnings():  # langsmith's own import-time deprecation notice
        warnings.simplefilter("ignore", DeprecationWarning)
        import langsmith.wrappers

    wrapped = []

    def fake_wrap(sdk_client):
        wrapped.append(sdk_client)
        return sdk_client

    monkeypatch.setattr(langsmith.wrappers, "wrap_openai", fake_wrap)
    monkeypatch.setattr(langsmith.wrappers, "wrap_anthropic", fake_wrap)

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-test")
    client = LLMClient(openai_api_key="sk-test", anthropic_api_key="sk-ant-test")
    oa, an = client._openai_client("gpt-5.4"), client._anthropic_client("claude-haiku-5-5")
    await client.close()
    assert wrapped == [oa, an]

    wrapped.clear()
    monkeypatch.delenv("LANGSMITH_API_KEY")  # tracing flag alone is not enough
    client = LLMClient(openai_api_key="sk-test")
    client._openai_client("gpt-5.4")
    await client.close()
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-test")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    client = LLMClient(anthropic_api_key="sk-ant-test")
    client._anthropic_client("claude-haiku-5-5")
    await client.close()
    assert wrapped == []


async def test_close_and_context_manager_close_created_clients():
    client = LLMClient()
    client._openai = MagicMock(close=AsyncMock())
    async with client as same:
        assert same is client
    client._openai.close.assert_awaited_once()

    await LLMClient().close()  # nothing created: nothing to close


# ── Codex CLI ────────────────────────────────────────────────────────────────

_TURN = '{"type":"turn.completed","usage":{"input_tokens":26000,"output_tokens":9000}}'


def _fake_codex(monkeypatch, *, final_text, exit_code=0, stderr="", events=_TURN):
    """Replace the codex subprocess; records the command and writes -o output."""
    calls = {}

    async def fake_run(cmd, prompt, cwd):
        calls.update(cmd=cmd, prompt=prompt, cwd=cwd)
        out = cmd[cmd.index("-o") + 1]
        with open(cmd[cmd.index("--output-schema") + 1], encoding="utf-8") as f:
            calls["schema"] = json.loads(f.read())
        if final_text is not None:
            with open(out, "w", encoding="utf-8") as f:
                f.write(final_text)
        return events, stderr, exit_code

    monkeypatch.setattr(llm, "_run_codex", fake_run)
    monkeypatch.setattr(llm, "_codex_bin", lambda: "codex")
    return calls


async def test_codex_routing_builds_command_and_parses(monkeypatch):
    monkeypatch.setenv("CODEX_REASONING_EFFORT", "high")
    extraction = make_extraction()
    calls = _fake_codex(monkeypatch, final_text=extraction.model_dump_json())
    client = LLMClient()

    result = await client.parse(model="codex:gpt-6-luna", system="SYS", user="USER",
                                schema=Extraction, constrained=False)

    assert result.parsed == extraction
    assert (result.input_tokens, result.output_tokens) == (26000, 9000)
    cmd = calls["cmd"]
    assert cmd[:4] == ["codex", "exec", "-m", "gpt-6-luna"]
    assert "model_reasoning_effort=high" in cmd
    for flag in ("--ignore-user-config", "--ignore-rules", "--ephemeral",
                 "--skip-git-repo-check", "--json"):
        assert flag in cmd
    assert cmd[cmd.index("-s") + 1] == "read-only"
    assert cmd[-1] == "-"  # prompt on stdin
    assert calls["prompt"] == "SYS\n\nUSER"
    # the empty temp dir is the agent's whole workspace
    assert calls["cwd"] == str(Path(cmd[cmd.index("-o") + 1]).parent)
    # strict schema, as the OpenAI API path sends
    assert calls["schema"]["additionalProperties"] is False
    assert set(calls["schema"]["required"]) == set(calls["schema"]["properties"])


async def test_codex_usage_comes_from_the_last_turn(monkeypatch):
    events = "\n".join([
        '{"type":"thread.started"}',
        '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":2}}',
        '{"type":"turn.completed","usage":{"input_tokens":30,"output_tokens":40}}',
    ])
    _fake_codex(monkeypatch, final_text=make_extraction().model_dump_json(), events=events)
    result = await LLMClient().parse(model="codex:gpt-6-luna", system="S", user="U",
                                     schema=Extraction)
    assert (result.input_tokens, result.output_tokens) == (30, 40)


async def test_codex_failure_raises(monkeypatch):
    _fake_codex(monkeypatch, final_text=None, exit_code=1, stderr="usage limit reached")
    with pytest.raises(CodexExecError, match="usage limit reached"):
        await LLMClient().parse(model="codex:gpt-6-luna", system="S", user="U",
                                schema=Extraction)


async def test_codex_without_final_message_raises(monkeypatch):
    _fake_codex(monkeypatch, final_text="   ", exit_code=0)
    with pytest.raises(CodexExecError, match="codex exec exit 0"):
        await LLMClient().parse(model="codex:gpt-6-luna", system="S", user="U",
                                schema=Extraction)


def test_codex_needs_no_api_key(monkeypatch):
    monkeypatch.setattr(llm, "_codex_bin", lambda: "codex")
    assert api_key_env("codex:gpt-6-luna") is None
    assert LLMClient().require_key("codex:gpt-6-luna") == ""
    monkeypatch.setattr(llm, "_codex_bin", lambda: None)
    with pytest.raises(RuntimeError, match="codex CLI not found for model codex:gpt-6-luna"):
        LLMClient().require_key("codex:gpt-6-luna")


def test_codex_bin_prefers_codex_bin_then_path(monkeypatch):
    monkeypatch.setattr(llm.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setenv("CODEX_BIN", r"C:\tools\codex.cmd")
    assert llm._codex_bin() == r"C:\tools\codex.cmd"
    monkeypatch.setenv("CODEX_BIN", "")
    assert llm._codex_bin() == "/usr/bin/codex"
    monkeypatch.delenv("CODEX_BIN")
    assert llm._codex_bin() == "/usr/bin/codex"


async def test_run_codex_pipes_the_prompt_and_returns_output(tmp_path):
    script = ("import sys\n"
              "data = sys.stdin.buffer.read().decode('utf-8')\n"
              "sys.stdout.buffer.write(data.upper().encode('utf-8'))\n"
              "sys.stderr.buffer.write('경고'.encode('utf-8'))\n"
              "sys.exit(3)\n")
    out, err, code = await llm._run_codex([sys.executable, "-c", script], "hello 한국", str(tmp_path))
    assert (out, err, code) == ("HELLO 한국", "경고", 3)


async def test_run_codex_kills_the_process_tree_when_cancelled(monkeypatch, tmp_path):
    killed = []
    real_kill_tree = llm._kill_tree

    def recording_kill_tree(proc):
        killed.append(proc)
        real_kill_tree(proc)

    monkeypatch.setattr(llm, "_kill_tree", recording_kill_tree)
    cmd = [sys.executable, "-c", "import time; time.sleep(60)"]

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(llm._run_codex(cmd, "", str(tmp_path)), timeout=0.5)

    assert len(killed) == 1
    assert killed[0].wait(timeout=20) is not None  # the child is gone, not orphaned


def test_kill_tree_uses_taskkill_on_windows_and_kill_elsewhere(monkeypatch):
    runs = []
    monkeypatch.setattr(llm.subprocess, "run", lambda *a, **k: runs.append((a, k)))
    proc = MagicMock(pid=4321)

    monkeypatch.setattr(llm.sys, "platform", "win32")
    llm._kill_tree(proc)
    assert runs == [((["taskkill", "/F", "/T", "/PID", "4321"],), {"capture_output": True})]
    proc.kill.assert_not_called()

    monkeypatch.setattr(llm.sys, "platform", "linux")
    llm._kill_tree(proc)
    proc.kill.assert_called_once_with()
    assert len(runs) == 1


# ── transient retry helper ───────────────────────────────────────────────────

_REQ = httpx.Request("POST", "https://api.example.test/v1")


def _status(cls, code):
    return cls("boom", response=httpx.Response(code, request=_REQ), body=None)


TRANSIENT_CASES = [
    lambda: TransientLLMError("rate limit"),
    lambda: asyncio.TimeoutError(),
    lambda: _status(openai.RateLimitError, 429),
    lambda: _status(openai.InternalServerError, 500),
    lambda: openai.APIConnectionError(request=_REQ),
    lambda: openai.APITimeoutError(request=_REQ),
    lambda: _status(anthropic.RateLimitError, 429),
    lambda: _status(anthropic.InternalServerError, 500),
    lambda: _status(anthropic.OverloadedError, 529),
    lambda: _status(anthropic.ServiceUnavailableError, 503),
    lambda: _status(anthropic.DeadlineExceededError, 504),
    lambda: anthropic.APIConnectionError(request=_REQ),
    lambda: anthropic.APITimeoutError(request=_REQ),
]


@pytest.fixture
def sleeps(monkeypatch):
    recorded = []

    async def fake_sleep(seconds):
        recorded.append(seconds)

    monkeypatch.setattr(llm.asyncio, "sleep", fake_sleep)
    return recorded


@pytest.mark.parametrize("make_error", TRANSIENT_CASES)
async def test_call_with_retry_retries_a_transient_error_once(make_error, sleeps):
    calls = []

    async def flaky():
        calls.append(len(sleeps))
        if len(calls) == 1:
            raise make_error()
        return "ok"

    assert await call_with_retry(flaky) == "ok"
    assert calls == [0, 1]  # the second try comes after the wait
    assert sleeps == [5.0]  # default backoff: 5 seconds


async def test_call_with_retry_raises_transient_error_when_the_retry_fails(sleeps):
    errors = [TransientLLMError("first"), _status(openai.RateLimitError, 429)]
    calls = []

    async def always_fail():
        calls.append(1)
        raise errors[len(calls) - 1]

    with pytest.raises(TransientLLMError) as exc:
        await call_with_retry(always_fail, backoff_s=0.25)
    assert len(calls) == 2
    assert sleeps == [0.25]
    assert exc.value.__cause__ is errors[1]
    assert str(exc.value) == str(errors[1])


async def test_call_with_retry_retries_a_wait_for_timeout():
    """asyncio.wait_for timeout counts as transient and is retried."""
    calls = []

    async def slow_then_fast():
        calls.append(1)
        if len(calls) == 1:
            await asyncio.sleep(0.05)  # longer than the 0.01s limit
        return StructuredResult(parsed=None, refusal="r", input_tokens=1000, output_tokens=200)

    result = await call_with_retry(lambda: asyncio.wait_for(slow_then_fast(), timeout=0.01),
                                   backoff_s=0)
    assert result.input_tokens == 1000
    assert len(calls) == 2


@pytest.mark.parametrize("error", [ValueError("bad"), CodexExecError("codex exec exit 1: x"),
                                   RuntimeError("LLM이 응답을 거부했습니다")])
async def test_call_with_retry_does_not_retry_other_errors(error, sleeps):
    calls = []

    async def broken():
        calls.append(1)
        raise error

    with pytest.raises(type(error)) as exc:
        await call_with_retry(broken)
    assert exc.value is error
    assert calls == [1]
    assert sleeps == []


async def test_call_with_retry_does_not_retry_validation_errors(sleeps):
    calls = []

    async def invalid():
        calls.append(1)
        Extraction.model_validate_json('{"report_type": "산')

    with pytest.raises(ValidationError):
        await call_with_retry(invalid)
    assert calls == [1]
    assert sleeps == []
