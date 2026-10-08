"""The pair layout: which two reports can be compared, in which order, and what is shown.

Ported from langgraph_tagger/workspace/tests/test_workspace.py, the four comparison tests:
test_comparison_orders_chronologically_and_uses_exact_narrative,
test_comparison_rejects_same_document_and_different_company,
test_unknown_publishers_do_not_imply_same_desk,
test_target_price_change_uses_selected_reports_not_embedded_previous_target.

New (spec §6, §9.6): the three 422 texts and the order of the checks, a report without a type, one
shared code is enough, the tie-break on id, a report without a date, every publisher case, a
narrative saved on the earlier report, the direction of the figures and the exact result keys.
"""
import pytest
from fastapi import HTTPException

from research_desk.features.compare.logic import comparison

NOT_SINGLE = '금융 비교는 단일종목 보고서 두 개를 선택해 주세요.'
SAME_REPORT = '서로 다른 보고서 두 개를 선택해 주세요.'
OTHER_COMPANY = '같은 기업의 보고서를 선택해 주세요.'


def report(rid, published, publisher='KB', code='016360', summary=None):
    return {'id': rid, 'published_at': published, 'publisher': publisher,
            'stock_codes': [code], 'summary': summary}


def rejected(left, right):
    with pytest.raises(HTTPException) as exc:
        comparison(left, right)
    assert exc.value.status_code == 422
    return exc.value.detail


# ── ported ───────────────────────────────────────────────────────────────────

def test_comparison_orders_chronologically_and_uses_exact_narrative():
    old = report(1, '2026-02-09')
    new = report(2, '2026-05-11', summary={'prev_report_id': 1, 'diff_narrative': '실적 추정 상향'})
    result = comparison(new, old)
    assert result['left']['id'] == 1
    assert result['right']['id'] == 2
    assert result['same_publisher'] is True
    assert result['narrative'] == '실적 추정 상향'
    # A third report must not inherit an unrelated pair's saved narrative.
    other = report(3, '2026-03-01', publisher='NH')
    result = comparison(other, new)
    assert result['narrative'] is None
    assert result['same_publisher'] is False


@pytest.mark.parametrize('other', [report(1, '2026-02-09'), report(2, '2026-05-11', code='005930')])
def test_comparison_rejects_same_document_and_different_company(other):
    with pytest.raises(HTTPException) as exc:
        comparison(report(1, '2026-02-09'), other)
    assert exc.value.status_code == 422


def test_unknown_publishers_do_not_imply_same_desk():
    assert comparison(report(1, '2026-01-01', publisher=None), report(2, '2026-02-01', publisher=None))['same_publisher'] is False


def test_target_price_change_uses_selected_reports_not_embedded_previous_target():
    old = report(1, '2026-02-09', summary={'target_price_new': 123000})
    new = report(2, '2026-05-11', summary={'target_price_new': 165000, 'target_price_old': 133000})
    change = comparison(old, new)['target_price_change']
    assert change['delta'] == 42000
    assert change['change_pct'] == pytest.approx(34.14634146)
    assert comparison(old, report(3, '2026-05-12'))['target_price_change'] is None


# ── new: which pairs ─────────────────────────────────────────────────────────

def test_the_422_texts():
    assert rejected(report(1, '2026-02-09'), report(1, '2026-02-09')) == SAME_REPORT
    assert rejected(report(1, '2026-02-09'), report(2, '2026-05-11', code='005930')) == OTHER_COMPANY


@pytest.mark.parametrize('report_type', ['산업', '섹터', 'IR자료', '전략·시황', '기타'])
@pytest.mark.parametrize('side', ['left', 'right'])
def test_only_single_company_reports_are_compared(report_type, side):
    pair = {'left': report(1, '2026-02-09'), 'right': report(2, '2026-05-11')}
    pair[side]['report_type'] = report_type
    assert rejected(pair['left'], pair['right']) == NOT_SINGLE


@pytest.mark.parametrize('report_type', [None, '단일종목'])
def test_a_report_without_a_type_counts_as_single_company(report_type):
    result = comparison(report(1, '2026-02-09') | {'report_type': report_type},
                        report(2, '2026-05-11') | {'report_type': '단일종목'})
    assert (result['left']['id'], result['right']['id']) == (1, 2)


