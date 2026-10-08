import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock

import pandas as pd
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from langgraph_tagger.analytics.db import EXPECTED_COLS
from langgraph_tagger.workspace.api import app, get_service
from langgraph_tagger.workspace.coverage import market_payload, stock_activity_payload
from langgraph_tagger.workspace.review import ReviewService
from langgraph_tagger.workspace.service import WorkspaceService


def frame():
    base = dict.fromkeys(EXPECTED_COLS)
    base.update(stock_codes=['016360'], company_names=['삼성증권'],
                sectors_major=['금융'], sectors_minor=['증권'], products=['증권'],
                publisher='KB', report_type='단일종목', tagging_status='auto',
                published_at='2026-05-11', sent_at='2026-05-11T01:00:00Z')
    return pd.DataFrame([base | {'id': 1}, base | {'id': 2, 'report_type': '산업'},
                         base | {'id': 3, 'out_of_scope_reason': 'foreign',
                                 'published_at': None, 'report_type': '기타',
                                 'stock_codes': [], 'sent_at': '2026-05-11T16:00:00Z'}])


def test_macro_reuses_inscope_coverage_and_kst_oos_dates():
    krx = pd.DataFrame([{'code': '016360', 'name': '삼성증권'}])
    result = market_payload(frame(), krx, 'sectors_major', [], 'D', True)
    assert result['total'] == 3 and result['inscope'] == 2 and result['oos'] == 1
    assert result['coverage'] == [{'bucket': '2026-05-11T00:00:00.000', 'sector': '금융', 'count': 2}]
    assert result['ranking'][0] == {'code': '016360', 'count': 2, 'name': '삼성증권'}
    assert next(r for r in result['types'] if r['report_type'] == '기타')['bucket'].startswith('2026-05-12')
    excluded = market_payload(frame(), krx, 'sectors_major', ['제조'], 'D', False)
    assert excluded['coverage'] == [] and excluded['ranking'] == []
    assert all(r['report_type'] != '기타' for r in excluded['types'])


def test_stock_activity_includes_industry_reports():
    result = stock_activity_payload(frame(), '016360', 'W')
    assert sum(r['count'] for r in result['timeline']) == 2
    service = WorkspaceService.__new__(WorkspaceService)
    service.db = SimpleNamespace(fetch_stock_rows=lambda *_: frame().iloc[:2])
    service.krx = pd.DataFrame([{'code': '016360', 'name': '삼성증권', 'sector_major': '금융', 'sector_minor': '증권'}])
    service.summaries = lambda ids: {}
    reports = service.stock_reports('016360')['reports']
    assert {r['report_type'] for r in reports} == {'단일종목', '산업'}


@pytest.mark.asyncio
async def test_selected_cached_reports_only_and_non_company_rejection():
    service = WorkspaceService.__new__(WorkspaceService)
    service.llm_semaphore = asyncio.Semaphore(2)
    service.analyzing = set()
    service.report_row = Mock(side_effect=lambda rid: {'id': rid, 'report_type': '산업' if rid == 9 else '단일종목'})
    service.summaries = Mock(side_effect=lambda ids: {rid: {'financial_details': {'metrics': []}} for rid in ids})
    results = await asyncio.gather(service.analyze(1), service.analyze(3))
    assert [r['id'] for r in results] == [1, 3]
    assert all(r['analysis_reused'] for r in results)
    assert {call.args[0] for call in service.report_row.call_args_list} == {1, 3}
    assert {call.args[0][0] for call in service.summaries.call_args_list} == {1, 3}
    with pytest.raises(HTTPException) as exc:
        await service.analyze(9)
    assert exc.value.status_code == 422
    assert not service.analyzing


class MemoryQuery:
    def __init__(self, db):
        self.db, self.filters, self.payload = db, {}, None

    def select(self, *_args, **_kwargs): return self
    def eq(self, key, value): self.filters[key] = value; return self
    def is_(self, key, value): self.filters[key] = None if value == 'null' else value; return self
    def update(self, payload): self.payload = payload; return self
    def execute(self):
        data = []
        for row in self.db.rows:
            if all(row.get(k) == v for k, v in self.filters.items()):
                if self.payload is not None:
                    row.update(deepcopy(self.payload))
                data.append(deepcopy(row))
        return SimpleNamespace(data=data, count=len(data))


class MemoryDB:
    def __init__(self):
        self.rows = [{'id': 1, 'tagging_status': 'review_needed', 'tagging_notes': 'low_confidence',
                      'report_type': '단일종목', 'publisher': 'KB', 'stock_codes': ['016360'],
                      'file_path': 'original.pdf', 'caption': 'unchanged', 'published_at': '2026-05-11'}]
    def table(self, _): return MemoryQuery(self)


