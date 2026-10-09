"""ExtractionResult validation.

Ported from langgraph_tagger/analytics/llm_summary/tests/test_schemas.py
(the DiffResult test moved with DiffResult to the compare feature).
"""
import pytest
from pydantic import ValidationError

from research_desk.features.analysis.schemas import ExtractionResult


def _valid_payload():
    return dict(
        target_price_new=85000, target_price_old=70000,
        target_price_dir='상향', recommendation='매수',
        recommendation_dir='유지',
        one_line_summary='메모리 가격 반등으로 25년 영업이익 ...',
        positive_points=['matter 1', 'matter 2'],
        risk_points=['risk 1'],
        target_price_raw='8.5만원', recommendation_raw='Buy',
        source_pages=[1, 3], extraction_confidence='high',
    )


def test_valid_full_payload():
    r = ExtractionResult(**_valid_payload())
    assert r.target_price_new == 85000
    assert r.source_pages == [1, 3]


def test_empty_bullets_allowed():
    p = _valid_payload()
    p['positive_points'] = []
    p['risk_points'] = []
    r = ExtractionResult(**p)
    assert r.positive_points == []
    assert r.risk_points == []


def test_too_many_bullets_rejected():
    p = _valid_payload()
    p['positive_points'] = ['a'] * 6
    with pytest.raises(ValidationError):
        ExtractionResult(**p)


def test_source_pages_dedupe_sort():
    p = _valid_payload()
    p['source_pages'] = [3, 1, 3, 2, 1]
    r = ExtractionResult(**p)
    assert r.source_pages == [1, 2, 3]


def test_source_pages_zero_rejected():
    p = _valid_payload()
    p['source_pages'] = [0, 1, 2]
    with pytest.raises(ValidationError):
        ExtractionResult(**p)


def test_source_pages_negative_rejected():
    p = _valid_payload()
    p['source_pages'] = [-1, 1]
    with pytest.raises(ValidationError):
        ExtractionResult(**p)


def test_evidence_invariant_target_price_no_evidence():
    p = _valid_payload()
    p['target_price_new'] = 80000
    p['target_price_raw'] = None
    p['source_pages'] = []
    with pytest.raises(ValidationError) as e:
        ExtractionResult(**p)
    assert 'evidence' in str(e.value).lower() or 'target_price_raw' in str(e.value)


def test_evidence_invariant_target_price_with_raw_ok():
    """target_price_new + raw → OK (source_pages 비어도)."""
    p = _valid_payload()
    p['source_pages'] = []
    p['target_price_raw'] = '8.5만원'
    r = ExtractionResult(**p)
    assert r.target_price_new == 85000


def test_extraction_confidence_invalid():
    p = _valid_payload()
    p['extraction_confidence'] = 'unknown'
    with pytest.raises(ValidationError):
        ExtractionResult(**p)
