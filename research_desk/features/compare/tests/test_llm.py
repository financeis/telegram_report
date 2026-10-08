"""diff_one: the compare AI call (clients mocked, no network).

Ported from langgraph_tagger/analytics/llm_summary/tests/test_llm.py, the diff tests:
test_diff_one_happy_path, test_diff_one_returns_null_narrative.

New (spec §9.3, analysis·compare): the call sends the comparison prompt with the DiffResult schema,
constrained; each try has its own time limit; a transient error, a timeout included, is tried once
more after 5 seconds and then fails; other errors and refusals fail at once. And the real DiffResult
through core.llm's constrained Anthropic path and the Codex strict schema.
"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import anthropic
import openai
import pytest

from research_desk.core import llm as core_llm
from research_desk.core.llm import LLMClient, StructuredResult, TransientLLMError
from research_desk.features.compare.llm import diff_one
from research_desk.features.compare.prompts import render_diff_messages
from research_desk.features.compare.schemas import DiffResult

PREVIOUS = {'target_price_new': 70000, 'recommendation': '매수', 'one_line_summary': '이전 view'}
CURRENT = {'target_price_new': 85000, 'recommendation': '매수', 'one_line_summary': '현재 view'}


def _fake_result(parsed_obj):
    return StructuredResult(parsed=parsed_obj, input_tokens=1000, output_tokens=200)


def _client(*outcomes):
    """A client whose parse gives these outcomes in turn (an exception is raised)."""
    calls = []

    async def parse(**kwargs):
        calls.append(kwargs)
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, (int, float)):   # a slow try: sleep this long, then answer
            await asyncio.sleep(outcome)
            return _fake_result(DiffResult(diff_narrative='늦은 응답'))
        return outcome

    fake = AsyncMock()
    fake.parse = parse
    fake.calls = calls
    return fake


async def _diff(client, **changes):
    kwargs = dict(client=client, model='gpt-5.4-mini', prev_summary=PREVIOUS, curr_summary=CURRENT,
                  prev_match_type='same_publisher', prev_report_id=1, prev_publisher='KB',
                  curr_publisher='KB', timeout_s=10, backoff_s=0)
    return await diff_one(**{**kwargs, **changes})


# ── ported ───────────────────────────────────────────────────────────────────

async def test_diff_one_happy_path():
    fake = AsyncMock()
    fake.parse = AsyncMock(
        return_value=_fake_result(
            DiffResult(diff_narrative='이전 리포트 대비 ...')
        )
    )
    r, _, _ = await diff_one(
        client=fake, model='gpt-5.4-mini',
        prev_summary={}, curr_summary={},
        prev_match_type='same_publisher', prev_report_id=1,
        prev_publisher='X', curr_publisher='X', timeout_s=10,
    )
    assert isinstance(r, DiffResult)
    assert r.diff_narrative.startswith('이전')


async def test_diff_one_returns_null_narrative():
    fake = AsyncMock()
    fake.parse = AsyncMock(
        return_value=_fake_result(DiffResult(diff_narrative=None))
    )
    r, _, _ = await diff_one(
        client=fake, model='gpt-5.4-mini',
        prev_summary={}, curr_summary={},
        prev_match_type='cross_publisher', prev_report_id=1,
        prev_publisher='', curr_publisher='', timeout_s=10,
    )
    assert r.diff_narrative is None


# ── new: what is sent ────────────────────────────────────────────────────────

async def test_the_comparison_prompt_goes_with_the_constrained_schema():
    fake = AsyncMock()
    fake.parse = AsyncMock(return_value=_fake_result(DiffResult(diff_narrative='x')))
    result, tokens_in, tokens_out = await diff_one(
        client=fake, model='claude-haiku-5-5', prev_summary=PREVIOUS, curr_summary=CURRENT,
        prev_match_type='cross_publisher', prev_report_id=4, prev_publisher='KB',
        curr_publisher='NH', timeout_s=10,
    )
    assert result == DiffResult(diff_narrative='x')
    assert (tokens_in, tokens_out) == (1000, 200)
    system, user = render_diff_messages(PREVIOUS, CURRENT, 'cross_publisher', 4, 'KB', 'NH')
    fake.parse.assert_awaited_once_with(model='claude-haiku-5-5', system=system['content'],
                                        user=user['content'], schema=DiffResult, constrained=True)


# ── new: retry once (spec §9.3) ──────────────────────────────────────────────

async def test_a_transient_error_is_tried_once_more_after_5_seconds(monkeypatch):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(core_llm.asyncio, 'sleep', fake_sleep)
    client = _client(TransientLLMError('rate limit'), _fake_result(DiffResult(diff_narrative='ok')))
    result, _, _ = await diff_one(
        client=client, model='gpt-5.4-mini', prev_summary=PREVIOUS, curr_summary=CURRENT,
        prev_match_type='same_publisher', prev_report_id=1, prev_publisher='KB',
        curr_publisher='KB', timeout_s=10,   # default backoff
    )
    assert result.diff_narrative == 'ok'
    assert len(client.calls) == 2
    assert sleeps == [5.0]
    assert client.calls[0] == client.calls[1]   # the same request again


async def test_a_timeout_counts_as_transient():
    # each try has its own limit: the first one is too slow, the second one answers at once
    client = _client(0.5, _fake_result(DiffResult(diff_narrative='두 번째 시도')))
    result, _, _ = await _diff(client, timeout_s=0.05)
    assert result.diff_narrative == '두 번째 시도'
    assert len(client.calls) == 2


@pytest.mark.parametrize('first, second', [
    (TransientLLMError('rate limit'), TransientLLMError('still limited')),
    (0.5, 0.5),
    (TransientLLMError('rate limit'), 0.5),
], ids=['transient twice', 'timeout twice', 'transient then timeout'])
async def test_a_second_transient_failure_fails(first, second):
    client = _client(first, second, _fake_result(DiffResult(diff_narrative='never')))
    with pytest.raises(TransientLLMError):
        await _diff(client, timeout_s=0.05)
    assert len(client.calls) == 2   # one retry only


async def test_other_errors_fail_at_once():
    client = _client(ValueError('bad request'), _fake_result(DiffResult(diff_narrative='never')))
    with pytest.raises(ValueError, match='bad request'):
        await _diff(client)
    assert len(client.calls) == 1


async def test_a_refusal_is_an_error():
    client = _client(StructuredResult(parsed=None, refusal='refusal (cyber): x'))
    with pytest.raises(RuntimeError, match='거부'):
        await _diff(client)
    assert len(client.calls) == 1


# ── the real schema through core.llm's provider paths ────────────────────────

async def test_real_schema_through_the_constrained_anthropic_path():
    expected = DiffResult(diff_narrative='KB는 2026년 영업이익 추정을 상향했다.')
    reply = SimpleNamespace(
        content=[SimpleNamespace(type='thinking', thinking='...'),
                 SimpleNamespace(type='text', text=expected.model_dump_json())],
        stop_reason='end_turn', stop_details=None,
        usage=SimpleNamespace(input_tokens=10, output_tokens=20,
                              cache_creation_input_tokens=0, cache_read_input_tokens=5),
    )
    client = LLMClient(anthropic_api_key='sk-ant-test', effort='low')
    client._anthropic = MagicMock()
    client._anthropic.messages.create = AsyncMock(return_value=reply)

    result, tokens_in, tokens_out = await _diff(client, model='claude-haiku-5-5')

    assert result == expected
    assert (tokens_in, tokens_out) == (15, 20)
    kwargs = client._anthropic.messages.create.call_args.kwargs
    # constrained: the real DiffResult goes through anthropic.transform_schema
    assert kwargs['output_config'] == {
        'effort': 'low',
        'format': {'type': 'json_schema', 'schema': anthropic.transform_schema(DiffResult)},
    }
    system, user = render_diff_messages(PREVIOUS, CURRENT, 'same_publisher', 1, 'KB', 'KB')
    assert kwargs['system'][0]['text'] == system['content']   # no schema text appended
    assert kwargs['messages'] == [{'role': 'user', 'content': user['content']}]


async def test_real_schema_through_the_codex_strict_schema(monkeypatch):
    expected = DiffResult(diff_narrative='KB는 상향, NH는 유지.')
    calls = {}

    async def fake_run(cmd, prompt, cwd):
        with open(cmd[cmd.index('--output-schema') + 1], encoding='utf-8') as f:
            calls['schema'] = json.loads(f.read())
        calls['prompt'] = prompt
        with open(cmd[cmd.index('-o') + 1], 'w', encoding='utf-8') as f:
            f.write(expected.model_dump_json())
        return '{"type":"turn.completed","usage":{"input_tokens":3000,"output_tokens":400}}', '', 0

    monkeypatch.setattr(core_llm, '_run_codex', fake_run)
    monkeypatch.setattr(core_llm, '_codex_bin', lambda: 'codex')

    result, tokens_in, tokens_out = await _diff(LLMClient(), model='codex:gpt-6-luna',
                                                prev_match_type='cross_publisher',
                                                curr_publisher='NH')

    assert result == expected
    assert (tokens_in, tokens_out) == (3000, 400)
    schema = calls['schema']
    assert schema == openai.pydantic_function_tool(DiffResult)['function']['parameters']
    assert schema['additionalProperties'] is False
    assert schema['required'] == list(schema['properties']) == ['diff_narrative']
    system, user = render_diff_messages(PREVIOUS, CURRENT, 'cross_publisher', 1, 'KB', 'NH')
    assert calls['prompt'] == f"{system['content']}\n\n{user['content']}"
