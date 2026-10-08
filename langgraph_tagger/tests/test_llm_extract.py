"""Tests for llm_extract node (mocked LLMClient)."""
from __future__ import annotations

import anthropic
import httpx
import pytest
from openai import APITimeoutError, InternalServerError, RateLimitError

from langgraph_tagger.llm_schemas import LLMExtraction
from langgraph_tagger.nodes.llm_extract import llm_extract, LLMTransientError
from langgraph_tagger.prompts import SYSTEM_PROMPT
from langgraph_tagger.tests.conftest import make_llm_extraction


def _httpx_response(status: int) -> httpx.Response:
    """openai 2.x requires the response to have its request set."""
    return httpx.Response(status, request=httpx.Request("POST", "http://x"))


def _state(model: str = "gpt-5.4-mini") -> dict:
    return {
        "model": model,
        "pdf_text": "샘플 텍스트",
        "file_name": "삼성전자_1Q26.pdf",
        "caption": None,
        "sent_at": __import__("datetime").datetime(2026, 5, 1, 9, 0),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("model,temperature", [
    ("gpt-5.6-luna", None),
    ("gpt-6-luna", None),
    ("gpt-5.4", 0),
    ("claude-haiku-5-5", 0),  # LLMClient drops temperature for Claude
])
async def test_happy_path_returns_parsed(mock_llm_client, model, temperature):
    extraction = make_llm_extraction()
    mock_llm_client.set_response(extraction)

    out = await llm_extract(_state(model), client=mock_llm_client)

    assert out["llm_raw"] == extraction
    assert "llm_refusal" not in out
    kwargs = mock_llm_client.parse.call_args.kwargs
    assert kwargs["model"] == model
    assert kwargs["system"] == SYSTEM_PROMPT
    assert "삼성전자_1Q26.pdf" in kwargs["user"]
    assert kwargs["schema"] is LLMExtraction
    assert kwargs["temperature"] == temperature


@pytest.mark.asyncio
async def test_unreadable_short_circuits(mock_llm_client):
    state = {"pdf_unreadable": True}
    out = await llm_extract(state, client=mock_llm_client)
    assert out["llm_raw"] is None
    # parse should not have been called
    mock_llm_client.parse.assert_not_called()


@pytest.mark.asyncio
async def test_refusal_recorded(mock_llm_client):
    mock_llm_client.set_refusal("policy violation: x")
    out = await llm_extract(_state(), client=mock_llm_client)
    assert out["llm_raw"] is None
    assert out["llm_refusal"] == "policy violation: x"


@pytest.mark.asyncio
@pytest.mark.parametrize("exc_factory", [
    lambda: RateLimitError("429", response=_httpx_response(429), body=None),
    lambda: APITimeoutError(request=httpx.Request("POST", "http://x")),
    lambda: InternalServerError("500", response=_httpx_response(500), body=None),
    lambda: anthropic.RateLimitError("429", response=_httpx_response(429), body=None),
    lambda: anthropic.OverloadedError("529", response=_httpx_response(529), body=None),
    lambda: anthropic.APITimeoutError(request=httpx.Request("POST", "http://x")),
])
async def test_transient_errors_raise_LLMTransientError(mock_llm_client, exc_factory):
    mock_llm_client.set_exception(exc_factory())
    with pytest.raises(LLMTransientError):
        await llm_extract(_state(), client=mock_llm_client)


@pytest.mark.asyncio
async def test_validation_error_wrapped_as_LLMTransientError(mock_llm_client):
    """Pydantic ValidationError on parse() must surface as LLMTransientError
    so the orchestrator records it under transient_errors and reverts the row
    to pending (per spec §9.3)."""
    from pydantic import BaseModel, ValidationError

    class _Bad(BaseModel):
        x: int

    try:
        _Bad(x="not_an_int")
    except ValidationError as e:
        pydantic_err = e

    mock_llm_client.set_exception(pydantic_err)
    with pytest.raises(LLMTransientError):
        await llm_extract(_state(), client=mock_llm_client)
