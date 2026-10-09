"""Tests for llm_extract node (mocked LLMClient).

Besides the call itself: for every row with an AI result, llm_extract stores the
publisher checks in the state — the stored publisher (the AI's answer only when it is
exactly a dictionary canonical name, else None), its type (the dictionary section) and
the suspect mark the file name's tag gives (``filename_mismatch`` / ``unknown`` / None).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import anthropic
import httpx
import pytest
from openai import APITimeoutError, InternalServerError, RateLimitError

from research_desk.core.llm import ImageInputUnsupported, StructuredResult
from research_desk.tagger import prompts
from research_desk.tagger.llm_schemas import LLMExtraction
from research_desk.tagger.nodes.llm_extract import LLMTransientError, llm_extract, publisher_checks
from research_desk.tagger.prompts import SYSTEM_PROMPT
from research_desk.tagger.tests.conftest import make_llm_extraction


def _httpx_response(status: int) -> httpx.Response:
    """openai 2.x requires the response to have its request set."""
    return httpx.Response(status, request=httpx.Request("POST", "http://x"))


def _state(model: str = "gpt-5.4-mini", file_name: str = "삼성전자_1Q26.pdf") -> dict:
    return {
        "model": model,
        "pdf_text": "샘플 텍스트",
        "file_name": file_name,
        "caption": None,
        "sent_at": __import__("datetime").datetime(2026, 5, 1, 9, 0),
    }


PUBLISHER_KEYS = {"publisher_final", "publisher_type_final", "publisher_suspect"}


def _reply_json(**overrides) -> str:
    """A reply as the model writes it (JSON text), before any validation."""
    reply = json.loads(make_llm_extraction().model_dump_json())
    reply.update(overrides)
    return json.dumps(reply, ensure_ascii=False)


def _validating_parse(mock_llm_client, reply_text: str) -> None:
    """parse() validates the model's JSON text against the schema, like core.llm does on
    the plain-JSON paths (Anthropic without a grammar, codex) and after a grammar."""
    async def parse(*, schema, **_):
        return StructuredResult(parsed=schema.model_validate_json(reply_text))
    mock_llm_client.parse.side_effect = parse


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


# ── publisher checks (stored publisher, dictionary type, suspect mark) ─────────

@pytest.mark.parametrize("answer,file_name,expected", [
    # no filename tag
    ("키움증권", "삼성전자_1Q26.pdf", ("키움증권", "broker", None)),
    (None, "삼성전자_1Q26.pdf", (None, None, "unknown")),
    ("Eugene", "삼성전자_1Q26.pdf", (None, None, "unknown")),            # alias → null
    ("따라서 확인 불가", "삼성전자_1Q26.pdf", (None, None, "unknown")),   # sentence → null
    # a tag the dictionary knows
    ("키움증권", "삼성전자_20260511_Kiwoom_1096333.pdf", ("키움증권", "broker", None)),
    ("키움증권", "삼성전자_20260511_MERITZ_1096333.pdf", ("키움증권", "broker", "filename_mismatch")),
    (None, "삼성전자_20260511_MERITZ_1096333.pdf", (None, None, "filename_mismatch")),
    ("Eugene", "삼성전자_20260511_Eugene_1096333.pdf", (None, None, "filename_mismatch")),
    ("유진투자증권", "삼성전자_20260511_Eugene_1096333.pdf", ("유진투자증권", "broker", None)),
    # a tag the dictionary does not know points to no publisher
    (None, "삼성전자_20260511_Nowhere_1096333.pdf", (None, None, "unknown")),
    ("키움증권", "삼성전자_20260511_Nowhere_1096333.pdf", ("키움증권", "broker", None)),
    # IR자료: the publisher is 해당기업 (other); a broker tag disagrees with it
    ("해당기업", "에코프로_IR_20260511_MERITZ_1096333.pdf", ("해당기업", "other", "filename_mismatch")),
    ("해당기업", "에코프로_IR_20260511_해당기업_1096333.pdf", ("해당기업", "other", None)),
    ("해당기업", "에코프로_IR.pdf", ("해당기업", "other", None)),
    # the type is the dictionary section of the stored name
    ("에프앤가이드", "x.pdf", ("에프앤가이드", "data_provider", None)),
    ("IR큐더스", "x.pdf", ("IR큐더스", "ir_agency", None)),
])
def test_publisher_checks(answer, file_name, expected):
    out = publisher_checks(answer, file_name)
    assert (out["publisher_final"], out["publisher_type_final"], out["publisher_suspect"]) == expected
    assert set(out) == PUBLISHER_KEYS


@pytest.mark.parametrize("answer", [123, ["키움증권"], "", " 키움증권"])
def test_publisher_checks_never_raise_on_odd_answers(answer):
    assert publisher_checks(answer, None) == {
        "publisher_final": None, "publisher_type_final": None, "publisher_suspect": "unknown"}


@pytest.mark.asyncio
@pytest.mark.parametrize("answer,file_name,expected", [
    ("키움증권", "삼성전자_1Q26.pdf", ("키움증권", "broker", None)),
    ("Eugene", "삼성전자_20260511_Eugene_1096333.pdf", (None, None, "filename_mismatch")),
    ("Eugene", "삼성전자_1Q26.pdf", (None, None, "unknown")),
    ("따라서 확인 불가", "삼성전자_1Q26.pdf", (None, None, "unknown")),
    (None, "삼성전자_20260511_MERITZ_1096333.pdf", (None, None, "filename_mismatch")),
    ("키움증권", "삼성전자_20260511_MERITZ_1096333.pdf", ("키움증권", "broker", "filename_mismatch")),
])
async def test_llm_extract_stores_the_publisher_checks(mock_llm_client, answer, file_name, expected):
    """A non-canonical answer is stored as None: no exception, so no revert to pending."""
    _validating_parse(mock_llm_client, _reply_json(publisher_canon=answer))

    out = await llm_extract(_state(file_name=file_name), client=mock_llm_client)

    assert (out["publisher_final"], out["publisher_type_final"], out["publisher_suspect"]) == expected
    assert out["llm_raw"].publisher_canon == expected[0]


@pytest.mark.asyncio
async def test_the_ai_publisher_type_is_not_used(mock_llm_client):
    """Even when a reply still carries a publisher_type, the type is the dictionary's."""
    _validating_parse(mock_llm_client, _reply_json(publisher_canon="키움증권", publisher_type="data_provider"))
    out = await llm_extract(_state(), client=mock_llm_client)
    assert out["publisher_final"] == "키움증권"
    assert out["publisher_type_final"] == "broker"


