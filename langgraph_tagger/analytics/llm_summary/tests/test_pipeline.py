from langgraph_tagger.analytics.llm_summary.pipeline import (
    normalize_target_price_dir, open_pool,
)
from langgraph_tagger.analytics.llm_summary.schemas import ExtractionResult


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


# ── open_pool asyncpg sizing regression (smoke bug) ───────────────────────

import pytest


@pytest.mark.asyncio
async def test_open_pool_min_size_le_max_size(monkeypatch):
    """asyncpg.create_pool은 default min_size=10. 우리 max_size=2이면
    ValueError. open_pool은 명시적으로 min_size <= max_size이어야 함.

    이게 빠지면 manual smoke에서 "🤖 LLM 분석" 버튼 첫 클릭에서
    `ValueError: min_size is greater than max_size`로 즉시 터짐.
    """
    from unittest.mock import AsyncMock, MagicMock
    captured: dict = {}

    async def fake_create_pool(*args, **kwargs):
        captured['args'] = args
        captured['kwargs'] = kwargs
        pool = MagicMock()
        pool.close = AsyncMock()
        return pool

    monkeypatch.setattr('asyncpg.create_pool', fake_create_pool)

    async with open_pool('postgres://test', max_size=2) as pool:
        assert pool is not None

    kw = captured['kwargs']
    assert 'min_size' in kw, \
        'open_pool must set min_size (default 10 > our max_size=2 → ValueError)'
    assert kw['min_size'] <= kw['max_size'], \
        f"min_size={kw['min_size']} > max_size={kw['max_size']} (asyncpg ValueError)"
    assert kw['min_size'] >= 1


@pytest.mark.asyncio
async def test_open_pool_disables_statement_cache(monkeypatch):
    """Supabase의 pgbouncer transaction mode는 prepared statement 캐시 안 됨.
    asyncpg.create_pool에 statement_cache_size=0 명시 안 하면
    `DuplicatePreparedStatementError: prepared statement "__asyncpg_stmt_X__"
    already exists` 로 silent fail (transient 아니라 일반 Exception이라
    pipeline._process_diff_one의 generic except에 잡혀서 row 업데이트 안 됨).

    기존 태거 supabase_io.py:140이 동일 패턴.
    """
    from unittest.mock import AsyncMock, MagicMock
    captured: dict = {}

    async def fake_create_pool(*args, **kwargs):
        captured['kwargs'] = kwargs
        pool = MagicMock()
        pool.close = AsyncMock()
        return pool

    monkeypatch.setattr('asyncpg.create_pool', fake_create_pool)

    async with open_pool('postgres://test', max_size=2):
        pass

    assert captured['kwargs'].get('statement_cache_size') == 0, \
        'open_pool must set statement_cache_size=0 (Supabase pgbouncer transaction mode)'


from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from langgraph_tagger.analytics.llm_summary import pipeline
from langgraph_tagger.analytics.llm_summary.config import LLMSummaryConfig
from langgraph_tagger.analytics.llm_summary.schemas import ExtractionResult


@pytest.fixture
def cfg():
    """Test config with mock API key — passed explicitly to analyze_stock."""
    return LLMSummaryConfig(
        llm_model='gpt-5.4-mini', max_concurrent=2,
        per_report_timeout_s=10, max_input_tokens=10000,
        summary_version='llm-summary@1.0',
        supabase_db_url='postgres://test',
        openai_api_key='sk-test-mock',
    )


@pytest.fixture
def fake_pool(monkeypatch):
    """Replace pipeline.open_pool with a no-op asynccontextmanager."""
    @asynccontextmanager
    async def _ctx(*a, **k):
        yield MagicMock()
    monkeypatch.setattr(pipeline, 'open_pool', _ctx)


class FakeAnalyticsDB:
    def __init__(self, df: pd.DataFrame, sb=None):
        self._df = df
        self._sb = sb
    def fetch_stock_rows(self, code, period_start_iso):
        return self._df.copy()


def _make_row(id_, report_type='단일종목', file_path='reports/x.pdf'):
    return {
        'id': id_, 'report_type': report_type,
        'publisher': '삼성증권', 'published_at': '2026-05-05',
        'stock_codes': ['005930'], 'file_path': file_path,
        'tagging_status': 'auto', 'out_of_scope_reason': None,
        'title': 'Test', 'sent_at': '2026-05-05T00:00:00+00:00',
    }


@pytest.fixture
def mock_llm_extract(monkeypatch):
    """extract_one mock returning a valid result."""
    async def _fake(*args, **kwargs):
        return (ExtractionResult(
            target_price_new=85000, target_price_old=70000,
            target_price_dir='상향', recommendation='매수',
            recommendation_dir='유지', one_line_summary='X',
            positive_points=['p'], risk_points=['r'],
            target_price_raw='8.5만원', recommendation_raw='Buy',
            source_pages=[1], extraction_confidence='high',
        ), 1000, 200)
    monkeypatch.setattr(pipeline, 'extract_one_safe', _fake)


