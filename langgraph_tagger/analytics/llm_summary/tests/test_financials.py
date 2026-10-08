"""Financial meaning survives extraction, comparisons, persistence and display."""
import pytest
from pydantic import ValidationError

from langgraph_tagger.analytics.llm_summary.financials import (
    FinancialDetails, FinancialMetric, compare_financials, numeric_change, ground_metrics,
)


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


def summary(*metrics):
    return {'financial_details': {'metrics': list(metrics)}}


def test_same_period_forecast_revision_is_calculated():
    rows = compare_financials(summary(metric(value=100)), summary(metric()))
    assert len(rows) == 1
    assert rows[0]['change_pct'] == 20
    assert rows[0]['previous_evidence']['page'] == 2


@pytest.mark.parametrize('changes', [
    {'fiscal_period': '2027'}, {'fiscal_period': '2026Q1'},
    {'unit': '억원'}, {'currency': 'USD'}, {'accounting_basis': '별도'},
    {'value_type': '실적'}, {'scenario': '낙관'}, {'metric': '매출액'},
])
def test_different_economic_basis_never_becomes_a_revision(changes):
    assert compare_financials(summary(metric(value=100)), summary(metric(**changes))) == []


@pytest.mark.parametrize('changes', [
    {'fiscal_period': None}, {'accounting_basis': '미기재'}, {'scenario': '미기재'},
    {'value': None},
])
def test_ambiguous_or_missing_basis_not_compared(changes):
    assert compare_financials(summary(metric(**changes)), summary(metric(**changes))) == []


def test_ambiguous_duplicate_rows_not_silently_overwritten():
    assert compare_financials(summary(metric(value=100), metric(value=90)), summary(metric())) == []


def test_loss_and_zero_do_not_generate_misleading_growth():
    assert numeric_change(-10, 20, '억원', '영업이익')['change_label'] == '흑자 전환'
    assert numeric_change(10, -20, '억원', '영업이익')['change_label'] == '적자 전환'
    assert numeric_change(0, 20, '억원')['change_pct'] is None
    assert numeric_change(-10, 20, '억원', '순차입금')['change_label'] == '음수→양수'


def test_margin_change_uses_percentage_points():
    result = numeric_change(10, 12, '%', '영업이익률')
    assert result['change_pct'] is None
    assert result['change_label'] == '+2.00%p'


def test_old_summary_without_details_is_compatible():
    assert compare_financials({'target_price_new': 100}, summary(metric())) == []


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