@pytest.mark.asyncio
async def test_llm_extract_checks_the_answer_itself(mock_llm_client):
    """The stored publisher is checked against the dictionary in the node too, so a parsed
    object that skipped validation cannot carry an alias into the row."""
    unchecked = make_llm_extraction().model_copy(update={"publisher_canon": "Eugene"})
    mock_llm_client.set_response(unchecked)
    out = await llm_extract(_state(), client=mock_llm_client)
    assert (out["publisher_final"], out["publisher_type_final"], out["publisher_suspect"]) == (
        None, None, "unknown")


@pytest.mark.asyncio
async def test_no_publisher_checks_without_an_ai_result(mock_llm_client):
    mock_llm_client.set_refusal("policy violation: x")
    refused = await llm_extract(_state(file_name="x_20260511_MERITZ_1.pdf"), client=mock_llm_client)
    unreadable = await llm_extract({"pdf_unreadable": True, "file_name": "x_20260511_MERITZ_1.pdf"},
                                   client=mock_llm_client)
    assert not PUBLISHER_KEYS & set(refused)
    assert not PUBLISHER_KEYS & set(unreadable)


# ── rows read from the page picture (no text on pages 1–3) ───────────────────

PNG = b"\x89PNG\r\n\x1a\n-page-1"
SENT_AT = datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc)


