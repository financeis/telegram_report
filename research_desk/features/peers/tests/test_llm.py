"""extract_profile: one company's AI extraction with grounding and one escalation (spec §5.4)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from research_desk.core.llm import StructuredResult, TransientLLMError
from research_desk.features.peers import llm, logic
from research_desk.features.peers.schemas import CompanyProfile

from .fakes import FakeLLM

SYN = logic.parse_synonyms({'DRAM': ['디램', 'D램']})
TEXT = '[사업의 개요]\n당사는 Legacy DRAM, NOR Flash, MCP, eMMC를 설계해 판매합니다.'
GOOD = ['Legacy DRAM', 'NOR Flash', 'MCP', 'eMMC']


def profile(keywords, **fields) -> CompanyProfile:
    base = dict(niche_industry='레거시 메모리 팹리스', summary='메모리를 설계해 판다', roles=['설계(팹리스)'],
                segments=[], products=[], keywords=list(keywords), applications=[], customers=[],
                competitors=[], is_holding=False, is_financial=False, info_quality='충분')
    return CompanyProfile(**(base | fields))


def invalid() -> ValidationError:
    try:
        CompanyProfile.model_validate({})
    except ValidationError as exc:
        return exc


async def extract(client, text=TEXT):
    return await llm.extract_profile(text, client=client, model='claude-haiku-5-5',
                                     escalation_model='gpt-5.4', synonyms=SYN, corp_name='가나반도체',
                                     backoff_s=0)


async def test_four_grounded_of_five_is_0_8_and_needs_no_escalation():
    client = FakeLLM(lambda model, user: profile(GOOD + ['HBM']))
    result = await extract(client)
    assert result.profile.keywords == GOOD
    assert result.grounding_ratio == pytest.approx(0.8)
    assert result.model == 'claude-haiku-5-5'
    assert result.tokens == (100, 10)
    assert len(client.parse_calls) == 1
    call = client.parse_calls[0]
    assert call['model'] == 'claude-haiku-5-5' and call['schema'] is CompanyProfile
    assert call['constrained'] is True
    assert '가나반도체' in call['user'] and TEXT in call['user']


async def test_below_0_8_the_escalation_model_extracts_once_more_and_its_result_is_used():
    replies = {'claude-haiku-5-5': profile(GOOD[:3] + ['HBM']),        # 3/4 = 0.75
               'gpt-5.4': profile(GOOD + ['HBM'])}                       # 4/5 = 0.8
    client = FakeLLM(lambda model, user: replies[model])
    result = await extract(client)
    assert [c['model'] for c in client.parse_calls] == ['claude-haiku-5-5', 'gpt-5.4']
    assert result.model == 'gpt-5.4'
    assert result.profile.keywords == GOOD
    assert result.grounding_ratio == pytest.approx(0.8)
    assert result.tokens == (200, 20)          # both calls


async def test_a_second_low_ratio_keeps_the_escalated_result_and_records_its_ratio():
    replies = {'claude-haiku-5-5': profile(['Legacy DRAM', 'HBM', 'GDDR']),   # 1/3
               'gpt-5.4': profile(['NOR Flash', 'HBM'])}                       # 1/2
    client = FakeLLM(lambda model, user: replies[model])
    result = await extract(client)
    assert len(client.parse_calls) == 2
    assert (result.model, result.profile.keywords) == ('gpt-5.4', ['NOR Flash'])
    assert result.grounding_ratio == pytest.approx(0.5)


async def test_a_failed_escalation_falls_back_to_the_first_result():
    def reply(model, user):
        return profile(['Legacy DRAM', 'HBM']) if model == 'claude-haiku-5-5' else RuntimeError('down')
    result = await extract(FakeLLM(reply))
    assert (result.model, result.profile.keywords) == ('claude-haiku-5-5', ['Legacy DRAM'])
    assert result.grounding_ratio == pytest.approx(0.5)


async def test_a_format_error_is_retried_once_with_the_same_model():
    answers = iter([invalid(), profile(GOOD)])
    client = FakeLLM(lambda model, user: next(answers))
    result = await extract(client)
    assert [c['model'] for c in client.parse_calls] == ['claude-haiku-5-5'] * 2
    assert result.profile.keywords == GOOD


async def test_two_format_errors_fail_the_extraction():
    client = FakeLLM(lambda model, user: invalid())
    with pytest.raises(ValidationError):
        await extract(client)
    assert len(client.parse_calls) == 2


async def test_a_refusal_fails_the_extraction():
    client = FakeLLM(lambda model, user: StructuredResult(parsed=None, refusal='no'))
    with pytest.raises(RuntimeError, match='거부'):
        await extract(client)


async def test_a_transient_error_is_retried_once_then_fails():
    client = FakeLLM(lambda model, user: TimeoutError())
    with pytest.raises(TransientLLMError):
        await extract(client)
    assert len(client.parse_calls) == 2


async def test_the_reply_is_capped_after_grounding():
    many = [f'MCP{i}' for i in range(20)]
    text = TEXT + ' ' + ' '.join(many)
    client = FakeLLM(lambda model, user: profile(many, products=many))
    result = await extract(client, text)
    assert result.profile.keywords == many[:15]
    assert result.profile.products == many[:12]
    assert result.grounding_ratio == 1.0


async def test_no_terms_at_all_is_a_full_ratio():
    client = FakeLLM(lambda model, user: profile([], info_quality='부족'))
    result = await extract(client)
    assert result.grounding_ratio == 1.0 and len(client.parse_calls) == 1
