"""DiffResult: the compare AI call's reply (the narrative only).

Ported from langgraph_tagger/analytics/llm_summary/tests/test_schemas.py::
test_diff_result_narrative_optional.

New (spec §9.3): the real DiffResult through the two schema conversions it meets in production —
anthropic.transform_schema (Claude structured output: the comparison call is constrained) and the
strict schema openai.pydantic_function_tool builds (what core.llm writes to the codex CLI's
--output-schema file, and what the OpenAI API path sends) — and replies in that shape.
"""
import json

import anthropic
import openai
import pytest
from pydantic import ValidationError

from research_desk.features.compare.schemas import DiffResult


def _types(prop: dict) -> set:
    return {option['type'] for option in prop['anyOf']}


def test_diff_result_narrative_optional():
    d = DiffResult(diff_narrative=None)
    assert d.diff_narrative is None
    d2 = DiffResult(diff_narrative='이전 리포트 대비 ...')
    assert d2.diff_narrative.startswith('이전')


def test_the_model_schema_is_one_optional_text():
    schema = DiffResult.model_json_schema()
    assert schema['type'] == 'object' and schema['title'] == 'DiffResult'
    assert set(schema['properties']) == {'diff_narrative'}
    assert _types(schema['properties']['diff_narrative']) == {'string', 'null'}
    assert 'required' not in schema          # the narrative may be left out
    assert 'description' not in schema       # no class docstring: nothing extra reaches the model


def test_anthropic_structured_output_schema():
    schema = anthropic.transform_schema(DiffResult)
    assert schema['type'] == 'object'
    assert schema['additionalProperties'] is False
    assert set(schema['properties']) == {'diff_narrative'}
    assert _types(schema['properties']['diff_narrative']) == {'string', 'null'}
    json.dumps(schema, ensure_ascii=False)


def test_codex_strict_schema():
    """What core.llm writes to the --output-schema file for a codex: model."""
    tool = openai.pydantic_function_tool(DiffResult)
    assert tool['function']['name'] == 'DiffResult'
    assert tool['function']['strict'] is True
    params = tool['function']['parameters']
    assert params['type'] == 'object'
    assert params['additionalProperties'] is False
    assert params['required'] == ['diff_narrative']   # strict: present, but it may be null
    assert _types(params['properties']['diff_narrative']) == {'string', 'null'}
    json.dumps(params, ensure_ascii=False)


@pytest.mark.parametrize('reply, narrative', [
    ('{"diff_narrative": "KB는 목표주가를 상향했다."}', 'KB는 목표주가를 상향했다.'),
    ('{"diff_narrative": null}', None),
    ('{}', None),
])
def test_replies_in_that_shape_validate(reply, narrative):
    assert DiffResult.model_validate_json(reply).diff_narrative == narrative


def test_a_narrative_that_is_not_text_is_rejected():
    with pytest.raises(ValidationError):
        DiffResult.model_validate({'diff_narrative': 123})
