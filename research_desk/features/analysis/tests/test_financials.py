"""Financial details keep their meaning through extraction and number grounding.

Ported from langgraph_tagger/analytics/llm_summary/tests/test_financials.py
(the shape and grounding tests; the comparison tests moved with compare).
"""
import pytest
from pydantic import ValidationError

from research_desk.features.analysis.financials import (
    FinancialDetails, FinancialMetric, ground_metrics,
)
from research_desk.features.analysis.schemas import ExtractionResult


def metric(**changes):
    data = dict(metric='영업이익', fiscal_period='2026', value=120,
                previous_value=None, unit='십억원', currency='KRW',
                accounting_basis='연결', value_type='추정', scenario='기본',
                evidence={'page': 2, 'quote': '2026E 영업이익 120 (십억원)'})
    return {**data, **changes}


def financial_details():
    return {
        'metrics': [metric()],
        'valuation': {
            'method': 'PBR', 'target_horizon': '12개월',
            'explanation': '예상 BPS에 목표 PBR 적용',
            'assumptions': [{'name': 'PBR', 'current': '1.6배', 'previous': '1.4배',
                             'fiscal_period': '2026',
                             'evidence': {'page': 1, 'quote': 'Target PBR 1.6배'}}],
            'change_drivers': [{'category': '배수 변경', 'explanation': 'ROE 전망 반영',
                                'evidence': {'page': 1, 'quote': 'ROE 상승으로 배수 상향'}}],
        },
        'theses': [{'claim': '수익성 개선', 'mechanism': '수수료 증가 → 이익 개선',
                    'support_type': '애널리스트 추정', 'monitoring_metric': '수수료 수익',
                    'invalidation_condition': None,
                    'evidence': {'page': 1, 'quote': '수수료 수익 증가 예상'}}],
        'catalysts': [{'event': '실적 발표', 'expected_timing': '2026년 7월',
                       'condition': None,
                       'evidence': {'page': 1, 'quote': '7월 실적 발표'}}],
        'rating': {'current_label': 'Outperform', 'previous_label': 'Buy',
                   'definition': None, 'horizon': '6개월',
                   'evidence': {'page': 1, 'quote': 'Outperform(Downgrade)'}},
    }


def test_old_summary_without_details_is_compatible():
    # Analysis side of the old test: a saved summary from before financial
    # details still validates, with financial_details None. (The original
    # assertion — compare_financials gives no rows for it — moved with
    # compare_financials to the compare feature.)
    old_summary = {
        'target_price_new': 100, 'target_price_old': None, 'target_price_dir': '신규',
        'recommendation': '매수', 'recommendation_dir': '신규', 'one_line_summary': 'X',
        'positive_points': [], 'risk_points': [], 'target_price_raw': '100원',
        'source_pages': [1], 'extraction_confidence': 'high',
    }
    result = ExtractionResult.model_validate(old_summary)
    assert result.financial_details is None
    assert result.model_dump()['financial_details'] is None


def test_structured_details_preserve_rating_and_missing_invalidation():
    result = FinancialDetails.model_validate(financial_details())
    assert result.rating.current_label == 'Outperform'
    assert result.rating.previous_label == 'Buy'
    assert result.theses[0].invalidation_condition is None
    assert result.valuation.method == 'PBR'


def test_metric_requires_real_page_and_finite_number():
    for bad in [metric(value=float('inf')), metric(evidence={'page': 0, 'quote': 'X'})]:
        with pytest.raises(ValidationError):
            FinancialMetric.model_validate(bad)


def test_unsupported_current_and_previous_values_are_not_published():
    payload = financial_details()
    payload['metrics'] = [
        metric(value=6800, evidence={'page': 1, 'quote': 'DPS 6,300원'}),
        metric(value=120, previous_value=100),
        metric(value=2220, previous_value=2000,
               evidence={'page': 1, 'quote': '영업이익 2,220'},
               previous_evidence={'page': 2, 'quote': '이전 영업이익 2,000 → 2,220'}),
    ]
    result, omitted = ground_metrics(FinancialDetails.model_validate(payload),
        '--- Page 1 ---\nDPS 6,300원 영업이익 2,220\n'
        '--- Page 2 ---\n2026E 영업이익 120 (십억원) 이전 영업이익 2,000 → 2,220\n')
    assert omitted == 2
    assert [m.value for m in result.metrics] == [120, 2220]
    assert result.metrics[0].previous_value is None
    assert result.metrics[1].previous_value == 2000


def test_fabricated_quote_number_missing_from_page_is_excluded():
    payload = financial_details()
    result, omitted = ground_metrics(FinancialDetails.model_validate(payload),
                                     '--- Page 2 ---\n2026 영업이익 100')
    assert result.metrics == [] and omitted == 1


def test_forward_bvps_and_sustainable_roe_not_mixed_with_annual_estimates():
    payload = financial_details()
    payload['metrics'] = [
        metric(metric='BVPS', value=100, evidence={'page': 1, 'quote': '12M Fwd BVPS 100'}),
        metric(metric='ROE', value=19, unit='%', previous_value=11.2,
               evidence={'page': 1, 'quote': '2026 ROE 19'},
               previous_evidence={'page': 1, 'quote': 'Sustainable ROE 11.2 → 12.8'}),
    ]
    result, omitted = ground_metrics(FinancialDetails.model_validate(payload),
        '--- Page 1 ---\n12M Fwd BVPS 100. 2026 ROE 19. Sustainable ROE 11.2 → 12.8')
    assert len(result.metrics) == 1 and result.metrics[0].previous_value is None
    assert omitted == 2


def test_current_valuation_period_does_not_prove_rollover():
    payload = financial_details()
    payload['valuation']['change_drivers'] = [{
        'category': '평가기간 변경', 'explanation': '12M Fwd 적용',
        'evidence': {'page': 1, 'quote': '12M Fwd 적용'},
    }]
    result, _ = ground_metrics(FinancialDetails.model_validate(payload), '')
    assert result.valuation.change_drivers == []


def test_grounding_reads_pages_from_the_page_markers():
    # The number must be on the cited page: page markers split the text.
    payload = financial_details()
    payload['metrics'] = [metric(value=120, evidence={'page': 2, 'quote': '영업이익 120'})]
    details = FinancialDetails.model_validate(payload)
    on_other_page = '--- Page 1 ---\n영업이익 120\n--- Page 2 ---\n매출액 900\n'
    on_cited_page = '--- Page 1 ---\n매출액 900\n--- Page 2 ---\n영업이익 120\n'
    dropped, omitted = ground_metrics(details, on_other_page)
    assert dropped.metrics == [] and omitted == 1
    kept, omitted = ground_metrics(details, on_cited_page)
    assert [m.value for m in kept.metrics] == [120] and omitted == 0
