"""extract_one: the analysis AI call (clients mocked, no network).

Ported from the extract tests of langgraph_tagger/analytics/llm_summary/tests/test_llm.py
(the diff tests moved with diff_one to compare), plus the real ExtractionResult
schema run through core.llm's unconstrained Anthropic path and Codex strict schema.
"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from research_desk.core import llm as core_llm
from research_desk.core.llm import LLMClient, StructuredResult, TransientLLMError
from research_desk.features.analysis.llm import extract_one
from research_desk.features.analysis.prompts import render_extraction_messages
from research_desk.features.analysis.schemas import ExtractionResult


def _fake_result(parsed_obj):
    return StructuredResult(parsed=parsed_obj, input_tokens=1000, output_tokens=200)


def _valid_extraction_obj():
    return ExtractionResult(
        target_price_new=85000, target_price_old=70000,
        target_price_dir='상향', recommendation='매수',
        recommendation_dir='유지',
        one_line_summary='메모리 가격 반등으로 25년 영업이익 ...',
        positive_points=['포인트 1', '포인트 2'],
        risk_points=['리스크 1'],
        target_price_raw='8.5만원', recommendation_raw='Buy',
        source_pages=[1], extraction_confidence='high',
    )


def _full_extraction_obj():
    """Every nested part of the schema filled in (financial_details included)."""
    evidence = {'page': 2, 'quote': '2026F 영업이익 1,234'}
    return ExtractionResult.model_validate({
        'financial_details': {
            'metrics': [{
                'metric': '영업이익', 'fiscal_period': '2026', 'value': 1234,
                'previous_value': 1100, 'unit': '십억원', 'currency': 'KRW',
                'accounting_basis': '연결', 'value_type': '추정', 'scenario': '기본',
                'evidence': evidence,
                'previous_evidence': {'page': 2, 'quote': '수정 전 1,100 수정 후 1,234'},
            }],
            'valuation': {
                'method': 'PER', 'target_horizon': '12개월',
                'explanation': '2027F EPS에 Target PER 15배 적용',
                'assumptions': [{'name': 'Target PER', 'current': '15배', 'previous': None,
                                 'fiscal_period': '2027', 'evidence': evidence}],
                'change_drivers': [{'category': '실적 추정 변경', 'explanation': '이익 상향',
                                    'evidence': evidence, 'previous_basis': None,
                                    'current_basis': None}],
            },
            'theses': [{'claim': 'HBM 확대', 'mechanism': 'HBM 비중 확대 → ASP 상승',
                        'support_type': '애널리스트 추정', 'monitoring_metric': 'HBM 매출액',
                        'invalidation_condition': None, 'invalidation_basis': '미기재',
                        'evidence': evidence}],
            'catalysts': [{'event': '실적 발표', 'expected_timing': '7월', 'condition': None,
                           'evidence': evidence}],
            'rating': {'current_label': 'Buy', 'previous_label': 'Buy', 'definition': None,
                       'horizon': '12개월', 'evidence': None},
        },
        'target_price_new': 85000, 'target_price_old': 70000, 'target_price_dir': '상향',
        'recommendation': '매수', 'recommendation_dir': '유지',
        'one_line_summary': '목표주가 상향: 메모리 가격 반등', 'positive_points': ['포인트'],
        'risk_points': [], 'target_price_raw': '8.5만원', 'recommendation_raw': 'Buy',
        'source_pages': [2, 1], 'extraction_confidence': 'high',
    })


async def test_extract_one_happy_path():
    fake = AsyncMock()
    fake.parse = AsyncMock(
        return_value=_fake_result(_valid_extraction_obj())
    )
    r, tokens_in, tokens_out = await extract_one(
        client=fake, model='gpt-5.4-mini',
        metadata={'publisher': 'X'}, pages_text='--- Page 1 ---\nbody',
        timeout_s=10,
    )
    assert isinstance(r, ExtractionResult)
    assert r.target_price_new == 85000
    assert tokens_in == 1000
    assert tokens_out == 200


async def test_extract_one_retry_on_transient():
    """첫 호출 transient fail, 두 번째는 성공 → 결과 반환."""
    call_count = {'n': 0}
    async def flaky_parse(**kwargs):
        call_count['n'] += 1
        if call_count['n'] == 1:
            raise TransientLLMError("rate limit")
        return _fake_result(_valid_extraction_obj())
    fake = AsyncMock()
    fake.parse = flaky_parse
    r, _, _ = await extract_one(
        client=fake, model='gpt-5.4-mini',
        metadata={}, pages_text='', timeout_s=10, backoff_s=0,  # 빠른 테스트
    )
    assert isinstance(r, ExtractionResult)
    assert call_count['n'] == 2


async def test_extract_one_permanent_fail_raises():
    """재시도 1회 후도 fail → TransientLLMError 그대로 raise."""
    calls = []
    async def always_fail(**kwargs):
        calls.append(1)
        raise TransientLLMError("persistent")
    fake = AsyncMock()
    fake.parse = always_fail
    with pytest.raises(TransientLLMError):
        await extract_one(
            client=fake, model='gpt-5.4-mini',
            metadata={}, pages_text='', timeout_s=10, backoff_s=0,
        )
    assert len(calls) == 2  # one retry only


async def test_extract_one_waits_backoff_before_the_retry(monkeypatch):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(core_llm.asyncio, 'sleep', fake_sleep)
    results = [TransientLLMError('rate limit'), _fake_result(_valid_extraction_obj())]

    async def parse(**kwargs):
        item = results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    fake = AsyncMock()
    fake.parse = parse
    await extract_one(client=fake, model='gpt-5.4-mini', metadata={}, pages_text='', timeout_s=10)
    assert sleeps == [5.0]  # default backoff: 5 seconds


async def test_extract_one_retries_on_wait_for_timeout():
    """asyncio.wait_for timeout이 transient로 분류되어 재시도."""
    call_count = {'n': 0}
    async def slow_then_succeeds(**kwargs):
        call_count['n'] += 1
        if call_count['n'] == 1:
            # Simulate a hang that wait_for would catch
            await asyncio.sleep(0.05)  # > timeout_s=0.01
            return _fake_result(_valid_extraction_obj())
        return _fake_result(_valid_extraction_obj())
    fake = AsyncMock()
    fake.parse = slow_then_succeeds
    r, _, _ = await extract_one(
        client=fake, model='gpt-5.4-mini',
        metadata={}, pages_text='',
        timeout_s=0.01,  # First call exceeds; second is instant after sleep ended
        backoff_s=0,
    )
    assert isinstance(r, ExtractionResult)
    assert call_count['n'] == 2  # retried


async def test_extract_one_passes_system_and_user_to_client():
    fake = AsyncMock()
    fake.parse = AsyncMock(return_value=_fake_result(_valid_extraction_obj()))
    await extract_one(
        client=fake, model='claude-haiku-5-5',
        metadata={'publisher': 'X'}, pages_text='--- Page 1 ---\nbody',
        timeout_s=10,
    )
    kwargs = fake.parse.call_args.kwargs
    assert kwargs['model'] == 'claude-haiku-5-5'
    assert kwargs['schema'] is ExtractionResult
    assert kwargs['constrained'] is False
    assert '<report_pages>' in kwargs['user']
    assert '<report_pages>' not in kwargs['system']
    system, user = render_extraction_messages({'publisher': 'X'}, '--- Page 1 ---\nbody')
    assert (kwargs['system'], kwargs['user']) == (system['content'], user['content'])


async def test_extract_one_refusal_raises():
    fake = AsyncMock()
    fake.parse = AsyncMock(return_value=StructuredResult(parsed=None, refusal='refusal (cyber): x'))
    with pytest.raises(RuntimeError, match='거부'):
        await extract_one(
            client=fake, model='claude-haiku-5-5',
            metadata={}, pages_text='', timeout_s=10,
        )


# ── the real schema through core.llm's provider paths ────────────────────────

async def test_real_schema_through_the_unconstrained_anthropic_path():
    expected = _full_extraction_obj()
    reply = SimpleNamespace(
        content=[SimpleNamespace(type='text', text=f"```json\n{expected.model_dump_json()}\n```")],
        stop_reason='end_turn', stop_details=None,
        usage=SimpleNamespace(input_tokens=10, output_tokens=20,
                              cache_creation_input_tokens=0, cache_read_input_tokens=5),
    )
    client = LLMClient(anthropic_api_key='sk-ant-test', effort='low')
    client._anthropic = MagicMock()
    client._anthropic.messages.create = AsyncMock(return_value=reply)

    result, tokens_in, tokens_out = await extract_one(
        client=client, model='claude-haiku-5-5', metadata={'publisher': 'KB'},
        pages_text='--- Page 1 ---\nbody\n', timeout_s=10,
    )

    assert result == expected
    assert (tokens_in, tokens_out) == (15, 20)
    kwargs = client._anthropic.messages.create.call_args.kwargs
    assert kwargs['output_config'] == {'effort': 'low'}  # no grammar: constrained=False
    prompt = render_extraction_messages({'publisher': 'KB'}, '--- Page 1 ---\nbody\n')[0]['content']
    schema = json.dumps(ExtractionResult.model_json_schema(), ensure_ascii=False)
    assert kwargs['system'][0]['text'] == (
        f"{prompt}\n\n<output_format>\n"
        "Reply with one JSON object only (no prose, no code fence) that "
        f"validates against this JSON Schema:\n{schema}\n</output_format>"
    )
    assert '"$defs"' in schema and '"FinancialDetails"' in schema  # the real, nested schema
    assert '"상향"' in schema  # Korean enum values embedded unescaped


async def test_real_schema_through_the_codex_strict_schema(monkeypatch):
    expected = _full_extraction_obj()
    calls = {}

    async def fake_run(cmd, prompt, cwd):
        with open(cmd[cmd.index('--output-schema') + 1], encoding='utf-8') as f:
            calls['schema'] = json.loads(f.read())
        calls['prompt'] = prompt
        with open(cmd[cmd.index('-o') + 1], 'w', encoding='utf-8') as f:
            f.write(expected.model_dump_json())
        return '{"type":"turn.completed","usage":{"input_tokens":26000,"output_tokens":9000}}', '', 0

    monkeypatch.setattr(core_llm, '_run_codex', fake_run)
    monkeypatch.setattr(core_llm, '_codex_bin', lambda: 'codex')
    monkeypatch.delenv('CODEX_REASONING_EFFORT', raising=False)

    result, tokens_in, tokens_out = await extract_one(
        client=LLMClient(), model='codex:gpt-6-luna', metadata={}, pages_text='--- Page 1 ---\n',
        timeout_s=10,
    )

    assert result == expected
    assert (tokens_in, tokens_out) == (26000, 9000)
    schema = calls['schema']

    def objects(node):
        if isinstance(node, dict):
            if node.get('type') == 'object' and 'properties' in node:
                yield node
            for value in node.values():
                yield from objects(value)
        elif isinstance(node, list):
            for value in node:
                yield from objects(value)

    found = list(objects(schema))
    # top level + FinancialDetails and every nested model: all strict
    assert len(found) >= 10
    for obj in found:
        assert obj['additionalProperties'] is False
        assert set(obj['required']) == set(obj['properties'])
    assert set(schema['properties']) == set(ExtractionResult.model_fields)
    system, user = render_extraction_messages({}, '--- Page 1 ---\n')
    assert calls['prompt'] == f"{system['content']}\n\n{user['content']}"
