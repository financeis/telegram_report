"""Compare through the real reports and analysis windows, on an in-memory Supabase.

Only the AI boundary (``LLMClient.parse``) and the Supabase client (``core.db.supabase_client``)
are replaced. The reports feature keeps its process-wide service, and analysis its Supabase client
and in-flight set, in module globals; each test here starts them empty and they are put back
afterwards, so the stand-in never leaks into other tests.

Checks (spec §4, §6, §9.5, §9.6, §9.9, §13 item 3):
- the comparison, and its 404 for reports that are not in scope, come from the reports feature's
  own reads; only the two selected reports' summaries are read;
- a new narrative is saved through analysis: one update of report_summaries, on the later report,
  with exactly the four comparison fields; it then shows for that pair and is not written again;
- without DB settings both addresses show the reports feature's name; without a model key only a
  new narrative shows the analysis feature's name;
- two real analyses calling the AI hold both slots, and a compare narrative waits for one.
"""
from __future__ import annotations

import asyncio

import pymupdf
import pytest
from fastapi.testclient import TestClient

from research_desk.core import db as core_db
from research_desk.core.llm import LLMClient, StructuredResult
from research_desk.features import analysis, reports
from research_desk.features.analysis import service as analysis_service
from research_desk.features.compare import service as compare_service
from research_desk.features.compare.schemas import DiffResult
from research_desk.features.reports import service as reports_service

from .fakes import REPORT_NOT_FOUND, FakeSupabase, analysed, build_app, metric, never, tagged, until

URL, KEY = 'https://example.supabase.test', 'service-key'
LIMIT_S = 5
KEY_REASON = 'OPENAI_API_KEY가 설정되지 않았습니다. .env에 추가 후 분석 다시 시도하세요.'
DB_REASON = 'DB 접속 설정(SUPABASE_URL, SUPABASE_SERVICE_KEY)이 없습니다'
# the smallest ExtractionResult the analysis accepts
EXTRACTION = {'target_price_dir': 'N/A', 'recommendation': '매수', 'recommendation_dir': '유지',
              'one_line_summary': '분석 결과', 'positive_points': [], 'risk_points': [],
              'extraction_confidence': 'high'}


@pytest.fixture
def fresh_windows(monkeypatch):
    """The reports and analysis windows prepare afresh in this test (and are put back after)."""
    monkeypatch.setattr(reports_service, '_service', None)
    monkeypatch.setattr(analysis_service, '_supabase_client', None)
    monkeypatch.setattr(analysis_service, '_analyzing', set())


@pytest.fixture
def db(fresh_windows, monkeypatch) -> FakeSupabase:
    """DB settings set; core.db.supabase_client hands out the in-memory stand-in."""
    fake = FakeSupabase()
    monkeypatch.setenv('SUPABASE_URL', URL)
    monkeypatch.setenv('SUPABASE_SERVICE_KEY', KEY)
    monkeypatch.setattr(core_db, 'supabase_client', lambda url, key: fake)
    return fake


@pytest.fixture
def client():
    with TestClient(build_app()) as test_client:
        yield test_client


def summary_reads(db: FakeSupabase) -> list:
    return [q.values('in', 'report_id')[0] for q in db.queries('report_summaries')]


