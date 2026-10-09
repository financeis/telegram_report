"""The AI reply shape (spec §5.2) and the extraction prompt (§5.3)."""
from __future__ import annotations

import json
import typing

import anthropic
import openai
import pytest
from pydantic import ValidationError

from research_desk.features.peers import prompts
from research_desk.features.peers.schemas import ROLES, CompanyProfile, Segment


def test_roles_are_the_thirteen_of_the_spec():
    assert ROLES == ("소재", "부품", "장비", "설계(팹리스)", "완제품 제조", "위탁생산(OEM/ODM/CDMO)",
                     "패키징·테스트", "유통", "서비스·플랫폼", "건설·EPC", "금융", "지주·투자", "기타")
    role = typing.get_args(CompanyProfile.model_fields['roles'].annotation)[0]
    assert typing.get_args(role) == ROLES


def test_the_profile_has_the_spec_fields_in_order():
    assert list(CompanyProfile.model_fields) == [
        'niche_industry', 'summary', 'roles', 'segments', 'products', 'keywords', 'applications',
        'customers', 'competitors', 'is_holding', 'is_financial', 'info_quality']
    assert list(Segment.model_fields) == ['name', 'products', 'keywords', 'revenue_share_pct']


def test_count_and_length_limits_are_not_in_the_schema():
    """Caps are cut in code after validation (spec §5.2), so an over-long reply still validates."""
    text = json.dumps(CompanyProfile.model_json_schema(), ensure_ascii=False)
    for word in ('maxItems', 'maxLength', 'minItems', 'minLength'):
        assert word not in text
    CompanyProfile(niche_industry='가' * 100, summary='나' * 500, roles=[], segments=[],
                   products=['x'] * 30, keywords=['y'] * 30, applications=[], customers=[],
                   competitors=[], is_holding=False, is_financial=False, info_quality='부족')


def test_nulls_are_refused_lists_and_minus_one_stand_for_missing():
    with pytest.raises(ValidationError):
        Segment(name='A', products=None, keywords=[], revenue_share_pct=-1)
    with pytest.raises(ValidationError):
        Segment(name='A', products=[], keywords=[], revenue_share_pct=None)
    with pytest.raises(ValidationError):
        CompanyProfile.model_validate({'niche_industry': 'x'})


def test_the_schema_converts_for_the_openai_strict_and_anthropic_grammar_paths():
    strict = openai.pydantic_function_tool(CompanyProfile)['function']['parameters']
    assert strict['additionalProperties'] is False
    assert set(strict['required']) == set(CompanyProfile.model_fields)
    anthropic.transform_schema(CompanyProfile)


def test_the_prompt_carries_the_extraction_rules():
    system = prompts.SYSTEM
    for rule in ('산업의 특성', '시장 규모', '성장성', '경기 변동', '정책', '추론하지 않', '반도체', '기술력',
                 '글로벌', '친환경', '수요', 'AI', 'DRAM, HBM, MLCC', '부족', '-1', '테스트 핸들러'):
        assert rule in system, rule
    assert system.count('가상 회사') >= 1


def test_the_user_message_holds_the_company_name_and_the_input():
    system, user = prompts.render_messages('가나반도체', '[사업의 개요]\n본문')
    assert system == prompts.SYSTEM
    assert '가나반도체' in user and '[사업의 개요]\n본문' in user
