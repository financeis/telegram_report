"""One company's profile extraction: AI call → grounding → (one escalation) → caps (spec §5.4).

``extract_profile`` asks the profile model for a ``CompanyProfile``, drops every term that is not
in the input text and computes the grounding ratio (kept ÷ given). Below 0.8 it asks the
escalation model once more and keeps that grounded result, whatever its ratio; if that second
call fails, the first result stands. Then lists are de-duplicated and cut to their caps.

Per call: a transient error (429 / 5xx / connection / timeout) is retried once after
``backoff_s`` (``core.llm.call_with_retry``); a reply that does not validate is asked once more
("형식 오류 재시도"); a refusal fails. Claude models get the schema as a decoding grammar
(``constrained=True``). Any failure of the first model's extraction propagates: the build
records the company as failed.
"""
from __future__ import annotations

import logging
from typing import Any, NamedTuple

from pydantic import ValidationError

from research_desk.core.llm import call_with_retry

from . import logic
from .prompts import render_messages
from .schemas import CompanyProfile

logger = logging.getLogger(__name__)

FORMAT_TRIES = 2


class ProfileResult(NamedTuple):
    profile: CompanyProfile         # grounded and capped
    grounding_ratio: float          # of the result kept
    tokens: tuple[int, int]         # (input, output) over every call made
    model: str                      # the model whose result was kept


class _Grounded(NamedTuple):
    profile: CompanyProfile
    ratio: float
    tokens: tuple[int, int]


async def _ask(client: Any, model: str, system: str, user: str, backoff_s: float) -> tuple[CompanyProfile, tuple[int, int]]:
    for attempt in range(1, FORMAT_TRIES + 1):
        try:
            result = await call_with_retry(
                lambda: client.parse(model=model, system=system, user=user, schema=CompanyProfile,
                                     constrained=True),
                backoff_s)
        except ValidationError:
            if attempt == FORMAT_TRIES:
                raise
            logger.warning("profile reply of %s did not validate; asking once more", model)
            continue
        if result.parsed is None:
            raise RuntimeError(f"AI가 응답을 거부했습니다: {result.refusal}")
        return result.parsed, (result.input_tokens, result.output_tokens)
    raise AssertionError("unreachable")


async def _extract(client, model, system, user, input_text, synonyms, backoff_s) -> _Grounded:
    profile, tokens = await _ask(client, model, system, user, backoff_s)
    grounded, kept, given = logic.ground_profile(profile, input_text, synonyms)
    return _Grounded(grounded, logic.grounding_ratio(kept, given), tokens)


async def extract_profile(
    input_text: str,
    *,
    client: Any,                      # research_desk.core.llm.LLMClient
    model: str,
    escalation_model: str,
    synonyms: logic.Synonyms,
    corp_name: str = "",
    backoff_s: float = 5.0,
) -> ProfileResult:
    """The grounded, capped profile of one company's assembled input. See the module docstring."""
    system, user = render_messages(corp_name, input_text)
    first = await _extract(client, model, system, user, input_text, synonyms, backoff_s)
    kept, used_model, tokens = first, model, first.tokens
    if first.ratio < logic.GROUNDING_MIN:
        try:
            second = await _extract(client, escalation_model, system, user, input_text, synonyms, backoff_s)
        except Exception as exc:   # the first result stands; the failure is logged by type only
            logger.warning("escalation to %s failed (%s); keeping the first result",
                           escalation_model, type(exc).__name__)
        else:
            kept, used_model = second, escalation_model
            tokens = (first.tokens[0] + second.tokens[0], first.tokens[1] + second.tokens[1])
    return ProfileResult(profile=logic.cap_profile(kept.profile, synonyms), grounding_ratio=kept.ratio,
                         tokens=tokens, model=used_model)
