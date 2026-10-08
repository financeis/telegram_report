"""Financial facts and conservative, same-basis comparisons for research cards."""
from __future__ import annotations

from collections import Counter
import re
from typing import Literal

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    page: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=500)


class FinancialMetric(BaseModel):
    metric: str
    fiscal_period: str | None
    value: float | None = Field(allow_inf_nan=False)
    previous_value: float | None = Field(allow_inf_nan=False)
    unit: str
    currency: str | None
    accounting_basis: Literal['연결', '별도', '미기재']
    value_type: Literal['실적', '추정', '가이던스']
    scenario: Literal['기본', '낙관', '비관', '미기재']
    evidence: Evidence
    previous_evidence: Evidence | None = None


class ValuationAssumption(BaseModel):
    name: str
    current: str
    previous: str | None
    fiscal_period: str | None
    evidence: Evidence


class ValuationDriver(BaseModel):
    category: Literal['실적 추정 변경', '배수 변경', '평가기간 변경',
                      '할인율·자본비용 변경', '주식수·순차입금·자산가치 변경', '기타']
    explanation: str
    evidence: Evidence
    previous_basis: str | None = None
    current_basis: str | None = None


class Valuation(BaseModel):
    method: Literal['PER', 'PBR', 'EV/EBITDA', 'EV/Sales', 'DCF', 'SOTP', 'DDM', '기타', '미기재']
    target_horizon: str | None
    explanation: str | None
    assumptions: list[ValuationAssumption] = Field(default_factory=list, max_length=12)
    change_drivers: list[ValuationDriver] = Field(default_factory=list, max_length=6)


class InvestmentThesis(BaseModel):
    claim: str
    mechanism: str
    support_type: Literal['공시·실적', '회사 가이던스', '애널리스트 추정', '애널리스트 의견']
    monitoring_metric: str | None
    invalidation_condition: str | None
    invalidation_basis: Literal['원문 명시', '논리에서 도출', '미기재'] = '논리에서 도출'
    evidence: Evidence


class Catalyst(BaseModel):
    event: str
    expected_timing: str | None
    condition: str | None
    evidence: Evidence


class RatingDetails(BaseModel):
    current_label: str | None
    previous_label: str | None
    definition: str | None
    horizon: str | None
    evidence: Evidence | None


class FinancialDetails(BaseModel):
    metrics: list[FinancialMetric] = Field(default_factory=list, max_length=48)
    valuation: Valuation
    theses: list[InvestmentThesis] = Field(default_factory=list, max_length=5)
    catalysts: list[Catalyst] = Field(default_factory=list, max_length=5)
    rating: RatingDetails


def _numbers(text: str) -> set[float]:
    text = text.replace('−', '-').replace('－', '-')
    values = set()
    for match in re.finditer(r'\(?-?\d[\d,]*(?:\.\d+)?\)?', text):
        raw = match.group().replace(',', '')
        if raw.startswith('(') and raw.endswith(')'):
            raw = '-' + raw[1:-1]
        try:
            values.add(float(raw.strip('()')))
        except ValueError:
            pass
    return values


def ground_metrics(details: FinancialDetails, pages_text: str) -> tuple[FinancialDetails, int]:
    """Keep numeric facts present in both their citation and the cited PDF page.

    This detects unsupported numbers, not all table-alignment errors. If only the
    prior number lacks support, retain the current observation without a revision.
    """
    chunks = re.split(r'--- Page (\d+) ---', pages_text)
    page_numbers = {int(chunks[i]): _numbers(chunks[i + 1])
                    for i in range(1, len(chunks) - 1, 2)}

    def supported(value, evidence):
        return (value is not None and evidence is not None
                and value in _numbers(evidence.quote)
                and value in page_numbers.get(evidence.page, set()))

    kept = []
    omitted = 0
    for metric in details.metrics:
        # A forward valuation input is not a fiscal-year forecast observation.
        if (re.search(r'12\s*M\s*Fwd|12\s*개월\s*선행|\bNTM\b', metric.evidence.quote, re.I)
                and re.fullmatch(r'\d{4}[EA]?', metric.fiscal_period or '')):
            omitted += 1
            continue
        if not supported(metric.value, metric.evidence):
            omitted += 1
            continue
        if metric.previous_value is not None and not (
            supported(metric.previous_value, metric.previous_evidence)
            and supported(metric.value, metric.previous_evidence)
        ):
            metric = metric.model_copy(update={'previous_value': None, 'previous_evidence': None})
            omitted += 1
        kept.append(metric)
    drivers = [driver for driver in details.valuation.change_drivers
               if driver.category != '평가기간 변경' or (
                   driver.previous_basis and driver.current_basis
                   and driver.previous_basis != driver.current_basis)]
    valuation = details.valuation.model_copy(update={'change_drivers': drivers})
    return details.model_copy(update={'metrics': kept, 'valuation': valuation}), omitted


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
