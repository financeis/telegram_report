"""Same-basis comparison of two reports' figures (metric_key, numeric_change, compare_financials).

Ported from langgraph_tagger/analytics/llm_summary/tests/test_financials.py, the comparison tests:
test_same_period_forecast_revision_is_calculated,
test_different_economic_basis_never_becomes_a_revision, test_ambiguous_or_missing_basis_not_compared,
test_ambiguous_duplicate_rows_not_silently_overwritten,
test_loss_and_zero_do_not_generate_misleading_growth, test_margin_change_uses_percentage_points, and
the original assertion of test_old_summary_without_details_is_compatible (its validation half stayed
with features/analysis).

New (spec §9.6): the matched row's exact content and order, the later report's metric order, a
metric on one side only, the seven-field key, growth rates and their labels around zero, losses and
ratios.
"""
import pytest

from research_desk.features.compare.logic import compare_financials, metric_key, numeric_change


def metric(**changes):
    data = dict(metric='영업이익', fiscal_period='2026', value=120,
                previous_value=None, unit='십억원', currency='KRW',
                accounting_basis='연결', value_type='추정', scenario='기본',
                evidence={'page': 2, 'quote': '2026E 영업이익 120 (십억원)'})
    return {**data, **changes}


def summary(*metrics):
    return {'financial_details': {'metrics': list(metrics)}}


# ── ported ───────────────────────────────────────────────────────────────────

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


# ── new: compare_financials ──────────────────────────────────────────────────

def test_a_matched_row_holds_both_values_the_change_and_both_citations():
    earlier = metric(value=100, evidence={'page': 3, 'quote': '2026E 영업이익 100'})
    (row,) = compare_financials(summary(earlier), summary(metric()))
    assert row == {
        'metric': '영업이익', 'fiscal_period': '2026', 'unit': '십억원', 'currency': 'KRW',
        'accounting_basis': '연결', 'value_type': '추정', 'scenario': '기본',
        'previous': 100, 'current': 120,
        'delta': 20, 'change_pct': 20.0, 'change_label': '+20.00%',
        'previous_evidence': {'page': 3, 'quote': '2026E 영업이익 100'},
        'current_evidence': {'page': 2, 'quote': '2026E 영업이익 120 (십억원)'},
    }
    assert list(row) == ['metric', 'fiscal_period', 'unit', 'currency', 'accounting_basis',
                         'value_type', 'scenario', 'previous', 'current', 'delta', 'change_pct',
                         'change_label', 'previous_evidence', 'current_evidence']


def test_rows_follow_the_later_reports_metric_order():
    earlier = summary(metric(value=100), metric(fiscal_period='2027', value=130))
    later = summary(metric(fiscal_period='2027', value=143), metric(value=110))
    rows = compare_financials(earlier, later)
    assert [(r['fiscal_period'], r['previous'], r['current']) for r in rows] == [
        ('2027', 130, 143), ('2026', 100, 110)]


def test_only_metrics_on_both_sides_are_compared():
    earlier = summary(metric(metric='매출액', value=1000), metric(metric='EBITDA', value=50))
    later = summary(metric(metric='매출액', value=1100), metric(metric='순이익', value=80))
    assert [r['metric'] for r in compare_financials(earlier, later)] == ['매출액']
    assert compare_financials(summary(), summary(metric())) == []
    assert compare_financials(summary(metric()), summary()) == []


@pytest.mark.parametrize('previous', [{}, {'financial_details': None}, {'financial_details': {}}],
                         ids=['no details key', 'details null', 'details without metrics'])
def test_a_summary_without_metrics_gives_no_rows(previous):
    assert compare_financials(previous, summary(metric())) == []
    assert compare_financials(summary(metric()), previous) == []


def test_a_duplicate_on_the_later_side_is_not_compared_either():
    assert compare_financials(summary(metric(value=100)), summary(metric(), metric(value=125))) == []


def test_a_missing_value_on_either_side_is_skipped():
    assert compare_financials(summary(metric(value=None)), summary(metric())) == []
    assert compare_financials(summary(metric(value=100)), summary(metric(value=None))) == []


# ── new: metric_key ──────────────────────────────────────────────────────────

def test_metric_key_is_the_seven_basis_fields():
    assert metric_key(metric()) == ('영업이익', '2026', '십억원', 'KRW', '연결', '추정', '기본')
    assert metric_key(metric(currency=None, unit='%')) == (
        '영업이익', '2026', '%', None, '연결', '추정', '기본')


@pytest.mark.parametrize('changes', [
    {'fiscal_period': None}, {'fiscal_period': ''}, {'accounting_basis': '미기재'},
    {'accounting_basis': None}, {'scenario': '미기재'}, {'scenario': None},
])
def test_metric_key_is_none_without_a_known_period_basis_and_scenario(changes):
    assert metric_key(metric(**changes)) is None


@pytest.mark.parametrize('basis', ['연결', '별도'])
@pytest.mark.parametrize('scenario', ['기본', '낙관', '비관'])
def test_every_known_basis_and_scenario_has_a_key(basis, scenario):
    assert metric_key(metric(accounting_basis=basis, scenario=scenario)) is not None


# ── new: numeric_change ──────────────────────────────────────────────────────

@pytest.mark.parametrize('previous, current', [(None, 1), (1, None), (None, None)])
def test_no_change_without_both_values(previous, current):
    assert numeric_change(previous, current, '원') == {
        'delta': None, 'change_pct': None, 'change_label': '비교값 없음'}


def test_growth_rate_between_positive_values():
    assert numeric_change(100, 120, '원') == {'delta': 20, 'change_pct': 20.0,
                                             'change_label': '+20.00%'}
    assert numeric_change(200, 150, '원')['change_label'] == '-25.00%'


@pytest.mark.parametrize('previous, current, label', [
    (-10, -5, '+5.00 억원 (증감률 미표시)'),
    (0, 0, '+0.00 억원 (증감률 미표시)'),
    (10, 0, '-10.00 억원 (증감률 미표시)'),
    (0, -20, '-20.00 억원 (증감률 미표시)'),
    (-1500000, -500, '+1,499,500.00 억원 (증감률 미표시)'),
])
def test_zero_or_both_negative_shows_the_difference_only(previous, current, label):
    assert numeric_change(previous, current, '억원', '영업이익') == {
        'delta': current - previous, 'change_pct': None, 'change_label': label}


@pytest.mark.parametrize('name', ['영업이익', '순이익', '지배주주순이익', '세전이익', 'EPS'])
def test_profit_metrics_turn_to_profit_or_loss(name):
    assert numeric_change(-10, 20, '억원', name)['change_label'] == '흑자 전환'
    assert numeric_change(10, -20, '억원', name)['change_label'] == '적자 전환'


def test_other_metrics_show_the_sign_change():
    assert numeric_change(10, -20, '억원', '순차입금')['change_label'] == '양수→음수'
    assert numeric_change(-10, 20, '억원')['change_label'] == '음수→양수'


def test_percent_units_use_points_even_around_zero():
    assert numeric_change(-1.5, 2.0, '%', '영업이익률') == {
        'delta': 3.5, 'change_pct': None, 'change_label': '+3.50%p'}