def test_the_comparison_and_its_narrative_end_to_end(db, client, monkeypatch):
    db.tables['reports'] = [
        tagged(1, '2026-02-09'), tagged(3, '2026-05-11'),
        tagged(5, None, status='verified', reason='foreign'), tagged(6, '2026-04-01', status='review_needed'),
    ]
    db.tables['report_summaries'] = [analysed(1, 70000, metric(100)), analysed(3, 85000, metric(120)),
                                     analysed(6, 1)]

    response = client.get('/api/compare?left=3&right=1')
    assert response.status_code == 200
    body = response.json()
    assert body['left'] == reports.public_report(tagged(1, '2026-02-09'), analysed(1, 70000, metric(100)))
    assert body['right']['id'] == 3 and body['same_publisher'] is True
    assert [(m['previous'], m['current']) for m in body['metrics']] == [(100, 120)]
    assert (body['narrative'], body['target_price_change']['change_label']) == (None, '+21.43%')
    assert 'file_path' not in response.text and '2026/1.pdf' not in response.text

    for rid in (5, 6, 9):   # out of scope, not final, no row
        response = client.get(f'/api/compare?left=1&right={rid}')
        assert (response.status_code, response.json()) == (404, {'detail': REPORT_NOT_FOUND})
    assert sorted(summary_reads(db)) == [[1], [1], [1], [1], [3]]   # never 5, 6 or 9

    # without a model key a new narrative is "analysis not ready" and nothing is written
    response = client.post('/api/compare/analyze', json={'left': 1, 'right': 3})
    assert response.status_code == 503
    assert response.json() == {'detail': f'분석 기능을 지금 쓸 수 없습니다: {KEY_REASON}'}
    assert db.queries('report_summaries', 'update') == []

    # with a key: written once, saved on the later report through analysis
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test')
    calls = []

    async def parse(self, **kwargs):
        calls.append(kwargs)
        return StructuredResult(parsed=DiffResult(diff_narrative='KB는 목표주가를 높였다.'))

    monkeypatch.setattr(LLMClient, 'parse', parse)
    body = client.post('/api/compare/analyze', json={'left': 1, 'right': 3}).json()
    assert body['narrative'] == 'KB는 목표주가를 높였다.'
    assert [(c['model'], c['schema'], c['constrained']) for c in calls] == [
        ('gpt-6-luna', DiffResult, True)]
    (update,) = db.queries('report_summaries', 'update')
    assert update.values('eq', 'report_id') == [3]
    assert update.payload == {
        'prev_report_id': 1, 'prev_match_type': 'same_publisher',
        'diff_narrative': 'KB는 목표주가를 높였다.',
        'comparison_details': {'metrics': body['metrics'], 'previous_publisher': 'KB',
                               'previous_published_at': '2026-02-09'},
    }
    # shown for this pair from now on, and not written again
    assert client.get('/api/compare?left=1&right=3').json()['narrative'] == 'KB는 목표주가를 높였다.'
    assert client.post('/api/compare/analyze', json={'left': 3, 'right': 1}).json()[
        'narrative'] == 'KB는 목표주가를 높였다.'
    assert len(calls) == 1 and len(db.queries('report_summaries', 'update')) == 1
    # compare itself reads and writes no table: every query came from reports or analysis
    assert {(q.table, q.kind) for q in db.executed} == {
        ('reports', 'select'), ('report_summaries', 'select'), ('report_summaries', 'update')}


def test_without_db_settings_both_addresses_show_the_reports_feature(fresh_windows, client):
    for response in (client.get('/api/compare?left=1&right=3'),
                     client.post('/api/compare/analyze', json={'left': 1, 'right': 3})):
        assert response.status_code == 503
        assert response.json() == {'detail': f'리포트 기능을 지금 쓸 수 없습니다: {DB_REASON}'}


async def test_two_real_analyses_make_a_compare_narrative_wait(db, tmp_path, monkeypatch):
    storage = tmp_path / 'reports'
    storage.mkdir()
    monkeypatch.setenv('STORAGE_BASE_DIR', str(storage))
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test')
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), 'Target 85,000')
        doc.save(str(storage / 'new.pdf'))
    db.tables['reports'] = [tagged(1, '2026-02-09'), tagged(3, '2026-05-11')]
    db.tables['report_summaries'] = [analysed(1, 70000), analysed(3, 85000)]
    gate = asyncio.Event()
    calls, state = [], {'active': 0, 'peak': 0}

    async def parse(self, **kwargs):
        schema = kwargs['schema']
        calls.append(schema.__name__)
        state['active'] += 1
        state['peak'] = max(state['peak'], state['active'])
        try:
            if schema is DiffResult:
                parsed = DiffResult(diff_narrative='분석 두 건이 끝난 뒤 작성')
            else:   # ExtractionResult: the analyses wait at the gate, inside their AI slots
                await gate.wait()
                parsed = schema.model_validate(EXTRACTION)
            return StructuredResult(parsed=parsed, input_tokens=1, output_tokens=1)
        finally:
            state['active'] -= 1

    monkeypatch.setattr(LLMClient, 'parse', parse)
    rows = [tagged(rid, '2026-06-01') | {'file_path': 'new.pdf'} for rid in (21, 22)]
    analyses = [asyncio.create_task(analysis.analyze_report(row)) for row in rows]
    await until(lambda: calls.count('ExtractionResult') == 2)   # both analyses call the AI
    narrative = asyncio.create_task(compare_service.analyze_comparison(3, 1))
    await until(lambda: [1] in summary_reads(db))   # compare has read both reports
    await never(lambda: 'DiffResult' in calls or narrative.done())
    gate.set()
    result = await asyncio.wait_for(narrative, LIMIT_S)
    assert result['narrative'] == '분석 두 건이 끝난 뒤 작성'
    done = await asyncio.wait_for(asyncio.gather(*analyses), LIMIT_S)
    assert [reused for _, reused in done] == [False, False]
    assert calls == ['ExtractionResult', 'ExtractionResult', 'DiffResult']
    assert state['peak'] == 2
    (update,) = db.queries('report_summaries', 'update')
    assert update.values('eq', 'report_id') == [3]
