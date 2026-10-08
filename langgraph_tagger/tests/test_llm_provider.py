"""Tests for llm_provider routing (SDK clients mocked, no network)."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from langgraph_tagger.llm_provider import LLMClient, api_key_env, is_anthropic
from langgraph_tagger.llm_schemas import LLMExtraction
from langgraph_tagger.tests.conftest import make_llm_extraction


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


def test_routing_by_model_name():
    assert is_anthropic("claude-haiku-5-5")
    assert not is_anthropic("gpt-6-luna")
    assert api_key_env("claude-haiku-5-5") == "ANTHROPIC_API_KEY"
    assert api_key_env("gpt-5.4") == "OPENAI_API_KEY"


@pytest.mark.asyncio
async def test_anthropic_request_shape_and_parse():
    extraction = make_llm_extraction()
    message = _anthropic_message(
        content=[
            SimpleNamespace(type="thinking", thinking="", signature="s"),
            SimpleNamespace(type="text", text=extraction.model_dump_json()),
        ],
        input_tokens=10, cache_write=0, cache_read=3000, output_tokens=80,
    )
    client, create = _client_with_anthropic(message)

    result = await client.parse(model="claude-haiku-5-5", system="SYS", user="USER",
                                schema=LLMExtraction, temperature=0)

    assert result.parsed == extraction
    assert result.refusal is None
    assert result.input_tokens == 3010  # uncached + cache reads
    assert result.output_tokens == 80
    kwargs = create.call_args.kwargs
    assert kwargs["model"] == "claude-haiku-5-5"
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


@pytest.mark.asyncio
async def test_anthropic_refusal_returns_reason():
    message = _anthropic_message(
        content=[], stop_reason="refusal",
        stop_details=SimpleNamespace(category="cyber", explanation="declined"),
    )
    client, _ = _client_with_anthropic(message)
    result = await client.parse(model="claude-haiku-5-5", system="S", user="U",
                                schema=LLMExtraction)
    assert result.parsed is None
    assert result.refusal == "refusal (cyber): declined"


@pytest.mark.asyncio
async def test_anthropic_truncated_json_raises_validation_error():
    message = _anthropic_message(
        content=[SimpleNamespace(type="text", text='{"report_type": "산')],
        stop_reason="max_tokens",
    )
    client, _ = _client_with_anthropic(message)
    with pytest.raises(ValidationError):
        await client.parse(model="claude-haiku-5-5", system="S", user="U",
                           schema=LLMExtraction)


@pytest.mark.asyncio
async def test_openai_path_passes_temperature_and_returns_usage():
    extraction = make_llm_extraction()
    client, parse = _client_with_openai(parsed=extraction)
    result = await client.parse(model="gpt-5.4", system="S", user="U",
                                schema=LLMExtraction, temperature=0)
    assert result.parsed == extraction
    assert (result.input_tokens, result.output_tokens) == (1000, 200)
    kwargs = parse.call_args.kwargs
    assert kwargs["temperature"] == 0
    assert kwargs["response_format"] is LLMExtraction
    assert kwargs["messages"][0] == {"role": "system", "content": "S"}


@pytest.mark.asyncio
async def test_openai_omits_temperature_when_none():
    client, parse = _client_with_openai(parsed=make_llm_extraction())
    await client.parse(model="gpt-6-luna", system="S", user="U", schema=LLMExtraction)
    assert "temperature" not in parse.call_args.kwargs


@pytest.mark.asyncio
async def test_openai_refusal():
    client, _ = _client_with_openai(parsed=None, refusal="nope")
    result = await client.parse(model="gpt-5.4", system="S", user="U", schema=LLMExtraction)
    assert result.parsed is None
    assert result.refusal == "nope"


def test_missing_key_for_model_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = LLMClient(openai_api_key="sk-test")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY is required"):
        client.require_key("claude-haiku-5-5")
    assert client.require_key("gpt-5.4") == "sk-test"


@pytest.mark.asyncio
async def test_anthropic_unconstrained_puts_schema_in_system_and_parses_json():
    extraction = make_llm_extraction()
    fenced = f"```json\n{extraction.model_dump_json()}\n```"
    message = _anthropic_message(content=[SimpleNamespace(type="text", text=fenced)])
    client, create = _client_with_anthropic(message)

    result = await client.parse(model="claude-haiku-5-5", system="SYS", user="USER",
                                schema=LLMExtraction, constrained=False)

    assert result.parsed == extraction
    kwargs = create.call_args.kwargs
    assert kwargs["output_config"] == {"effort": "low"}  # no grammar format
    system_block = kwargs["system"][0]
    assert system_block["text"].startswith("SYS\n\n<output_format>")
    assert '"report_type"' in system_block["text"]
    assert system_block["cache_control"] == {"type": "ephemeral"}
    assert "tools" not in kwargs


@pytest.mark.asyncio
async def test_anthropic_unconstrained_refusal():
    message = _anthropic_message(
        content=[], stop_reason="refusal",
        stop_details=SimpleNamespace(category="general_harms", explanation=None),
    )
    client, _ = _client_with_anthropic(message)
    result = await client.parse(model="claude-haiku-5-5", system="S", user="U",
                                schema=LLMExtraction, constrained=False)
    assert result.parsed is None
    assert result.refusal == "refusal (general_harms):"


@pytest.mark.asyncio
async def test_openai_ignores_constrained_flag():
    client, parse = _client_with_openai(parsed=make_llm_extraction())
    await client.parse(model="gpt-5.4", system="S", user="U", schema=LLMExtraction,
                       constrained=False)
    assert parse.call_args.kwargs["response_format"] is LLMExtraction


def _fake_codex(monkeypatch, *, final_text, exit_code=0, stderr=""):
    """Replace the codex subprocess; records the command and writes -o output."""
    calls = {}

    async def fake_run(cmd, prompt, cwd):
        calls.update(cmd=cmd, prompt=prompt, cwd=cwd)
        out = cmd[cmd.index("-o") + 1]
        calls["schema"] = json.loads(open(cmd[cmd.index("--output-schema") + 1], encoding="utf-8").read())
        if final_text is not None:
            with open(out, "w", encoding="utf-8") as f:
                f.write(final_text)
        events = '{"type":"turn.completed","usage":{"input_tokens":26000,"output_tokens":9000}}'
        return events, stderr, exit_code

    monkeypatch.setattr("langgraph_tagger.llm_provider._run_codex", fake_run)
    monkeypatch.setattr("langgraph_tagger.llm_provider._codex_bin", lambda: "codex")
    return calls


@pytest.mark.asyncio
async def test_codex_routing_builds_command_and_parses(monkeypatch):
    monkeypatch.setenv("CODEX_REASONING_EFFORT", "high")
    extraction = make_llm_extraction()
    calls = _fake_codex(monkeypatch, final_text=extraction.model_dump_json())
    client = LLMClient()

    result = await client.parse(model="codex:gpt-6-luna", system="SYS", user="USER",
                                schema=LLMExtraction, constrained=False)

    assert result.parsed == extraction
    assert (result.input_tokens, result.output_tokens) == (26000, 9000)
    cmd = calls["cmd"]
    assert cmd[:4] == ["codex", "exec", "-m", "gpt-6-luna"]
    assert "model_reasoning_effort=high" in cmd
    for flag in ("--ignore-user-config", "--ephemeral", "--skip-git-repo-check", "--json"):
        assert flag in cmd
    assert cmd[cmd.index("-s") + 1] == "read-only"
    assert calls["prompt"] == "SYS\n\nUSER"
    # strict schema, as the OpenAI API path sends
    assert calls["schema"]["additionalProperties"] is False
    assert set(calls["schema"]["required"]) == set(calls["schema"]["properties"])


@pytest.mark.asyncio
async def test_codex_failure_raises(monkeypatch):
    from langgraph_tagger.llm_provider import CodexExecError
    _fake_codex(monkeypatch, final_text=None, exit_code=1, stderr="usage limit reached")
    with pytest.raises(CodexExecError, match="usage limit reached"):
        await LLMClient().parse(model="codex:gpt-6-luna", system="S", user="U",
                                schema=LLMExtraction)


def test_codex_needs_no_api_key(monkeypatch):
    monkeypatch.setattr("langgraph_tagger.llm_provider._codex_bin", lambda: "codex")
    assert api_key_env("codex:gpt-6-luna") is None
    assert LLMClient().require_key("codex:gpt-6-luna") == ""
    monkeypatch.setattr("langgraph_tagger.llm_provider._codex_bin", lambda: None)
    with pytest.raises(RuntimeError, match="codex CLI not found"):
        LLMClient().require_key("codex:gpt-6-luna")
