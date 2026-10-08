"""LLM async wrapper — extract_one + diff_one + transient 재시도 1회.

structured outputs로 schema 준수 강제. provider는 모델 이름으로 결정
(langgraph_tagger.llm_provider).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from langgraph_tagger.analytics.llm_summary.prompts import (
    render_extraction_messages, render_diff_messages,
)
from langgraph_tagger.analytics.llm_summary.schemas import (
    ExtractionResult, DiffResult,
)
from langgraph_tagger.llm_provider import TRANSIENT_ERRORS, StructuredResult

logger = logging.getLogger(__name__)


class TransientLLMError(RuntimeError):
    """429 / 5xx / timeout / connection — 재시도 가능."""


_TRANSIENT_TYPES = (
    *TRANSIENT_ERRORS,
    TransientLLMError, asyncio.TimeoutError,  # wait_for timeout
)


async def _call_with_retry(coro_factory, backoff_s: float):
    try:
        return await coro_factory()
    except _TRANSIENT_TYPES as e:
        logger.warning("LLM transient error, retrying in %ss: %s", backoff_s, e)
        await asyncio.sleep(backoff_s)
        try:
            return await coro_factory()
        except _TRANSIENT_TYPES as e2:
            logger.error("LLM transient retry exhausted: %s", e2)
            raise TransientLLMError(str(e2)) from e2


def _require_parsed(result: StructuredResult):
    if result.parsed is None:
        raise RuntimeError(f"LLM이 응답을 거부했습니다: {result.refusal}")
    return result.parsed


async def _parse(client, model, messages, schema, timeout_s, backoff_s, constrained=True):
    system, user = messages[0]['content'], messages[1]['content']

    async def _call():
        return await asyncio.wait_for(
            client.parse(model=model, system=system, user=user, schema=schema,
                         constrained=constrained),
            timeout=timeout_s,
        )

    result = await _call_with_retry(_call, backoff_s)
    return _require_parsed(result), result.input_tokens, result.output_tokens


async def extract_one(
    *,
    client,                       # langgraph_tagger.llm_provider.LLMClient
    model: str,
    metadata: dict[str, Any],
    pages_text: str,
    timeout_s: int,
    backoff_s: float = 5.0,
) -> tuple[ExtractionResult, int, int]:
    """ExtractionResult + (input_tokens, output_tokens) 반환."""
    messages = render_extraction_messages(metadata, pages_text)
    # ExtractionResult is too large for Claude's grammar compiler — see
    # LLMClient.parse(constrained=False).
    return await _parse(client, model, messages, ExtractionResult, timeout_s, backoff_s,
                        constrained=False)


async def diff_one(
    *,
    client,
    model: str,
    prev_summary: dict[str, Any],
    curr_summary: dict[str, Any],
    prev_match_type: Literal['same_publisher', 'cross_publisher'],
    prev_report_id: int,
    prev_publisher: str,
    curr_publisher: str,
    timeout_s: int,
    backoff_s: float = 5.0,
) -> tuple[DiffResult, int, int]:
    messages = render_diff_messages(
        prev_summary, curr_summary, prev_match_type,
        prev_report_id, prev_publisher, curr_publisher,
    )
    return await _parse(client, model, messages, DiffResult, timeout_s, backoff_s)
