"""llm_extract node: single structured-output LLM call (provider picked by model name).

With an AI result it also stores the publisher checks (``publisher_checks``) for the
row, whatever branch the row takes next (in-scope or out-of-scope).

A PDF with no text on pages 1-3 (``pdf_unreadable``) whose page 1 was drawn
(``page_images``) is asked about with that picture instead of text: the same system
prompt and answer shape, PAGE_IMAGE_NOTE where the text goes. The row then gets
``page_image`` and ``pdf_unreadable`` back to False, so its answer takes the same
branches as a text row's. No picture, or a model that cannot take pictures
(``page_image_unsupported``), leaves the row unreadable without an AI call.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import ValidationError

from research_desk.core.llm import (
    TRANSIENT_ERRORS, ImageInputUnsupported, LLMClient, supports_images,
)
from research_desk.tagger import vocabulary
from research_desk.tagger.llm_schemas import LLMExtraction
from research_desk.tagger.prompts import SYSTEM_PROMPT, user_message
from research_desk.tagger.state import RowState


class LLMTransientError(Exception):
    """Wraps 429/5xx/timeout/network from the LLM provider; orchestrator reverts row to pending."""


def publisher_checks(answer: Any, file_name: Optional[str]) -> dict:
    """The stored publisher, its type and the suspect mark for one AI publisher answer.

    - ``publisher_final``: the answer only when it is exactly a canonical name of the
      publisher dictionary; an alias, a typo, a sentence or a non-string is None. Never
      an error, so a bad answer cannot send the row back to pending.
    - ``publisher_type_final``: the dictionary section of that name (None with None),
      never the AI's type.
    - ``publisher_suspect``: the publisher the file name's tag points to
      (``vocabulary.filename_publisher``) only marks a suspect and never changes the
      stored value — ``filename_mismatch`` when there is one and the stored publisher
      differs from it (None included), ``unknown`` when there is none and the stored
      publisher is None, otherwise None.
    """
    kind = vocabulary.publisher_type(answer) if isinstance(answer, str) else None
    stored = answer if kind is not None else None
    pointed = vocabulary.filename_publisher(file_name)
    if pointed is not None:
        suspect = "filename_mismatch" if stored != pointed else None
    else:
        suspect = "unknown" if stored is None else None
    return {"publisher_final": stored, "publisher_type_final": kind, "publisher_suspect": suspect}


async def llm_extract(state: RowState, *, client: LLMClient) -> dict:
    if not state.get("pdf_unreadable"):
        return await _ask(state, client, user=_user_message(state, pdf_text=state["pdf_text"]))

    images = state.get("page_images")
    if not images:
        # Short-circuit: don't burn an LLM call on unreadable input
        return {"llm_raw": None}
    if not supports_images(state["model"]):
        return _picture_unsupported()
    try:
        answer = await _ask(state, client, user=_user_message(state, page_image=True),
                            images=list(images))
    except ImageInputUnsupported:
        return _picture_unsupported()
    # Read after all: the answer takes the same branches as a text row's. The PNG is
    # not needed any more; dropping it keeps it out of the later nodes (and their traces).
    return {**answer, "pdf_unreadable": False, "page_image": True, "page_images": []}


def _picture_unsupported() -> dict:
    """The model cannot take the page picture: no AI call, the row stays unreadable."""
    return {"llm_raw": None, "page_image_unsupported": True, "page_images": []}


def _user_message(state: RowState, **body: Any) -> str:
    """The row's user message: its own values, then the PDF text or the picture note."""
    sent_at = state["sent_at"]
    return user_message(
        file_name=state["file_name"],
        caption=state.get("caption"),
        sent_at_iso=sent_at.isoformat() if sent_at else "",
        **body,
    )


async def _ask(state: RowState, client: LLMClient, *, user: str,
               images: Optional[list[bytes]] = None) -> dict:
    """One parse() call (``images`` only for a picture row) → the row's update."""
    pictures = {} if images is None else {"images": images}
    try:
        result = await client.parse(
            model=state["model"],
            system=SYSTEM_PROMPT,
            user=user,
            schema=LLMExtraction,
            # Luna models reject temperature=0; omit it to use the supported default.
            temperature=None if "luna" in state["model"] else 0,
            **pictures,
        )
    except (*TRANSIENT_ERRORS, ValidationError) as e:
        raise LLMTransientError(str(e)) from e

    if result.refusal:
        return {"llm_raw": None, "llm_refusal": result.refusal}
    parsed = result.parsed
    if parsed is None:
        return {"llm_raw": None}
    return {"llm_raw": parsed, **publisher_checks(parsed.publisher_canon, state.get("file_name"))}
