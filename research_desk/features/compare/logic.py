"""Comparison of two reports' saved analyses (no DB, no network; spec §9.6).

- ``comparison(left, right)``: which pairs can be compared (both 단일종목 or without a type, two
  different reports, at least one shared stock code — else 422), the earlier report as ``left``
  (``published_at``, then ``id``), the same publisher only when both are known and equal, the
  figure comparison, the target-price change from both reports' ``target_price_new``, and the
  narrative saved on the later report only when it was written for exactly this pair.
- ``compare_financials``: only unique estimates whose metric, period, unit, currency, accounting
  basis, value type and scenario all match; actuals are never compared.
- ``numeric_change``: no growth rate around zero or losses (흑자 전환 / 적자 전환 and the like);
  ratios in % change in points.

Moved unchanged: metric_key, numeric_change and compare_financials from the old
analytics/llm_summary/financials.py, comparison from the old workspace/service.py.
"""
from __future__ import annotations

from collections import Counter

from fastapi import HTTPException


def metric_key(metric: dict) -> tuple | None:
    """Unknown period/basis/scenario cannot establish like-for-like revisions."""
    if (not metric.get('fiscal_period')
            or metric.get('accounting_basis') not in ('연결', '별도')
            or metric.get('scenario') not in ('기본', '낙관', '비관')):
        return None
    return tuple(metric.get(k) for k in (
        'metric', 'fiscal_period', 'unit', 'currency',
        'accounting_basis', 'value_type', 'scenario',
    ))


def numeric_change(previous: float | None, current: float | None, unit: str,
                   metric: str = '') -> dict:
    """Avoid misleading growth rates around zero or losses; ratios use points."""
    if previous is None or current is None:
        return {'delta': None, 'change_pct': None, 'change_label': '비교값 없음'}
    delta = current - previous
    if unit == '%':
        return {'delta': delta, 'change_pct': None, 'change_label': f'{delta:+.2f}%p'}
    if previous <= 0 or current <= 0:
        is_profit = metric in ('영업이익', '순이익', '지배주주순이익', '세전이익', 'EPS')
        if previous < 0 < current:
            label = '흑자 전환' if is_profit else '음수→양수'
        elif previous > 0 > current:
            label = '적자 전환' if is_profit else '양수→음수'
        else:
            label = f'{delta:+,.2f} {unit} (증감률 미표시)'
        return {'delta': delta, 'change_pct': None, 'change_label': label}
    pct = delta / previous * 100
    return {'delta': delta, 'change_pct': pct, 'change_label': f'{pct:+.2f}%'}


def compare_financials(previous: dict, current: dict) -> list[dict]:
    """Match unique, comparable estimates. No FY rollover or silent unit mixing."""
    old = (previous.get('financial_details') or {}).get('metrics', [])
    new = (current.get('financial_details') or {}).get('metrics', [])
    old_counts = Counter(metric_key(m) for m in old)
    new_counts = Counter(metric_key(m) for m in new)
    index = {metric_key(m): m for m in old if metric_key(m) is not None}
    result = []
    for m in new:
        key = metric_key(m)
        if (key is None or old_counts[key] != 1 or new_counts[key] != 1
                or m.get('value_type') == '실적'):
            continue
        prior = index[key]
        if prior.get('value') is None or m.get('value') is None:
            continue
        result.append({
            **{k: m.get(k) for k in ('metric', 'fiscal_period', 'unit', 'currency',
                                    'accounting_basis', 'value_type', 'scenario')},
            'previous': prior['value'], 'current': m['value'],
            **numeric_change(prior['value'], m['value'], m['unit'], m['metric']),
            'previous_evidence': prior.get('evidence'),
            'current_evidence': m.get('evidence'),
        })
    return result


def comparison(left: dict, right: dict) -> dict:
    if any(r.get('report_type') not in (None, '단일종목') for r in (left, right)):
        raise HTTPException(422, '금융 비교는 단일종목 보고서 두 개를 선택해 주세요.')
    if left['id'] == right['id']:
        raise HTTPException(422, '서로 다른 보고서 두 개를 선택해 주세요.')
    if not set(left.get('stock_codes') or []) & set(right.get('stock_codes') or []):
        raise HTTPException(422, '같은 기업의 보고서를 선택해 주세요.')
    left, right = sorted([left, right], key=lambda r: (r.get('published_at') or '', r['id']))
    old, new = left.get('summary') or {}, right.get('summary') or {}
    # A saved narrative is valid only for its exact source pair.
    narrative = new.get('diff_narrative') if new.get('prev_report_id') == left['id'] else None
    same = bool(left.get('publisher')) and left['publisher'] == right.get('publisher')
    target_change = None
    if old.get('target_price_new') is not None and new.get('target_price_new') is not None:
        target_change = numeric_change(old['target_price_new'], new['target_price_new'], '원')
    return {'left': left, 'right': right, 'same_publisher': same,
            'metrics': compare_financials(old, new), 'narrative': narrative,
            'target_price_change': target_change}