@pytest.mark.asyncio
async def test_pass1_cache_hit_skips_llm(mock_llm_extract, fake_pool, cfg, tmp_path):
    """cache hit + diff already done이면 LLM 호출 0."""
    df = pd.DataFrame([_make_row(1)])
    sb = MagicMock()
    sb.table.return_value.select.return_value.in_.return_value.eq.return_value.execute.return_value.data = [
        {'report_id': 1, 'summary_version': 'llm-summary@1.0',
         'prev_match_type': 'same_publisher',
         'publisher': '삼성증권', 'stock_codes': ['005930'],
         'published_at': '2026-05-05'},
    ]
    adb = FakeAnalyticsDB(df, sb)

    cards = await pipeline.analyze_stock(
        analytics_db=adb, storage_base_dir=tmp_path,
        stock_code='005930', period_start_iso='2026-01-01',
        progress_cb=lambda *a, **k: None, cfg=cfg,
    )
    assert len(cards) == 1


@pytest.mark.asyncio
async def test_pass1_filters_non_단일종목(mock_llm_extract, fake_pool, cfg,
                                          monkeypatch, tmp_path):
    df = pd.DataFrame([_make_row(1, report_type='산업'),
                       _make_row(2, report_type='단일종목')])
    sb = MagicMock()
    sb.table.return_value.select.return_value.in_.return_value.eq.return_value.execute.return_value.data = []
    adb = FakeAnalyticsDB(df, sb)

    monkeypatch.setattr(pipeline, 'find_prev_for_diff_safe',
                        AsyncMock(return_value=None))
    upsert_calls = []
    monkeypatch.setattr(pipeline, '_call_summary_store_upsert',
                        lambda sb, payload: upsert_calls.append(payload))
    monkeypatch.setattr(pipeline, '_call_summary_store_update_diff',
                        lambda *a, **k: None)
    monkeypatch.setattr(pipeline, 'extract_pdf_pages',
                        lambda *a, **k: ('text', 1, 1, False))

    cards = await pipeline.analyze_stock(
        analytics_db=adb, storage_base_dir=tmp_path,
        stock_code='005930', period_start_iso='2026-01-01',
        progress_cb=lambda *a, **k: None, cfg=cfg,
    )
    # 단일종목만 LLM extract됨 — upsert는 id=2에 대해서만
    upserted_ids = [p['report_id'] for p in upsert_calls]
    assert upserted_ids == [2]


@pytest.mark.asyncio
async def test_pass2_cascade_hit_writes_diff(monkeypatch, fake_pool, cfg,
                                              mock_llm_extract, tmp_path):
    df = pd.DataFrame([_make_row(2, file_path='r2.pdf')])
    sb = MagicMock()
    # cache: id=2 row 있지만 prev_match_type=NULL → Pass2 대상
    sb.table.return_value.select.return_value.in_.return_value.eq.return_value.execute.return_value.data = [
        {'report_id': 2, 'summary_version': 'llm-summary@1.0',
         'prev_match_type': None, 'publisher': '삼성증권',
         'published_at': '2026-05-05', 'stock_codes': ['005930'],
         'target_price_new': 85000, 'recommendation': '매수',
         'one_line_summary': '현재 view'},
    ]
    adb = FakeAnalyticsDB(df, sb)

    from langgraph_tagger.analytics.llm_summary.summary_store import PrevRow
    from langgraph_tagger.analytics.llm_summary.schemas import DiffResult
    fake_prev = PrevRow(
        prev_report_id=1, prev_publisher='삼성증권',
        prev_published_at='2026-03-15', match_type='same_publisher',
        summary={'target_price_new': 70000, 'recommendation': '매수',
                 'one_line_summary': '이전', 'positive_points': [],
                 'risk_points': [], 'target_price_dir': '신규',
                 'recommendation_dir': '신규',
                 'target_price_raw': '7만원', 'recommendation_raw': 'Buy',
                 'target_price_old': None},
    )
    monkeypatch.setattr(pipeline, 'find_prev_for_diff_safe',
                        AsyncMock(return_value=fake_prev))
    monkeypatch.setattr(pipeline, 'diff_one_safe',
                        AsyncMock(return_value=(DiffResult(diff_narrative='vs 3/15...'), 100, 50)))

    update_calls = []
    monkeypatch.setattr(pipeline, '_call_summary_store_update_diff',
                        lambda sb, **kw: update_calls.append(kw))

    await pipeline.analyze_stock(
        analytics_db=adb, storage_base_dir=tmp_path,
        stock_code='005930', period_start_iso='2026-01-01',
        progress_cb=lambda *a, **k: None, cfg=cfg,
    )
    assert len(update_calls) == 1

    assert update_calls[0]['match_type'] == 'same_publisher'
    assert update_calls[0]['narrative'].startswith('vs 3/15')
    assert update_calls[0]['prev_report_id'] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('refresh,expected_ids', [(False, []), (True, [1])])