def test_the_checks_run_in_order_type_then_same_report_then_company():
    industry = report(1, '2026-02-09') | {'report_type': '산업'}
    assert rejected(industry, industry) == NOT_SINGLE
    assert rejected(industry, report(2, '2026-05-11', code='005930')) == NOT_SINGLE
    assert rejected(report(1, '2026-02-09'), report(1, '2026-05-11', code='005930')) == SAME_REPORT


def test_one_shared_code_is_enough():
    left = report(1, '2026-02-09') | {'stock_codes': ['016360', '005930']}
    right = report(2, '2026-05-11') | {'stock_codes': ['005930', '000660']}
    assert comparison(left, right)['right']['id'] == 2


@pytest.mark.parametrize('codes', [None, []], ids=['null', 'empty'])
def test_reports_without_codes_are_not_the_same_company(codes):
    left = report(1, '2026-02-09') | {'stock_codes': codes}
    right = report(2, '2026-05-11') | {'stock_codes': codes}
    assert rejected(left, right) == OTHER_COMPANY


# ── new: order and publishers ────────────────────────────────────────────────

def test_reports_of_the_same_day_are_ordered_by_id():
    result = comparison(report(7, '2026-05-11'), report(3, '2026-05-11'))
    assert (result['left']['id'], result['right']['id']) == (3, 7)


def test_a_report_without_a_date_comes_first():
    result = comparison(report(1, '2026-05-11'), report(2, None))
    assert (result['left']['id'], result['right']['id']) == (2, 1)


@pytest.mark.parametrize('earlier, later, same', [
    ('KB', 'KB', True), ('KB', 'NH', False), ('KB', None, False), (None, 'KB', False),
    (None, None, False), ('', '', False),
])
def test_the_same_publisher_needs_both_publishers_and_equal(earlier, later, same):
    result = comparison(report(2, '2026-05-11', publisher=later),
                        report(1, '2026-02-09', publisher=earlier))
    assert result['same_publisher'] is same


# ── new: what is shown ───────────────────────────────────────────────────────

def test_the_result_keys_and_the_reports_as_given():
    old = report(1, '2026-02-09', summary={'target_price_new': 100})
    new = report(2, '2026-05-11', summary={'target_price_new': 110})
    result = comparison(new, old)
    assert list(result) == ['left', 'right', 'same_publisher', 'metrics', 'narrative',
                            'target_price_change']
    assert result['left'] == old and result['right'] == new


def test_reports_without_a_saved_analysis_compare_to_nothing():
    result = comparison(report(1, '2026-02-09'), report(2, '2026-05-11'))
    assert (result['metrics'], result['narrative'], result['target_price_change']) == ([], None, None)


def test_a_narrative_saved_on_the_earlier_report_is_not_shown():
    old = report(1, '2026-02-09', summary={'prev_report_id': 2, 'diff_narrative': '반대 방향'})
    new = report(2, '2026-05-11', summary={'target_price_new': 100})
    assert comparison(old, new)['narrative'] is None


def test_the_figures_go_from_the_earlier_report_to_the_later_one():
    def analysed(value):
        return {'financial_details': {'metrics': [{
            'metric': '영업이익', 'fiscal_period': '2026', 'value': value, 'unit': '십억원',
            'currency': 'KRW', 'accounting_basis': '연결', 'value_type': '추정',
            'scenario': '기본', 'evidence': {'page': 1, 'quote': f'영업이익 {value}'}}]}}

    old = report(1, '2026-02-09', summary=analysed(100))
    new = report(2, '2026-05-11', summary=analysed(120))
    for pair in ((old, new), (new, old)):
        (row,) = comparison(*pair)['metrics']
        assert (row['previous'], row['current'], row['change_label']) == (100, 120, '+20.00%')


def test_the_target_price_change_is_in_won():
    old = report(1, '2026-02-09', summary={'target_price_new': 100000})
    new = report(2, '2026-05-11', summary={'target_price_new': 90000})
    assert comparison(new, old)['target_price_change'] == {
        'delta': -10000, 'change_pct': -10.0, 'change_label': '-10.00%'}
    assert comparison(old, report(3, '2026-05-12', summary={'target_price_new': None}))[
        'target_price_change'] is None
