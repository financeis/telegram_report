"""Phase 2 orchestration — 2-pass (extract → diff) + pool lifecycle.

Spec §8. 메인 함수 `analyze_stock`은 Task 11/12에서 추가.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable, Optional

import asyncpg
from langgraph_tagger.llm_provider import LLMClient

from langgraph_tagger.analytics.llm_summary import summary_store
from langgraph_tagger.analytics.llm_summary.config import (
    LLMSummaryConfig, load_llm_summary_config, make_llm_client,
)
from langgraph_tagger.analytics.llm_summary.llm import (
    diff_one, extract_one, TransientLLMError,
)
from langgraph_tagger.analytics.llm_summary.pdf_text import extract_all_pages
from langgraph_tagger.analytics.llm_summary.schemas import ExtractionResult
from langgraph_tagger.analytics.llm_summary.financials import (
    compare_financials, has_financial_details, ground_metrics,
)

logger = logging.getLogger(__name__)


def normalize_target_price_dir(r: ExtractionResult) -> ExtractionResult:
    """deterministic 산수로 target_price_dir 덮어씀 (LLM 판단보다 산수 우선).

    Spec §6.3 — old/new 둘 다 int면 부호 비교, 한쪽만이면 LLM 판단 유지,
    둘 다 None이면 'N/A' 강제.
    """
    new, old = r.target_price_new, r.target_price_old
    if new is not None and old is not None:
        if new > old:    forced = '상향'
        elif new < old:  forced = '하향'
        else:            forced = '불변'
    elif new is None and old is None:
        forced = 'N/A'
    else:
        # 한쪽만 있음 → LLM 판단 유지 (신규/N/A 등)
        return r
    if r.target_price_dir == forced:
        return r
    return r.model_copy(update={'target_price_dir': forced})


# ── wrappers (테스트 mock 용 — module 레벨 함수로 빼서 monkeypatch 쉽게) ─

extract_one_safe = extract_one
diff_one_safe = diff_one
find_prev_for_diff_safe = summary_store.find_prev_for_diff


def extract_pdf_pages(path: Path, max_tokens: int):
    r = extract_all_pages(path, max_tokens)
    return r.text, r.pages_used, r.total_pages, r.input_truncated


def _call_summary_store_upsert(sb, payload):
    summary_store.upsert_summary(sb, payload)


def _call_summary_store_update_diff(sb, **kwargs):
    summary_store.update_diff(sb, **kwargs)


@asynccontextmanager
async def open_pool(db_url: str, max_size: int = 2):
    """매 호출 새 pool 열고 닫음 — Streamlit rerun event loop mismatch 회피.

    - asyncpg default min_size=10 인데 우리 운영 ceiling은 max_size=2.
      min_size>max_size이면 ValueError라서 min_size=1로 명시.
    - Supabase는 pgbouncer transaction mode를 쓰는 경우가 많아 prepared
      statement 캐싱이 깨짐. statement_cache_size=0으로 비활성화.
      (기존 태거 langgraph_tagger/supabase_io.py:136-141와 동일 패턴.)
    """
    pool = await asyncpg.create_pool(
        db_url,
        min_size=1,
        max_size=max_size,
        statement_cache_size=0,
    )
    try:
        yield pool
    finally:
        await pool.close()


# ── 메인 함수 ────────────────────────────────────────────────────────────

async def analyze_stock(
    *,
    analytics_db,                       # langgraph_tagger.analytics.db.AnalyticsDB
    storage_base_dir: Path,
    stock_code: str,
    period_start_iso: str,
    progress_cb: Callable[..., None],
    cfg: Optional[LLMSummaryConfig] = None,
    refresh_financials: bool = False,
) -> list[dict[str, Any]]:
    cfg = cfg or load_llm_summary_config()
    sb = analytics_db._sb                # supabase-py REST client (analytics에서 재사용)

    # Step A — fetch + 단일종목 필터
    df = analytics_db.fetch_stock_rows(stock_code, period_start_iso)
    if df.empty:
        return []
    df = df[df['report_type'] == '단일종목'].copy()
    if df.empty:
        return []

    report_ids = df['id'].astype(int).tolist()

    # Step B — cache lookup
    cached = summary_store.fetch_summaries(sb, report_ids, cfg.summary_version)
    miss_ids = [rid for rid in report_ids if rid not in cached or (
        refresh_financials and not has_financial_details(cached[rid])
    )]

    # Step C — LLM client는 cache miss가 있을 때만 필요 (lazy)
    if miss_ids:
        client = make_llm_client(cfg)
    else:
        client = None

    async with open_pool(cfg.supabase_db_url, max_size=cfg.max_concurrent) as pool:
        # Pass 1 — extract cache misses
        if miss_ids:
            sem = asyncio.Semaphore(cfg.max_concurrent)
            miss_df = df[df['id'].isin(miss_ids)]
            tasks = [
                _process_extract_one(
                    row=row.to_dict(), client=client, cfg=cfg, sb=sb,
                    storage_base_dir=storage_base_dir, sem=sem,
                )
                for _, row in miss_df.iterrows()
            ]
            done = 0
            for coro in asyncio.as_completed(tasks):
                await coro
                done += 1
                progress_cb(1, done, len(tasks))

        # Pass 2 — diff for rows where prev_match_type IS NULL or 'none'
        # 재페치해서 최신 상태 가져옴 (Pass1에서 새로 들어온 row 포함)
        fresh = summary_store.fetch_summaries(sb, report_ids, cfg.summary_version)
        target_rows = []
        for rid in report_ids:
            row_meta = df[df['id'] == rid].iloc[0].to_dict()
            summary_row = fresh.get(rid)
            if summary_row is None:
                continue  # Pass1 실패한 row — diff 시도 안 함
            if summary_row.get('prev_match_type') in (None, 'none') or rid in miss_ids:
                target_rows.append((rid, row_meta, summary_row))

        if target_rows:
            sem2 = asyncio.Semaphore(cfg.max_concurrent)
            if client is None:
                # Pass1에서 client 안 만들었지만 Pass2는 LLM diff 필요할 수 있음
                # — cascade hit 시에만 호출됨. lazy 검증.
                client = make_llm_client(cfg)
            tasks2 = [
                _process_diff_one(
                    rid=rid, row_meta=row_meta, curr_summary=curr_summary,
                    stock_code=stock_code,
                    client=client, cfg=cfg, sb=sb, pool=pool, sem=sem2,
                )
                for rid, row_meta, curr_summary in target_rows
            ]
            done = 0
            for coro in asyncio.as_completed(tasks2):
                await coro
                done += 1
                progress_cb(2, done, len(tasks2))

    # Step D — final fetch + 카드 빌드
    final = summary_store.fetch_summaries(sb, report_ids, cfg.summary_version)
    cards: list[dict[str, Any]] = []
    for _, row in df.sort_values('published_at', ascending=False).iterrows():
        rid = int(row['id'])
        s = final.get(rid)
        if s is None:
            cards.append({'report_id': rid, 'error': 'extract', 'meta': row.to_dict()})
        else:
            cards.append({'report_id': rid, 'summary': s, 'meta': row.to_dict()})
    return cards


async def _process_extract_one(
    *,
    row: dict[str, Any],
    client: LLMClient,
    cfg: LLMSummaryConfig,
    sb,
    storage_base_dir: Path,
    sem: asyncio.Semaphore,
) -> None:
    """row-level failure 격리 — 예외 잡아 카드만 error 표시."""
    rid = int(row['id'])
    try:
        async with sem:
            file_path = storage_base_dir / row['file_path']
            text, pages_used, total_pages, truncated = extract_pdf_pages(
                file_path, cfg.max_input_tokens,
            )
            if not text:
                logger.warning("PDF empty/missing for report_id=%d", rid)
                return  # row 미생성 → final fetch에서 error 카드

            metadata = {
                'publisher': row.get('publisher'),
                'stock_codes': row.get('stock_codes', []),
                'published_at': row.get('published_at'),
                'title': row.get('title'),
            }
            extracted, tokens_in, tokens_out = await extract_one_safe(
                client=client, model=cfg.llm_model,
                metadata=metadata, pages_text=text,
                timeout_s=cfg.per_report_timeout_s,
            )
            extracted = normalize_target_price_dir(extracted)
            financial_payload = None
            if extracted.financial_details is not None:
                grounded, omitted = ground_metrics(extracted.financial_details, text)
                financial_payload = grounded.model_dump()
                financial_payload['unsupported_numeric_values'] = omitted

            payload = {
                'report_id': rid,
                **extracted.model_dump(),
                'financial_details': financial_payload,
                'input_truncated': truncated,
                'input_pages_used': pages_used,
                'input_total_pages': total_pages,
                'summary_version': cfg.summary_version,
                'llm_model': cfg.llm_model,
                'llm_tokens_input': tokens_in,
                'llm_tokens_output': tokens_out,
                # diff fields 의무 reset (spec §10)
                'prev_report_id': None,
                'prev_match_type': None,
                'diff_narrative': None,
                'comparison_details': None,
            }
            _call_summary_store_upsert(sb, payload)
    except TransientLLMError as e:
        logger.warning("Extract permanent fail report_id=%d: %s", rid, e)
    except Exception as e:
        logger.exception("Extract unexpected error report_id=%d: %s", rid, e)


async def _process_diff_one(
    *,
    rid: int,
    row_meta: dict[str, Any],
    curr_summary: dict[str, Any],
    stock_code: str,
    client: LLMClient,
    cfg: LLMSummaryConfig,
    sb,
    pool,
    sem: asyncio.Semaphore,
) -> None:
    try:
        async with sem:
            # stock_code comes from analyze_stock's parameter — the stock the user is
            # analyzing. For multi-stock rows (보통주+우선주 등), this ensures the diff
            # cascade runs against the correct stock, not row_meta['stock_codes'][0].
            prev = await find_prev_for_diff_safe(
                pool, stock_code=stock_code,
                publisher=row_meta.get('publisher'),
                current_published_at=str(row_meta['published_at']),
                active_version=cfg.summary_version,
            )
            if prev is None:
                _call_summary_store_update_diff(
                    sb, report_id=rid, prev_report_id=None,
                    match_type='none', narrative=None,
                )
                return

            diff, _, _ = await diff_one_safe(
                client=client, model=cfg.llm_model,
                prev_summary=prev.summary, curr_summary=curr_summary,
                prev_match_type=prev.match_type,
                prev_report_id=prev.prev_report_id,
                prev_publisher=prev.prev_publisher or '',
                curr_publisher=row_meta.get('publisher') or '',
                timeout_s=cfg.per_report_timeout_s,
            )
            _call_summary_store_update_diff(
                sb, report_id=rid, prev_report_id=prev.prev_report_id,
                match_type=prev.match_type, narrative=diff.diff_narrative,
                comparison_details={
                    'previous_publisher': prev.prev_publisher,
                    'previous_published_at': str(prev.prev_published_at),
                    'metrics': compare_financials(prev.summary, curr_summary),
                    'previous_has_financials': has_financial_details(prev.summary),
                },
            )
    except TransientLLMError as e:
        # prev_match_type 그대로 NULL/none 유지 → 다음 클릭 Pass2 재시도
        logger.warning("Diff transient fail report_id=%d: %s", rid, e)
    except Exception as e:
        logger.exception("Diff unexpected error report_id=%d: %s", rid, e)