def _picture_state(model: str = "gpt-5.4-mini", file_name: str = "삼성전자_1Q26.pdf",
                   caption: str | None = "오늘의 리포트") -> dict:
    """extract_pdf's output for a PDF with no text on pages 1–3 whose page 1 was drawn."""
    return {
        "model": model, "file_name": file_name, "caption": caption, "sent_at": SENT_AT,
        "pdf_text": "", "pages_used": [1], "pdf_unreadable": True, "page_images": [PNG],
    }


def test_the_page_image_phrase_is_fixed():
    """It stands where the PDF text goes; changing it changes every picture request."""
    assert prompts.PAGE_IMAGE_NOTE == (
        "(글자를 읽을 수 없는 PDF라 페이지를 그림으로 첨부했다. 첨부한 그림을 보고 추출한다.)")


def test_user_message_for_a_picture_row():
    assert prompts.user_message(file_name="a.pdf", caption=None,
                                sent_at_iso="2026-05-01T09:00:00+00:00", page_image=True) == (
        "파일명: a.pdf\n"
        "caption: (없음)\n"
        "sent_at (UTC): 2026-05-01T09:00:00+00:00\n"
        "PDF 첫 페이지(들):\n"
        "---\n"
        f"{prompts.PAGE_IMAGE_NOTE}\n"
        "---"
    )


def test_user_message_for_a_text_row_is_unchanged():
    assert prompts.user_message(file_name="a.pdf", caption="캡션",
                                sent_at_iso="2026-05-01T09:00:00", pdf_text="본문") == (
        "파일명: a.pdf\ncaption: 캡션\nsent_at (UTC): 2026-05-01T09:00:00\n"
        "PDF 첫 페이지(들):\n---\n본문\n---"
    )


@pytest.mark.asyncio
async def test_a_picture_row_asks_the_ai_with_the_page_png(mock_llm_client):
    extraction = make_llm_extraction()
    mock_llm_client.set_response(extraction)

    out = await llm_extract(_picture_state(), client=mock_llm_client)

    mock_llm_client.parse.assert_awaited_once()
    kwargs = mock_llm_client.parse.call_args.kwargs
    assert list(kwargs["images"]) == [PNG]
    assert kwargs["model"] == "gpt-5.4-mini"
    assert kwargs["system"] is SYSTEM_PROMPT            # same system prompt as text rows
    assert kwargs["schema"] is LLMExtraction
    assert kwargs["temperature"] == 0
    assert kwargs["user"] == (
        "파일명: 삼성전자_1Q26.pdf\n"
        "caption: 오늘의 리포트\n"
        "sent_at (UTC): 2026-05-01T09:00:00+00:00\n"
        "PDF 첫 페이지(들):\n"
        "---\n"
        f"{prompts.PAGE_IMAGE_NOTE}\n"
        "---"
    )
    assert out["llm_raw"] == extraction
    assert out["page_image"] is True
    assert out["pdf_unreadable"] is False      # read after all → the normal branches
    assert "page_image_unsupported" not in out
    assert out["page_images"] == []            # the PNG is not carried further
    assert PUBLISHER_KEYS <= set(out)


@pytest.mark.asyncio
async def test_luna_picture_rows_omit_temperature(mock_llm_client):
    mock_llm_client.set_response(make_llm_extraction())
    await llm_extract(_picture_state(model="gpt-5.6-luna"), client=mock_llm_client)
    assert mock_llm_client.parse.call_args.kwargs["temperature"] is None


@pytest.mark.asyncio
async def test_text_rows_send_no_pictures(mock_llm_client):
    mock_llm_client.set_response(make_llm_extraction())

    out = await llm_extract({**_state(), "caption": "캡션"}, client=mock_llm_client)

    kwargs = mock_llm_client.parse.call_args.kwargs
    assert set(kwargs) == {"model", "system", "user", "schema", "temperature"}
    assert kwargs["user"] == (
        "파일명: 삼성전자_1Q26.pdf\ncaption: 캡션\nsent_at (UTC): 2026-05-01T09:00:00\n"
        "PDF 첫 페이지(들):\n---\n샘플 텍스트\n---"
    )
    assert not {"page_image", "page_image_unsupported", "pdf_unreadable", "page_images"} & set(out)


