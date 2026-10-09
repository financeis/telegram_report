"""The LLM reply schema (spec §9.2, §9.3).

- Its value sets equal research_desk/domain/vocabulary.yaml (report types 6,
  publisher types 4; the graph's OOS reasons 5).
- The publisher answer is closed to the publisher dictionary's canonical names: the
  request schema lists them as an enum, and any other answer (an alias, a typo, a
  sentence, a non-string) validates as null instead of failing (a validation error
  would send the row back to pending on every batch). The AI does not answer the
  publisher type; the graph takes it from the dictionary.
- The real LLMExtraction goes through the two schema conversions it meets in
  production: anthropic.transform_schema (Claude structured output) and the
  strict schema openai.pydantic_function_tool builds (the codex CLI path, which
  is also what the OpenAI API path sends).
"""
from __future__ import annotations

import json
import typing

import anthropic
import openai
import pytest
from pydantic import ValidationError

from research_desk.domain import reports
from research_desk.tagger import llm_schemas, state
from research_desk.tagger.llm_schemas import LLMExtraction
from research_desk.tagger.vocabulary import canonical_names

SAMPLE_REPLY = {
    "report_type": "단일종목",
    "title": "삼성전자 1Q26 Preview",
    "published_at": "2026-05-01",
    "stock_codes_raw": ["005930"],
    "company_names_raw": ["삼성전자"],
    "publisher_canon": "키움증권",
    "analysts": ["홍길동"],
    "oos_signals": {"foreign_primary_coverage": False, "etf_or_fund": False,
                    "digital_asset": False, "private_company_likely": False},
    "self_confidence": "high",
    "notes": None,
}


def _enum(prop: dict) -> list:
    """The enum of a property, also when it is wrapped in anyOf [..., null]."""
    if "enum" in prop:
        return prop["enum"]
    (values,) = [option["enum"] for option in prop["anyOf"] if "enum" in option]
    return values


def _allows_null(prop: dict) -> bool:
    return {"type": "null"} in prop.get("anyOf", [])


def test_report_types_equal_the_shared_vocabulary():
    assert typing.get_args(llm_schemas.REPORT_TYPES) == reports.REPORT_TYPES


def test_publisher_types_equal_the_shared_vocabulary():
    assert typing.get_args(llm_schemas.PUBLISHER_TYPES) == reports.PUBLISHER_TYPES


def test_graph_oos_reasons_equal_the_shared_vocabulary():
    hint = typing.get_type_hints(state.RowState)["oos_reason"]
    (literal,) = [arg for arg in typing.get_args(hint) if arg is not type(None)]
    assert typing.get_args(literal) == reports.OOS_REASONS


def test_pydantic_schema_uses_the_vocabulary():
    schema = LLMExtraction.model_json_schema()
    assert _enum(schema["properties"]["report_type"]) == list(reports.REPORT_TYPES)
    # The publisher answer is one canonical name of the dictionary (file order) or null.
    assert _enum(schema["properties"]["publisher_canon"]) == list(canonical_names())
    assert _allows_null(schema["properties"]["publisher_canon"])


def test_the_ai_does_not_answer_the_publisher_type():
    """The stored publisher type comes from the dictionary section, never from the AI."""
    assert "publisher_type" not in LLMExtraction.model_fields
    assert "publisher_type" not in LLMExtraction.model_json_schema()["properties"]


def test_anthropic_structured_output_schema():
    schema = anthropic.transform_schema(LLMExtraction)
    props = schema["properties"]
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(props) == set(LLMExtraction.model_fields)
    assert set(schema["required"]) == {"report_type", "oos_signals", "self_confidence"}
    assert _enum(props["report_type"]) == list(reports.REPORT_TYPES)
    assert _enum(props["publisher_canon"]) == list(canonical_names())
    assert _allows_null(props["publisher_canon"])
    signals = schema["$defs"]["OOSSignals"]
    assert signals["additionalProperties"] is False
    assert set(signals["required"]) == {
        "foreign_primary_coverage", "etf_or_fund", "digital_asset", "private_company_likely"}
    json.dumps(schema, ensure_ascii=False)


def test_codex_strict_schema():
    """What core.llm writes to the --output-schema file for a codex: model."""
    tool = openai.pydantic_function_tool(LLMExtraction)
    assert tool["function"]["name"] == "LLMExtraction"
    assert tool["function"]["strict"] is True
    params = tool["function"]["parameters"]
    assert params["additionalProperties"] is False
    assert set(params["required"]) == set(params["properties"]) == set(LLMExtraction.model_fields)
    assert _enum(params["properties"]["report_type"]) == list(reports.REPORT_TYPES)
    assert _enum(params["properties"]["publisher_canon"]) == list(canonical_names())
    assert _allows_null(params["properties"]["publisher_canon"])
    assert params["$defs"]["OOSSignals"]["additionalProperties"] is False
    json.dumps(params, ensure_ascii=False)


def test_a_reply_in_that_shape_validates():
    parsed = LLMExtraction.model_validate_json(json.dumps(SAMPLE_REPLY, ensure_ascii=False))
    assert parsed.report_type == "단일종목"
    assert parsed.oos_signals.private_company_likely is False


@pytest.mark.parametrize("field,value", [
    ("report_type", "IPO"),               # not one of the 6 report types
    ("self_confidence", "certain"),
])
def test_values_outside_the_vocabulary_are_rejected(field, value):
    with pytest.raises(ValidationError):
        LLMExtraction.model_validate({**SAMPLE_REPLY, field: value})


@pytest.mark.parametrize("canonical", canonical_names())
def test_a_canonical_publisher_answer_is_kept(canonical):
    reply = json.dumps({**SAMPLE_REPLY, "publisher_canon": canonical}, ensure_ascii=False)
    assert LLMExtraction.model_validate_json(reply).publisher_canon == canonical


@pytest.mark.parametrize("answer", [
    "Eugene",                 # an alias of 유진투자증권
    "EUGENE",                 # a filename tag
    "유진",                    # a Korean alias
    "따라서 확인 불가",          # a sentence
    "키움 증권",                # a typo
    " 키움증권",                # not the same characters
    "키움증권 ",
    "",
    "null",
    123,
    ["키움증권"],
    {"canonical": "키움증권"},
    None,
])
def test_a_non_canonical_publisher_answer_is_null_not_an_error(answer):
    """Exact canonical names only; everything else is null — and never a ValidationError,
    on the plain-JSON paths (Anthropic without a grammar, codex) as well."""
    reply = json.dumps({**SAMPLE_REPLY, "publisher_canon": answer}, ensure_ascii=False)
    assert LLMExtraction.model_validate_json(reply).publisher_canon is None
    assert LLMExtraction.model_validate({**SAMPLE_REPLY, "publisher_canon": answer}).publisher_canon is None


def test_a_reply_without_a_publisher_is_null():
    reply = {key: value for key, value in SAMPLE_REPLY.items() if key != "publisher_canon"}
    assert LLMExtraction.model_validate_json(json.dumps(reply, ensure_ascii=False)).publisher_canon is None


@pytest.mark.parametrize("publisher_type", ["broker", "data_provider", "company", 7])
def test_a_reply_that_still_carries_a_publisher_type_validates(publisher_type):
    """An old-shaped reply (or a model adding the key) is not an error; the value is dropped."""
    reply = json.dumps({**SAMPLE_REPLY, "publisher_type": publisher_type}, ensure_ascii=False)
    parsed = LLMExtraction.model_validate_json(reply)
    assert parsed.publisher_canon == "키움증권"
    assert not hasattr(parsed, "publisher_type")