async def test_financial_expansion_only_refreshes_basic_summaries(
    monkeypatch, fake_pool, cfg, mock_llm_extract, tmp_path, refresh, expected_ids,
):
    from langgraph_tagger.analytics.llm_summary.tests.test_financials import financial_details
    cache = {
        1: {'report_id': 1, 'prev_match_type': 'same_publisher', 'financial_details': None},
        2: {'report_id': 2, 'prev_match_type': 'same_publisher',
            'financial_details': financial_details()},
    }
    monkeypatch.setattr(pipeline.summary_store, 'fetch_summaries', lambda *a: dict(cache))
    written = []

    def save(sb, payload):
        written.append(payload['report_id'])
        cache[payload['report_id']] = payload

    monkeypatch.setattr(pipeline, '_call_summary_store_upsert', save)
    monkeypatch.setattr(pipeline, 'extract_pdf_pages', lambda *a: ('text', 1, 1, False))
    monkeypatch.setattr(pipeline, 'find_prev_for_diff_safe', AsyncMock(return_value=None))
    monkeypatch.setattr(pipeline, '_call_summary_store_update_diff', lambda *a, **k: None)
    db = FakeAnalyticsDB(pd.DataFrame([_make_row(1), _make_row(2)]), MagicMock())
    cards = await pipeline.analyze_stock(
        analytics_db=db, storage_base_dir=tmp_path,
        stock_code='005930', period_start_iso='2026-01-01',
        progress_cb=lambda *a: None, cfg=cfg, refresh_financials=refresh,
    )
    assert written == expected_ids
    assert len(cards) == 2
    assert cache[2]['financial_details'] == financial_details()


@pytest.mark.asyncio
async def test_pass2_no_prev_marks_none(monkeypatch, fake_pool, cfg,
                                         mock_llm_extract, tmp_path):
    df = pd.DataFrame([_make_row(2)])
    sb = MagicMock()
    sb.table.return_value.select.return_value.in_.return_value.eq.return_value.execute.return_value.data = [
        {'report_id': 2, 'summary_version': 'llm-summary@1.0',
         'prev_match_type': None, 'publisher': '삼성증권',
         'published_at': '2026-05-05', 'stock_codes': ['005930']},
    ]
    adb = FakeAnalyticsDB(df, sb)

    monkeypatch.setattr(pipeline, 'find_prev_for_diff_safe',
                        AsyncMock(return_value=None))
    update_calls = []
    monkeypatch.setattr(pipeline, '_call_summary_store_update_diff',
                        lambda sb, **kw: update_calls.append(kw))

    await pipeline.analyze_stock(
        analytics_db=adb, storage_base_dir=tmp_path,
        stock_code='005930', period_start_iso='2026-01-01',
        progress_cb=lambda *a, **k: None, cfg=cfg,
    )
    assert len(update_calls) == 1
    assert update_calls[0]['match_type'] == 'none'
    assert update_calls[0]['narrative'] is None
    assert update_calls[0]['prev_report_id'] is None


@pytest.mark.asyncio
async def test_pass2_includes_none_marked_rows(monkeypatch, fake_pool, cfg,
                                                mock_llm_extract, tmp_path):
    """'none' 마킹된 row도 Pass2 재평가 대상 (P1 #1)."""
    df = pd.DataFrame([_make_row(2)])
    sb = MagicMock()
    sb.table.return_value.select.return_value.in_.return_value.eq.return_value.execute.return_value.data = [
        {'report_id': 2, 'summary_version': 'llm-summary@1.0',
         'prev_match_type': 'none',  # 이전 클릭에 cascade fail
         'publisher': '삼성증권',
         'published_at': '2026-05-05', 'stock_codes': ['005930']},
    ]
    adb = FakeAnalyticsDB(df, sb)

    fpd = AsyncMock(return_value=None)
    monkeypatch.setattr(pipeline, 'find_prev_for_diff_safe', fpd)
    update_calls = []
    monkeypatch.setattr(pipeline, '_call_summary_store_update_diff',
                        lambda sb, **kw: update_calls.append(kw))

    await pipeline.analyze_stock(
        analytics_db=adb, storage_base_dir=tmp_path,
        stock_code='005930', period_start_iso='2026-01-01',
        progress_cb=lambda *a, **k: None, cfg=cfg,
    )
    # 'none' row도 Pass2가 다시 cascade 시도 (재평가)
    assert fpd.call_count == 1
    assert len(update_calls) == 1
