"""The LLM reply schema (spec §9.2, §9.3).

- Its value sets equal research_desk/domain/vocabulary.yaml (report types 6,
  publisher types 4; the graph's OOS reasons 5).
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

SAMPLE_REPLY = {
    "report_type": "단일종목",
    "title": "삼성전자 1Q26 Preview",
    "published_at": "2026-05-01",
    "stock_codes_raw": ["005930"],
    "company_names_raw": ["삼성전자"],
    "publisher_canon": "키움증권",
    "publisher_type": "broker",
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
    assert _enum(schema["properties"]["publisher_type"]) == list(reports.PUBLISHER_TYPES)


def test_anthropic_structured_output_schema():
    schema = anthropic.transform_schema(LLMExtraction)
    props = schema["properties"]
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(props) == set(LLMExtraction.model_fields)
    assert set(schema["required"]) == {"report_type", "oos_signals", "self_confidence"}
    assert _enum(props["report_type"]) == list(reports.REPORT_TYPES)
    assert _enum(props["publisher_type"]) == list(reports.PUBLISHER_TYPES)
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
    assert _enum(params["properties"]["publisher_type"]) == list(reports.PUBLISHER_TYPES)
    assert params["$defs"]["OOSSignals"]["additionalProperties"] is False
    json.dumps(params, ensure_ascii=False)


def test_a_reply_in_that_shape_validates():
    parsed = LLMExtraction.model_validate_json(json.dumps(SAMPLE_REPLY, ensure_ascii=False))
    assert parsed.report_type == "단일종목"
    assert parsed.oos_signals.private_company_likely is False


@pytest.mark.parametrize("field,value", [
    ("report_type", "IPO"),               # not one of the 6 report types
    ("publisher_type", "company"),        # removed in v2
    ("self_confidence", "certain"),
])
def test_values_outside_the_vocabulary_are_rejected(field, value):
    with pytest.raises(ValidationError):
        LLMExtraction.model_validate({**SAMPLE_REPLY, field: value})
