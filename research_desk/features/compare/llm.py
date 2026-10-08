"""The compare AI call: ``diff_one`` → DiffResult + token counts.

The reply is constrained to the DiffResult schema (structured output). Each try has its own
``timeout_s`` limit; a transient error (429 / 5xx / connection / timeout) is retried once after
``backoff_s`` by ``core.llm.call_with_retry`` (then TransientLLMError); other errors and refusals
fail at once (spec §9.3). The provider is picked by the model name (``core.llm.LLMClient``); the
SDK keeps its default retries. Moved unchanged from the old analytics/llm_summary/llm.py.
"""
from __future__ import annotations

import asyncio
from typing import Any, Literal

from research_desk.core.llm import StructuredResult, call_with_retry

from .prompts import render_diff_messages
from .schemas import DiffResult


def _require_parsed(result: StructuredResult):
    if result.parsed is None:
        raise RuntimeError(f"LLM이 응답을 거부했습니다: {result.refusal}")
    return result.parsed


async def diff_one(
    *,
    client,                       # research_desk.core.llm.LLMClient
    model: str,
    prev_summary: dict[str, Any],
    curr_summary: dict[str, Any],
    prev_match_type: Literal['same_publisher', 'cross_publisher'],
    prev_report_id: int,
    prev_publisher: str,
    curr_publisher: str,
    timeout_s: float,
    backoff_s: float = 5.0,
) -> tuple[DiffResult, int, int]:
    """DiffResult + (input_tokens, output_tokens) 반환."""
    messages = render_diff_messages(
        prev_summary, curr_summary, prev_match_type,
        prev_report_id, prev_publisher, curr_publisher,
    )
    system, user = messages[0]['content'], messages[1]['content']

    async def call():
        return await asyncio.wait_for(
            client.parse(model=model, system=system, user=user, schema=DiffResult,
                         constrained=True),
            timeout=timeout_s,
        )

    result = await call_with_retry(call, backoff_s)
    return _require_parsed(result), result.input_tokens, result.output_tokens
