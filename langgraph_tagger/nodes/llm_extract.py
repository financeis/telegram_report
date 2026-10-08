"""llm_extract node: single structured-output LLM call (provider picked by model name)."""
from __future__ import annotations

from pydantic import ValidationError

from langgraph_tagger.llm_provider import TRANSIENT_ERRORS, LLMClient
from langgraph_tagger.llm_schemas import LLMExtraction
from langgraph_tagger.prompts import SYSTEM_PROMPT, user_message
from langgraph_tagger.state import RowState


class LLMTransientError(Exception):
    """Wraps 429/5xx/timeout/network from the LLM provider; orchestrator reverts row to pending."""


async def llm_extract(state: RowState, *, client: LLMClient) -> dict:
    if state.get("pdf_unreadable"):
        # Short-circuit: don't burn an LLM call on unreadable input
        return {"llm_raw": None}

    sent_at = state["sent_at"]
    sent_iso = sent_at.isoformat() if sent_at else ""

    try:
        result = await client.parse(
            model=state["model"],
            system=SYSTEM_PROMPT,
            user=user_message(
                file_name=state["file_name"],
                caption=state.get("caption"),
                sent_at_iso=sent_iso,
                pdf_text=state["pdf_text"],
            ),
            schema=LLMExtraction,
            # Luna models reject temperature=0; omit it to use the supported default.
            temperature=None if "luna" in state["model"] else 0,
        )
    except (*TRANSIENT_ERRORS, ValidationError) as e:
        raise LLMTransientError(str(e)) from e

    if result.refusal:
        return {"llm_raw": None, "llm_refusal": result.refusal}
    return {"llm_raw": result.parsed}
