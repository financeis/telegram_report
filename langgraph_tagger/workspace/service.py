"""UI-facing adapter. Credentials and PDF paths never enter browser payloads."""
from __future__ import annotations

import asyncio
from pathlib import Path
from threading import Lock
from typing import Any
from time import monotonic
from .coverage import market_payload, stock_activity_payload
from .review import ReviewService

from fastapi import HTTPException
from supabase import create_client

from langgraph_tagger.analytics import favorites, krx
from langgraph_tagger.analytics.config import load_analytics_config
from langgraph_tagger.analytics.db import AnalyticsDB, SELECT_COLS
from langgraph_tagger.analytics.llm_summary.config import load_llm_summary_config, make_llm_client
from langgraph_tagger.analytics.llm_summary.financials import compare_financials, ground_metrics, numeric_change
from langgraph_tagger.analytics.llm_summary.llm import extract_one, diff_one
from langgraph_tagger.analytics.llm_summary.pdf_text import extract_all_pages
from langgraph_tagger.analytics.llm_summary.pipeline import normalize_target_price_dir
from langgraph_tagger.analytics.llm_summary.summary_store import fetch_summaries, upsert_summary, update_diff


def resolve_pdf(base: Path, relative: str) -> Path:
    root = base.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path.suffix.lower() != '.pdf':
        raise HTTPException(404, 'PDF를 찾을 수 없습니다.')
    if not path.is_file():
        raise HTTPException(404, '로컬 PDF 파일이 없습니다. 수집 상태를 확인해 주세요.')
    return path


def public_report(row: dict, summary: dict | None) -> dict:
    return {k: row.get(k) for k in (
        'id', 'title', 'file_name', 'published_at', 'publisher', 'report_type',
        'stock_codes', 'company_names', 'sectors_major', 'sectors_minor', 'products',
    )} | {'summary': summary, 'pdf_url': f"/api/reports/{row['id']}/pdf"}


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