@pytest.mark.asyncio
async def test_the_system_prompt_is_the_same_for_text_and_picture_rows(mock_llm_client):
    mock_llm_client.set_response(make_llm_extraction())
    await llm_extract(_state(), client=mock_llm_client)
    await llm_extract(_picture_state(file_name="다른파일.pdf", caption="다른 캡션"), client=mock_llm_client)

    (text_call, picture_call) = mock_llm_client.parse.call_args_list
    assert text_call.kwargs["system"] == picture_call.kwargs["system"] == SYSTEM_PROMPT
    assert "다른파일.pdf" not in SYSTEM_PROMPT


@pytest.mark.asyncio
@pytest.mark.parametrize("images", [[], None])
async def test_no_picture_means_no_ai_call(mock_llm_client, images):
    state = _picture_state()
    if images is None:
        del state["page_images"]
    else:
        state["page_images"] = images

    out = await llm_extract(state, client=mock_llm_client)

    mock_llm_client.parse.assert_not_called()
    assert out == {"llm_raw": None}


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["gpt-4", "gpt-3.5-turbo", "codex:o3-mini"])
async def test_a_model_that_cannot_take_pictures_leaves_the_row_unreadable(mock_llm_client, model):
    out = await llm_extract(_picture_state(model=model), client=mock_llm_client)

    mock_llm_client.parse.assert_not_called()
    # pdf_unreadable stays True (not in the update) → status_unreadable / first_page_unreadable
    assert out == {"llm_raw": None, "page_image_unsupported": True, "page_images": []}


@pytest.mark.asyncio
async def test_parse_refusing_pictures_is_handled_the_same(mock_llm_client):
    """If parse still says the model cannot take images, the row is unreadable — not an error
    that would send it back to pending."""
    mock_llm_client.set_exception(ImageInputUnsupported("model gpt-5.4-mini cannot take page images"))

    out = await llm_extract(_picture_state(), client=mock_llm_client)

    assert out == {"llm_raw": None, "page_image_unsupported": True, "page_images": []}


@pytest.mark.asyncio
async def test_a_refused_picture_row_is_a_refusal(mock_llm_client):
    mock_llm_client.set_refusal("cannot read this")

    out = await llm_extract(_picture_state(), client=mock_llm_client)

    assert out["llm_raw"] is None
    assert out["llm_refusal"] == "cannot read this"
    assert out["pdf_unreadable"] is False      # the AI was asked: llm_refusal, not unreadable
    assert out["page_image"] is True
    assert not PUBLISHER_KEYS & set(out)


@pytest.mark.asyncio
async def test_a_picture_row_without_a_parsed_answer(mock_llm_client):
    mock_llm_client.parse.return_value = StructuredResult(parsed=None)

    out = await llm_extract(_picture_state(), client=mock_llm_client)

    assert out["llm_raw"] is None
    assert out["pdf_unreadable"] is False
    assert out["page_image"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("exc_factory", [
    lambda: RateLimitError("429", response=_httpx_response(429), body=None),
    lambda: anthropic.OverloadedError("529", response=_httpx_response(529), body=None),
])
async def test_picture_row_transient_errors_raise_LLMTransientError(mock_llm_client, exc_factory):
    mock_llm_client.set_exception(exc_factory())
    with pytest.raises(LLMTransientError):
        await llm_extract(_picture_state(), client=mock_llm_client)


@pytest.mark.asyncio
@pytest.mark.parametrize("answer,file_name,expected", [
    ("키움증권", "삼성전자_1Q26.pdf", ("키움증권", "broker", None)),
    ("키움증권", "삼성전자_20260511_MERITZ_1096333.pdf", ("키움증권", "broker", "filename_mismatch")),
    ("Eugene", "삼성전자_1Q26.pdf", (None, None, "unknown")),
])
async def test_picture_rows_get_the_publisher_checks(mock_llm_client, answer, file_name, expected):
    _validating_parse(mock_llm_client, _reply_json(publisher_canon=answer))

    out = await llm_extract(_picture_state(file_name=file_name), client=mock_llm_client)

    assert (out["publisher_final"], out["publisher_type_final"], out["publisher_suspect"]) == expected