@pytest.mark.parametrize('action,reason,status', [('verify', None, 'verified'), ('oos', 'foreign', 'verified'), ('retag', None, 'pending')])
def test_review_actions_and_server_snapshot_undo(action, reason, status):
    db = MemoryDB()
    original = deepcopy(db.rows[0])
    service = ReviewService(db)
    response = service.act(1, action, reason)
    assert db.rows[0]['tagging_status'] == status
    assert db.rows[0]['file_path'] == original['file_path'] and db.rows[0]['caption'] == original['caption']
    if action == 'oos':
        assert db.rows[0]['stock_codes'] == [] and db.rows[0]['out_of_scope_reason'] == 'foreign'
    if action == 'retag':
        assert db.rows[0]['report_type'] is None and db.rows[0]['tagging_locked_at'] is None
    assert service.undo(response['undo_token']) == {'report_id': 1}
    for key, value in original.items():
        assert db.rows[0][key] == value
    with pytest.raises(HTTPException):
        service.undo(response['undo_token'])


def test_review_does_not_overwrite_subsequent_worker_or_review():
    db = MemoryDB(); service = ReviewService(db)
    result = service.act(1, 'retag')
    db.rows[0]['tagging_worker_id'] = 'another-worker'
    with pytest.raises(HTTPException) as exc:
        service.undo(result['undo_token'])
    assert exc.value.status_code == 409
    assert db.rows[0]['tagging_worker_id'] == 'another-worker'
    with pytest.raises(HTTPException):
        service.act(1, 'verify')


def test_new_routes_validate_before_mutating():
    service = SimpleNamespace(review=ReviewService(MemoryDB()), coverage_cache={})
    app.dependency_overrides[get_service] = lambda: service
    try:
        with TestClient(app) as client:
            assert client.get('/api/market?unit=bad').status_code == 422
            assert client.get('/api/market?days=-1').status_code == 422
            assert client.post('/api/review/1/action', json={'action': 'oos'}).status_code == 422
            assert client.post('/api/review/1/action', json={'action': 'destroy'}).status_code == 422
            saved = client.post('/api/review/1/action', json={'action': 'verify'}).json()
            assert client.post('/api/review/undo/'+saved['undo_token']).json() == {'report_id': 1}
    finally:
        app.dependency_overrides.clear()


def test_review_preview_caps_pages_and_serves_png(tmp_path):
    import pymupdf
    path = tmp_path / 'review.pdf'
    with pymupdf.open() as doc:
        doc.new_page().insert_text((50, 50), 'Review preview')
        doc.save(path)
    service = SimpleNamespace(cfg=SimpleNamespace(storage_base_dir=tmp_path),
                              review=SimpleNamespace(row=lambda rid: {'file_path': 'review.pdf'}))
    app.dependency_overrides[get_service] = lambda: service
    try:
        with TestClient(app) as client:
            assert client.get('/api/review/1/preview').json() == {'page_count': 1, 'preview_pages': 1}
            image = client.get('/api/review/1/pages/1')
            assert image.status_code == 200 and image.content.startswith(b'\x89PNG')
            assert client.get('/api/review/1/pages/2').status_code == 404
            assert client.get('/api/review/1/pages/4').status_code == 404
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_explicit_pair_narrative_reads_only_selected_reports(monkeypatch):
    from langgraph_tagger.workspace import service as module
    cfg = SimpleNamespace(llm_model='claude-haiku-5-5', per_report_timeout_s=90)
    monkeypatch.setattr(module, 'load_llm_summary_config', lambda: cfg)
    client = AsyncMock()
    monkeypatch.setattr(module, 'make_llm_client', lambda _: client)
    generate = AsyncMock(return_value=(SimpleNamespace(diff_narrative='Selected pair only'), 10, 10))
    save = Mock()
    monkeypatch.setattr(module, 'diff_one', generate)
    monkeypatch.setattr(module, 'update_diff', save)
    service = WorkspaceService.__new__(WorkspaceService)
    service.llm_semaphore = asyncio.Semaphore(2)
    service.sb = object()
    rows = {rid: {'id': rid, 'report_type': '단일종목', 'stock_codes': ['016360'],
                  'publisher': 'KB', 'published_at': date, 'summary': {'one_liner': str(rid)}}
            for rid, date in [(1, '2026-01-01'), (3, '2026-03-01')]}
    service.report = Mock(side_effect=lambda rid: rows[rid])
    result = await service.analyze_comparison(3, 1)
    assert result['narrative'] == 'Selected pair only'
    assert {c.args[0] for c in service.report.call_args_list} == {1, 3}
    assert generate.call_args.kwargs['prev_report_id'] == 1
    assert generate.call_args.kwargs['curr_summary'] == rows[3]['summary']
    assert save.call_args.args[1:3] == (3, 1)