class WorkspaceService:
    def __init__(self):
        self.cfg = load_analytics_config()
        self.sb = create_client(self.cfg.supabase_url, self.cfg.supabase_service_key)
        self.db = AnalyticsDB(self.sb)
        self.krx = krx.load_krx(self.cfg.krx_csv_path).fillna('')
        self.favorites_path = Path.home() / '.review_viewer' / 'favorites.json'
        self.favorite_lock = Lock()
        # This local server is a single process; at most two LLM calls in flight.
        self.llm_semaphore = asyncio.Semaphore(2)
        self.analyzing: set[int] = set()
        self.review = ReviewService(self.sb)
        self.coverage_cache = {}
        self.coverage_lock = Lock()

    def bootstrap(self):
        return {'stocks': self.krx.to_dict('records'), 'favorites': favorites.load(self.favorites_path)}

    def set_favorite(self, code: str, enabled: bool):
        if not krx.lookup(self.krx, code):
            raise HTTPException(404, '종목을 찾을 수 없습니다.')
        with self.favorite_lock:
            (favorites.add if enabled else favorites.remove)(self.favorites_path, code)
            return {'favorites': favorites.load(self.favorites_path)}

    def summaries(self, ids: list[int]) -> dict:
        # Reading the dashboard requires only Supabase REST credentials.
        import os
        result = {}
        for i in range(0, len(ids), 100):
            result.update(fetch_summaries(self.sb, ids[i:i+100], os.getenv('PHASE2_SUMMARY_VERSION', 'llm-summary@1.0')))
        return result

    def stock_reports(self, code: str):
        info = krx.lookup(self.krx, code)
        if not info:
            raise HTTPException(404, '종목을 찾을 수 없습니다.')
        df = self.db.fetch_stock_rows(code, '2000-01-01')
        rows = df.sort_values(['published_at', 'id'], ascending=False).to_dict('records')
        saved = self.summaries([r['id'] for r in rows])
        return {'stock': dict(zip(('code', 'name', 'sector_major', 'sector_minor'), info)),
                'reports': [public_report(r, saved.get(r['id'])) for r in rows]}

    def report_row(self, rid: int):
        result = (self.sb.table('reports').select(SELECT_COLS).eq('id', rid)
                  .in_('tagging_status', ['auto', 'verified']).is_('out_of_scope_reason', 'null')
                  .execute())
        if not result.data:
            raise HTTPException(404, '기업 보고서를 찾을 수 없습니다.')
        return result.data[0]

    def report(self, rid: int):
        return public_report(self.report_row(rid), self.summaries([rid]).get(rid))

    def pdf(self, rid: int):
        row = self.report_row(rid)
        return resolve_pdf(self.cfg.storage_base_dir, row['file_path'])

    async def analyze(self, rid: int):
        if rid in self.analyzing:
            raise HTTPException(409, '이 보고서는 분석 중입니다. 잠시 후 새로고침해 주세요.')
        self.analyzing.add(rid)
        try:
            async with self.llm_semaphore:
                row = await asyncio.to_thread(self.report_row, rid)
                if row.get('report_type') != '단일종목':
                    raise HTTPException(422, '금융 정보 분석은 단일종목 보고서를 선택해 주세요.')
                existing = (await asyncio.to_thread(self.summaries, [rid])).get(rid)
                if existing and existing.get('financial_details'):
                    return public_report(row, existing) | {'analysis_reused': True}
                cfg = load_llm_summary_config()
                llm = make_llm_client(cfg)
                path = resolve_pdf(self.cfg.storage_base_dir, row['file_path'])
                pdf = await asyncio.to_thread(extract_all_pages, path, cfg.max_input_tokens)
                if not pdf.text:
                    raise HTTPException(422, 'PDF에서 읽을 수 있는 텍스트가 없습니다.')
                async with llm as client:
                    result, tin, tout = await extract_one(
                        client=client, model=cfg.llm_model,
                        metadata={k: row.get(k) for k in ('publisher', 'stock_codes', 'published_at', 'title')},
                        pages_text=pdf.text, timeout_s=cfg.per_report_timeout_s,
                    )
                result = normalize_target_price_dir(result)
                payload: dict[str, Any] = result.model_dump()
                if result.financial_details:
                    grounded, omitted = ground_metrics(result.financial_details, pdf.text)
                    payload['financial_details'] = grounded.model_dump() | {'unsupported_numeric_values': omitted}
                payload.update(report_id=rid, input_truncated=pdf.input_truncated,
                               input_pages_used=pdf.pages_used, input_total_pages=pdf.total_pages,
                               summary_version=cfg.summary_version, llm_model=cfg.llm_model,
                               llm_tokens_input=tin, llm_tokens_output=tout, prev_report_id=None,
                               prev_match_type=None, diff_narrative=None, comparison_details=None)
                await asyncio.to_thread(upsert_summary, self.sb, payload)
                return public_report(row, payload) | {'analysis_reused': False}
        finally:
            self.analyzing.discard(rid)

    def market(self, since, level, items, unit, include_oos):
        key = (since, include_oos)
        with self.coverage_lock:
            cached = self.coverage_cache.get(key)
            if cached and monotonic() - cached[0] < 180:
                df = cached[1]
            else:
                df = self.db.fetch_inscope_or_oos_rows(since, include_oos)
                self.coverage_cache = {key: (monotonic(), df)}
        return market_payload(df, self.krx, level, items, unit, include_oos)

    def activity(self, code, since, unit):
        return stock_activity_payload(self.db.fetch_stock_rows(code, since), code, unit)

    async def analyze_comparison(self, left_id, right_id):
        async with self.llm_semaphore:
            left = await asyncio.to_thread(self.report, left_id)
            right = await asyncio.to_thread(self.report, right_id)
            result = comparison(left, right)
            left, right = result['left'], result['right']
            if not left['summary'] or not right['summary']:
                raise HTTPException(422, '선택한 두 보고서를 먼저 분석해 주세요.')
            if result['narrative']:
                return result
            cfg = load_llm_summary_config()
            match = 'same_publisher' if result['same_publisher'] else 'cross_publisher'
            async with make_llm_client(cfg) as client:
                diff, _, _ = await diff_one(
                    client=client, model=cfg.llm_model, prev_summary=left['summary'],
                    curr_summary=right['summary'], prev_match_type=match,
                    prev_report_id=left['id'], prev_publisher=left['publisher'] or '미상',
                    curr_publisher=right['publisher'] or '미상', timeout_s=cfg.per_report_timeout_s,
                )
            await asyncio.to_thread(update_diff, self.sb, right['id'], left['id'], match,
                                    diff.diff_narrative, {'metrics': result['metrics'],
                                    'previous_publisher': left['publisher'],
                                    'previous_published_at': left['published_at']})
            return result | {'narrative': diff.diff_narrative}
