"""The analysis AI call: ``extract_one`` → ExtractionResult + token counts.

Each try has its own ``timeout_s`` limit; a transient error (429 / 5xx /
connection / timeout) is retried once after ``backoff_s`` by
``core.llm.call_with_retry`` (then TransientLLMError). The provider is picked by
the model name (``core.llm.LLMClient``). The SDK keeps its default retries.
"""
from __future__ import annotations

import asyncio
from typing import Any

from research_desk.core.llm import StructuredResult, call_with_retry

from .prompts import render_extraction_messages
from .schemas import ExtractionResult


def _require_parsed(result: StructuredResult):
    if result.parsed is None:
        raise RuntimeError(f"LLM이 응답을 거부했습니다: {result.refusal}")
    return result.parsed


async def extract_one(
    *,
    client,                       # research_desk.core.llm.LLMClient
    model: str,
    metadata: dict[str, Any],
    pages_text: str,
    timeout_s: float,
    backoff_s: float = 5.0,
) -> tuple[ExtractionResult, int, int]:
    """ExtractionResult + (input_tokens, output_tokens) 반환."""
    messages = render_extraction_messages(metadata, pages_text)
    system, user = messages[0]['content'], messages[1]['content']

    async def call():
        # ExtractionResult is too large for Claude's grammar compiler — see
        # LLMClient.parse(constrained=False).
        return await asyncio.wait_for(
            client.parse(model=model, system=system, user=user, schema=ExtractionResult,
                         constrained=False),
            timeout=timeout_s,
        )

    result = await call_with_retry(call, backoff_s)
    return _require_parsed(result), result.input_tokens, result.output_tokens
