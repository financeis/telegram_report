"""Target price direction is recomputed by arithmetic.

Ported from langgraph_tagger/analytics/llm_summary/tests/test_pipeline.py.
"""
from research_desk.features.analysis.logic import normalize_target_price_dir
from research_desk.features.analysis.schemas import ExtractionResult


def _make(new=None, old=None, dir_='N/A'):
    return ExtractionResult(
        target_price_new=new, target_price_old=old,
        target_price_dir=dir_, recommendation='매수',
        recommendation_dir='유지', one_line_summary='X',
        positive_points=[], risk_points=[],
        target_price_raw='8만원' if new else None,
        recommendation_raw='Buy',
        source_pages=[1] if new else [],
        extraction_confidence='high',
    )


def test_normalize_both_int_higher_overrides_to_상향():
    r = _make(new=85000, old=70000, dir_='불변')  # LLM이 틀려도
    out = normalize_target_price_dir(r)
    assert out.target_price_dir == '상향'


def test_normalize_both_int_lower_overrides_to_하향():
    r = _make(new=60000, old=70000, dir_='상향')  # LLM이 틀려도
    out = normalize_target_price_dir(r)
    assert out.target_price_dir == '하향'


def test_normalize_both_int_equal_overrides_to_불변():
    r = _make(new=70000, old=70000, dir_='상향')
    out = normalize_target_price_dir(r)
    assert out.target_price_dir == '불변'


def test_normalize_only_new_keeps_llm_judgment():
    r = _make(new=85000, old=None, dir_='신규')
    out = normalize_target_price_dir(r)
    assert out.target_price_dir == '신규'  # LLM 판단 유지


def test_normalize_neither_forces_NA():
    r = _make(new=None, old=None, dir_='상향')  # LLM 잘못 추출
    out = normalize_target_price_dir(r)
    assert out.target_price_dir == 'N/A'
