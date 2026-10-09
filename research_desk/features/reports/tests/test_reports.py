"""features.reports: the report list, PDF and analyze routes, and the window other features use.

Ported from langgraph_tagger:
- workspace/tests/test_workspace.py::test_browser_report_omits_internal_paths
- workspace/tests/test_workspace.py::test_routes_and_explicit_analysis, the analyze-route part
  (the summary analysis returns comes back from POST /api/reports/{rid}/analyze)
- workspace/tests/test_migration.py::test_stock_activity_includes_industry_reports, the
  report-list part (industry reports are listed for the company)
- workspace/tests/test_migration.py::test_selected_cached_reports_only_and_non_company_rejection,
  the "only the requested report rows are read" part (the analysis part is in features/analysis)
- analytics/tests/test_config.py: a missing SUPABASE_URL / SUPABASE_SERVICE_KEY is now
  NotReady("리포트", …) instead of SystemExit (spec §10 item 7)

New (spec §6, §9.5, §9.9, §13 item 3): the public report shape, the zero-padded company lookup
with the code as received for the DB, list order and attached summaries, the 404 texts, the row
read (404) before analysis, readiness (NotReady, .env re-read, retry on the next request, the
stock list needed by the list route only, version warnings) and the window functions.

The DB is an in-memory read-only stand-in handed out by core.db.supabase_client. The analysis
window is replaced where this feature looks it up, except at the end, where the real analysis
window runs on the same stand-in. Every environment variable involved is set or deleted here.
"""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from research_desk.core import db as core_db
from research_desk.core.settings import NotReady
from research_desk.domain.stocks import version_file, write_version
from research_desk.features import analysis, reports
from research_desk.features.reports import public_report, router
from research_desk.features.reports import service as service_module
from research_desk.features.reports.service import ReportsService, get_service
from research_desk.features.reports.store import EXPECTED_COLS
from research_desk.features.reports.tests.fakes import FakeSupabase

ENV = ('SUPABASE_URL', 'SUPABASE_SERVICE_KEY', 'SUPABASE_DB_URL', 'STORAGE_BASE_DIR', 'KRX_CSV_PATH',
       'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'LLM_MODEL_PHASE2', 'OPENAI_MODEL_PHASE2',
       'PHASE2_SUMMARY_VERSION', 'PHASE2_PER_REPORT_TIMEOUT_S', 'PHASE2_MAX_INPUT_TOKENS', 'CODEX_BIN',
       'TELEGRAM_API_ID', 'TELEGRAM_API_HASH', 'TELEGRAM_CHANNEL', 'TELEGRAM_CHANNEL_ID')

URL, KEY = 'https://example.supabase.test', 'service-key'

PUBLIC_KEYS = ['id', 'title', 'file_name', 'published_at', 'publisher', 'report_type', 'stock_codes',
               'company_names', 'sectors_major', 'sectors_minor', 'products', 'summary', 'pdf_url']
# what analysis needs from the row (features/analysis: analyze_report)
ANALYSIS_KEYS = ('id', 'report_type', 'file_path', 'publisher', 'stock_codes', 'published_at', 'title')

REPORT_NOT_FOUND = {'detail': '기업 보고서를 찾을 수 없습니다.'}
STOCK_NOT_FOUND = {'detail': '종목을 찾을 수 없습니다.'}
DB_REASON = 'DB 접속 설정(SUPABASE_URL, SUPABASE_SERVICE_KEY)이 없습니다'
STOCKS_REASON = '종목표 파일을 읽을 수 없습니다'
DB_NOT_READY = {'detail': f'리포트 기능을 지금 쓸 수 없습니다: {DB_REASON}'}
STOCKS_NOT_READY = {'detail': f'리포트 기능을 지금 쓸 수 없습니다: {STOCKS_REASON}'}
NOT_IN_STORAGE = 'PDF를 찾을 수 없습니다.'
FILE_MISSING = '로컬 PDF 파일이 없습니다. 수집 상태를 확인해 주세요.'
LOGGER = 'research_desk.features.reports.service'
KST = ZoneInfo('Asia/Seoul')

# The first header cell has a line break inside quotes, like the real file.
HEADER_LINE = '"종목\n코드",종목명,시장,산업명(대),산업명(중),주요제품\n'
ROWS = ('005930,삼성전자,KOSPI,반도체,메모리반도체,DRAM/NAND\n'
        '016360,삼성증권,KOSPI,금융,증권,증권\n'
        '000020,동화약품,KOSPI,,,의약품\n')
SAMSUNG_SEC = {'code': '016360', 'name': '삼성증권', 'sector_major': '금융', 'sector_minor': '증권'}


# ── rows and files ───────────────────────────────────────────────────────────

def tagged(rid, *, published='2026-05-11', codes=('016360',), report_type='단일종목', status='auto',
           reason=None, **extra):
    """A reports row as the tagger leaves it, with collector columns the browser must never see."""
    return {'id': rid, 'published_at': published, 'sent_at': '2026-05-11T01:00:00+00:00',
            'report_type': report_type, 'publisher': 'KB', 'stock_codes': list(codes),
            'company_names': ['삼성증권'], 'sectors_major': ['금융'], 'sectors_minor': ['증권'],
            'products': ['증권'], 'tagging_status': status, 'out_of_scope_reason': reason,
            'file_path': f'2026/{rid}.pdf', 'file_name': f'{rid}_report.pdf', 'title': f'report {rid}',
            'publisher_type': 'broker', 'caption': 'internal caption', 'file_hash_sha256': 'ab' * 32,
            **extra}


def report(rid, published, publisher='KB', code='016360', summary=None):
    """The old test_workspace.py helper."""
    return {'id': rid, 'published_at': published, 'publisher': publisher,
            'stock_codes': [code], 'summary': summary}


def write_stock_csv(path: Path, text: str = HEADER_LINE + ROWS) -> Path:
    path.write_bytes(text.encode('utf-8'))
    return path


def write_pdf(storage: Path, relative: str, content: bytes = b'%PDF-1.4 test') -> bytes:
    path = storage / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return content


def ids(response_or_body) -> list[int]:
    body = response_or_body.json() if hasattr(response_or_body, 'json') else response_or_body
    return [r['id'] for r in body['reports']]


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """No settings at all unless a test sets them, and no real Supabase client, ever."""
    for name in ENV:
        monkeypatch.delenv(name, raising=False)

    def refuse(url, key):
        raise AssertionError('no real Supabase client in these tests')

    monkeypatch.setattr(core_db, 'supabase_client', refuse)


@pytest.fixture
def db(monkeypatch):
    """DB settings set; core.db.supabase_client hands out the stand-in and records each call."""
    fake = FakeSupabase(reports=[], report_summaries=[])
    fake.made = []

    def supabase_client(url, key):
        fake.made.append((url, key))
        return fake

    monkeypatch.setenv('SUPABASE_URL', URL)
    monkeypatch.setenv('SUPABASE_SERVICE_KEY', KEY)
    monkeypatch.setattr(core_db, 'supabase_client', supabase_client)
    return fake


@pytest.fixture
def stock_csv(tmp_path, monkeypatch) -> Path:
    """A small stock list with a matching version file, at KRX_CSV_PATH."""
    path = write_stock_csv(tmp_path / 'krx.csv')
    write_version(path, '2026-05-08')
    monkeypatch.setenv('KRX_CSV_PATH', str(path))
    return path


@pytest.fixture
def storage(tmp_path, monkeypatch) -> Path:
    folder = tmp_path / 'reports'
    folder.mkdir()
    monkeypatch.setenv('STORAGE_BASE_DIR', str(folder))
    return folder


@pytest.fixture
def summaries(monkeypatch):
    """analysis.summaries_for, replaced where this feature looks it up; records each call."""
    state = SimpleNamespace(saved={}, calls=[])

    def summaries_for(report_ids):
        report_ids = list(report_ids)
        state.calls.append(report_ids)
        return {rid: state.saved[rid] for rid in report_ids if rid in state.saved}

    monkeypatch.setattr(analysis, 'summaries_for', summaries_for)
    return state


@pytest.fixture
def analyze(monkeypatch):
    """analysis.analyze_report, replaced where this feature looks it up; records each row."""
    state = SimpleNamespace(rows=[], result=({'one_line_summary': '검증된 응답'}, False), error=None)

    async def analyze_report(row):
        state.rows.append(dict(row))
        if state.error is not None:
            raise state.error
        return state.result

    monkeypatch.setattr(analysis, 'analyze_report', analyze_report)
    return state


def build_app(service: ReportsService) -> FastAPI:
    """This feature's router plus the NotReady → 503 answer the web app adds (spec §6)."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_service] = lambda: service

    @app.exception_handler(NotReady)
    async def not_ready(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=503)

    return app


@pytest.fixture
def service() -> ReportsService:
    return ReportsService()


@pytest.fixture
def client(service):
    with TestClient(build_app(service)) as test_client:
        yield test_client


@pytest.fixture
def window(monkeypatch):
    """The window's process-wide service starts fresh here and is put back after the test."""
    monkeypatch.setattr(service_module, '_service', None)


# ── the window and the addresses ─────────────────────────────────────────────

def test_window_exposes_the_public_names():
    assert isinstance(reports.router, APIRouter)
    for name in ('report_row', 'get_report', 'public_report', 'period_rows', 'stock_rows',
                 'rows_for_stocks', 'latest_report_sent_at', 'tagging_in_progress'):
        assert callable(getattr(reports, name)), name


def test_the_router_serves_exactly_the_three_addresses():
    assert sorted((sorted(r.methods), r.path) for r in router.routes) == [
        (['GET'], '/api/reports/{rid}/pdf'),
        (['GET'], '/api/stocks/{code}/reports'),
        (['POST'], '/api/reports/{rid}/analyze'),
    ]
    # the old handler names, so these /openapi.json entries stay the same
    assert sorted(r.name for r in router.routes) == ['analyze', 'pdf', 'stock_reports']


def test_a_report_id_must_be_a_number(client, db, analyze):
    assert client.get('/api/reports/abc/pdf').status_code == 422
    assert client.post('/api/reports/abc/analyze').status_code == 422
    assert db.executed == [] and analyze.rows == []


# ── the public report shape (spec §6) ────────────────────────────────────────

def test_browser_report_omits_internal_paths():
    # ported: test_workspace.py::test_browser_report_omits_internal_paths
    row = report(4, '2026-05-11') | {'file_path': 'private/location.pdf', 'caption': 'internal metadata'}
    value = public_report(row, None)
    assert 'file_path' not in value and 'caption' not in value
    assert value['pdf_url'] == '/api/reports/4/pdf'


def test_public_report_has_exactly_the_contract_keys_in_order():
    summary = {'one_line_summary': '요약'}
    value = public_report(tagged(7), summary)
    assert list(value) == PUBLIC_KEYS
    assert value == {
        'id': 7, 'title': 'report 7', 'file_name': '7_report.pdf', 'published_at': '2026-05-11',
        'publisher': 'KB', 'report_type': '단일종목', 'stock_codes': ['016360'],
        'company_names': ['삼성증권'], 'sectors_major': ['금융'], 'sectors_minor': ['증권'],
        'products': ['증권'], 'summary': summary, 'pdf_url': '/api/reports/7/pdf',
    }


def test_public_report_shows_missing_columns_as_null():
    assert public_report({'id': 5}, None) == dict.fromkeys(PUBLIC_KEYS[:-1]) | {'id': 5, 'pdf_url': '/api/reports/5/pdf'}


def test_the_public_shape_stays_13_keys_without_publisher_type(client, db, stock_csv, summaries, analyze):
    # the rows read now carry publisher_type (16 columns); the browser shape does not change
    row = tagged(1, publisher_type='data_provider')
    assert 'publisher_type' in row and list(public_report(row, None)) == PUBLIC_KEYS
    db.tables['reports'] = [row]
    listed = client.get('/api/stocks/016360/reports').json()['reports']
    analyzed = client.post('/api/reports/1/analyze').json()
    assert [list(r) for r in listed] == [PUBLIC_KEYS]
    assert list(analyzed) == PUBLIC_KEYS + ['analysis_reused']
    assert len(PUBLIC_KEYS) == 13


def test_browser_responses_never_carry_paths_or_keys(client, db, stock_csv, storage, summaries, analyze):
    db.tables['reports'] = [tagged(1)]
    for response in (client.get('/api/stocks/016360/reports'), client.post('/api/reports/1/analyze')):
        assert response.status_code == 200
        text = response.text
        for secret in ('file_path', '2026/1.pdf', 'internal caption', 'file_hash', 'ab' * 32,
                       KEY, URL, str(storage), storage.as_posix()):
            assert secret not in text, secret


# ── GET /api/stocks/{code}/reports ───────────────────────────────────────────

def test_the_list_shows_the_company_and_its_reports_newest_first(client, db, stock_csv, summaries):
    db.tables['reports'] = [tagged(1, published='2026-02-09'), tagged(2, published='2026-05-11'),
                            tagged(3, published='2026-05-11'), tagged(4, published='2026-03-01')]
    saved = {'report_id': 3, 'one_line_summary': '저장된 분석'}
    summaries.saved = {3: saved}

    body = client.get('/api/stocks/016360/reports').json()

    assert list(body) == ['stock', 'reports']
    assert body['stock'] == SAMSUNG_SEC
    assert ids(body) == [3, 2, 4, 1]   # published_at descending, then id descending
    assert body['reports'][0] == public_report(tagged(3), saved)
    assert [r['summary'] for r in body['reports']] == [saved, None, None, None]
    assert all(list(r) == PUBLIC_KEYS for r in body['reports'])
    assert summaries.calls == [[3, 2, 4, 1]]   # one call for the listed reports only
    (query,) = db.queries('reports')
    assert query.filter_values('contains', 'stock_codes') == [['016360']]
    assert query.filter_values('gte', 'published_at') == ['2000-01-01']


def test_industry_reports_are_listed_for_the_company(client, db, stock_csv, summaries):
    # ported: test_migration.py::test_stock_activity_includes_industry_reports (report-list part)
    db.tables['reports'] = [tagged(1), tagged(2, report_type='산업')]
    reports_ = client.get('/api/stocks/016360/reports').json()['reports']
    assert {r['report_type'] for r in reports_} == {'단일종목', '산업'}


def test_only_in_scope_rows_since_2000_holding_the_code_are_listed(client, db, stock_csv, summaries):
    db.tables['reports'] = [
        tagged(1), tagged(6, status='verified'),               # listed
        tagged(2, status='review_needed'), tagged(3, status='pending'),
        tagged(4, status='verified', reason='foreign'), tagged(5, reason='ir_self'),
        tagged(7, published='1999-12-31'), tagged(8, codes=('005930',)),
    ]
    assert ids(client.get('/api/stocks/016360/reports')) == [6, 1]


def test_a_short_code_is_zero_padded_for_the_company_and_given_as_is_to_the_db(client, db, stock_csv,
                                                                                summaries):
    db.tables['reports'] = [tagged(1), tagged(2, codes=('16360',))]
    body = client.get('/api/stocks/16360/reports').json()
    assert body['stock'] == SAMSUNG_SEC
    (query,) = db.queries('reports')
    assert query.filter_values('contains', 'stock_codes') == [['16360']]
    assert ids(body) == [2]   # the DB matched the code as received


@pytest.mark.parametrize('code', ['999999', '16361', '0016360', 'abc', '005930x'])
def test_an_unknown_company_is_404_and_reads_no_reports(client, db, stock_csv, summaries, code):
    response = client.get(f'/api/stocks/{code}/reports')
    assert response.status_code == 404
    assert response.json() == STOCK_NOT_FOUND
    assert db.executed == [] and summaries.calls == []


def test_empty_sector_cells_are_empty_strings_and_no_reports_is_an_empty_list(client, db, stock_csv,
                                                                              summaries):
    body = client.get('/api/stocks/20/reports').json()
    assert body == {'stock': {'code': '000020', 'name': '동화약품', 'sector_major': '', 'sector_minor': ''},
                    'reports': []}


def test_the_list_needs_no_ai_key_telegram_or_db_url(client, db, stock_csv, summaries):
    # analytics/tests/test_config.py::test_does_not_require_openai_or_telegram, at the feature level
    db.tables['reports'] = [tagged(1)]
    assert ids(client.get('/api/stocks/016360/reports')) == [1]
    assert db.made == [(URL, KEY)]


# ── GET /api/reports/{rid}/pdf ───────────────────────────────────────────────

def test_the_pdf_of_an_in_scope_report_is_served(client, db, storage):
    content = write_pdf(storage, '2026/7.pdf')
    db.tables['reports'] = [tagged(7)]
    response = client.get('/api/reports/7/pdf')
    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/pdf'
    assert response.content == content
    (query,) = db.queries('reports')
    assert query.filter_values('eq', 'id') == [7]


@pytest.mark.parametrize('row', [
    tagged(7, status='review_needed'), tagged(7, status='pending'), tagged(7, status='processing'),
    tagged(7, status='verified', reason='ir_self'), tagged(7, reason='foreign'), tagged(8),
], ids=['review_needed', 'pending', 'processing', 'oos verified', 'oos auto', 'another id'])
def test_no_pdf_without_an_in_scope_row(client, db, storage, row):
    write_pdf(storage, '2026/7.pdf')
    db.tables['reports'] = [row]
    response = client.get('/api/reports/7/pdf')
    assert response.status_code == 404
    assert response.json() == REPORT_NOT_FOUND


@pytest.mark.parametrize('file_path, detail', [
    ('../outside.pdf', NOT_IN_STORAGE),
    ('{outside}', NOT_IN_STORAGE),          # an absolute path elsewhere
    ('../.env', NOT_IN_STORAGE),
    ('notes.txt', NOT_IN_STORAGE),
    ('missing.pdf', FILE_MISSING),
])
def test_pdf_paths_stay_inside_the_storage_folder(client, db, storage, file_path, detail):
    outside = storage.parent / 'outside.pdf'
    outside.write_bytes(b'%PDF-1.4')
    (storage.parent / '.env').write_text('SECRET=1', encoding='utf-8')
    (storage / 'notes.txt').write_text('x', encoding='utf-8')
    db.tables['reports'] = [tagged(8, file_path=file_path.format(outside=outside))]
    response = client.get('/api/reports/8/pdf')
    assert response.status_code == 404
    assert response.json() == {'detail': detail}


def test_the_storage_folder_defaults_to_reports_in_the_current_folder(client, db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    content = write_pdf(tmp_path / 'reports', 'x.pdf', b'%PDF-1.4 default folder')
    db.tables['reports'] = [tagged(9, file_path='x.pdf')]
    assert client.get('/api/reports/9/pdf').content == content


def test_the_pdf_needs_no_stock_list(client, db, storage, tmp_path, monkeypatch):
    monkeypatch.setenv('KRX_CSV_PATH', str(tmp_path / 'missing.csv'))
    content = write_pdf(storage, '2026/7.pdf')
    db.tables['reports'] = [tagged(7)]
    assert client.get('/api/reports/7/pdf').content == content


# ── POST /api/reports/{rid}/analyze ──────────────────────────────────────────

def test_the_analysis_answer_is_the_public_report_with_the_summary(client, db, analyze):
    # ported: test_workspace.py::test_routes_and_explicit_analysis (analyze part)
    db.tables['reports'] = [tagged(1)]
    response = client.post('/api/reports/1/analyze')
    assert response.status_code == 200
    body = response.json()
    assert body['summary']['one_line_summary'] == '검증된 응답'
    assert body == public_report(tagged(1), {'one_line_summary': '검증된 응답'}) | {'analysis_reused': False}
    assert list(body) == PUBLIC_KEYS + ['analysis_reused']


@pytest.mark.parametrize('reused', [True, False])
def test_analysis_reused_comes_from_analysis(client, db, analyze, reused):
    db.tables['reports'] = [tagged(1)]
    analyze.result = ({'report_id': 1, 'financial_details': {'metrics': []}}, reused)
    body = client.post('/api/reports/1/analyze').json()
    assert body['analysis_reused'] is reused
    assert body['summary'] == {'report_id': 1, 'financial_details': {'metrics': []}}


def test_analysis_gets_the_row_this_feature_read(client, db, analyze):
    db.tables['reports'] = [tagged(1)]
    client.post('/api/reports/1/analyze')
    (row,) = analyze.rows
    assert set(row) == set(EXPECTED_COLS)
    assert {k: row[k] for k in ANALYSIS_KEYS} == {k: tagged(1)[k] for k in ANALYSIS_KEYS}


@pytest.mark.parametrize('row', [None, tagged(1, status='review_needed'),
                                 tagged(1, status='verified', reason='foreign')],
                         ids=['no row', 'not final', 'out of scope'])
def test_a_missing_report_is_404_before_analysis(client, db, analyze, row):
    # spec §9.5: the row is checked first (404), even when analysis would answer 409
    analyze.error = HTTPException(409, '이 보고서는 분석 중입니다. 잠시 후 새로고침해 주세요.')
    db.tables['reports'] = [row] if row else []
    response = client.post('/api/reports/1/analyze')
    assert response.status_code == 404
    assert response.json() == REPORT_NOT_FOUND
    assert analyze.rows == []


KEY_REASON = 'OPENAI_API_KEY가 설정되지 않았습니다. .env에 추가 후 분석 다시 시도하세요.'


@pytest.mark.parametrize('error, status, detail', [
    (HTTPException(409, '이 보고서는 분석 중입니다. 잠시 후 새로고침해 주세요.'), 409,
     '이 보고서는 분석 중입니다. 잠시 후 새로고침해 주세요.'),
    (HTTPException(422, '금융 정보 분석은 단일종목 보고서를 선택해 주세요.'), 422,
     '금융 정보 분석은 단일종목 보고서를 선택해 주세요.'),
    (HTTPException(422, 'PDF에서 읽을 수 있는 텍스트가 없습니다.'), 422, 'PDF에서 읽을 수 있는 텍스트가 없습니다.'),
    (HTTPException(404, NOT_IN_STORAGE), 404, NOT_IN_STORAGE),
    (NotReady('분석', KEY_REASON), 503, f'분석 기능을 지금 쓸 수 없습니다: {KEY_REASON}'),
], ids=['409', '422 type', '422 text', '404 pdf', 'analysis not ready'])
def test_analysis_errors_reach_the_browser_unchanged(client, db, analyze, error, status, detail):
    db.tables['reports'] = [tagged(1)]
    analyze.error = error
    response = client.post('/api/reports/1/analyze')
    assert response.status_code == status
    assert response.json() == {'detail': detail}


async def test_only_the_requested_report_rows_are_read(db, analyze):
    # ported: test_migration.py::test_selected_cached_reports_only_and_non_company_rejection (row reads)
    db.tables['reports'] = [tagged(rid) for rid in (1, 2, 3, 9)]
    analyze.result = ({'financial_details': {'metrics': []}}, True)
    service = ReportsService()
    results = await asyncio.gather(service.analyze(1), service.analyze(3))
    assert [r['id'] for r in results] == [1, 3]
    assert all(r['analysis_reused'] for r in results)
    assert sorted(q.filter_values('eq', 'id')[0] for q in db.queries('reports')) == [1, 3]
    assert sorted(row['id'] for row in analyze.rows) == [1, 3]


def test_analyze_needs_no_stock_list(client, db, analyze, tmp_path, monkeypatch):
    monkeypatch.setenv('KRX_CSV_PATH', str(tmp_path / 'missing.csv'))
    db.tables['reports'] = [tagged(1)]
    assert client.post('/api/reports/1/analyze').status_code == 200


# ── readiness: DB settings (spec §9.9, §10 item 7) ───────────────────────────

@pytest.mark.parametrize('missing', ['SUPABASE_URL', 'SUPABASE_SERVICE_KEY'])
@pytest.mark.parametrize('value', [None, ''], ids=['unset', 'empty'])
def test_a_missing_db_setting_is_not_ready(service, monkeypatch, missing, value):
    # ported: analytics/tests/test_config.py::test_missing_supabase_url_exits and
    # ::test_missing_supabase_key_exits — NotReady("리포트", …) instead of SystemExit
    monkeypatch.setenv('SUPABASE_URL', URL)
    monkeypatch.setenv('SUPABASE_SERVICE_KEY', KEY)
    if value is None:
        monkeypatch.delenv(missing)
    else:
        monkeypatch.setenv(missing, value)
    with pytest.raises(NotReady) as exc:
        service.report_row(1)
    assert (exc.value.area, exc.value.reason) == ('리포트', DB_REASON)
    assert missing in str(exc.value)
    assert str(exc.value) == DB_NOT_READY['detail']


def test_without_db_settings_every_address_is_503_with_the_reason(client, stock_csv, storage, analyze,
                                                                  summaries):
    write_pdf(storage, '2026/1.pdf')
    responses = (client.get('/api/stocks/016360/reports'), client.get('/api/reports/1/pdf'),
                 client.post('/api/reports/1/analyze'))
    for response in responses:
        assert response.status_code == 503
        assert response.json() == DB_NOT_READY   # a fixed sentence: no URL, no key
    assert analyze.rows == [] and summaries.calls == []


WINDOW_CALLS = {
    'report_row': lambda: reports.report_row(1),
    'get_report': lambda: reports.get_report(1),
    'period_rows': lambda: reports.period_rows('2026-01-01', False),
    'period_rows with oos': lambda: reports.period_rows('2026-01-01', True),
    'stock_rows': lambda: reports.stock_rows('016360', '2026-01-01'),
    'rows_for_stocks': lambda: reports.rows_for_stocks(['016360', '005930'], '2026-01-01'),
    'rows_for_stocks without codes': lambda: reports.rows_for_stocks([], '2026-01-01'),
    'latest_report_sent_at': lambda: reports.latest_report_sent_at(),
    'tagging_in_progress': lambda: reports.tagging_in_progress(),
}


@pytest.mark.parametrize('call', list(WINDOW_CALLS.values()), ids=list(WINDOW_CALLS))
def test_without_db_settings_the_window_is_not_ready(window, summaries, call):
    # spec §9.9: a feature using this window sees the reports feature's name
    with pytest.raises(NotReady) as exc:
        call()
    assert (exc.value.area, exc.value.reason) == ('리포트', DB_REASON)
    assert summaries.calls == []


def test_db_settings_fixed_later_work_without_a_restart(client, db, storage, monkeypatch):
    content = write_pdf(storage, '2026/7.pdf')
    db.tables['reports'] = [tagged(7)]
    monkeypatch.delenv('SUPABASE_SERVICE_KEY')
    assert client.get('/api/reports/7/pdf').status_code == 503
    assert client.get('/api/reports/7/pdf').status_code == 503
    monkeypatch.setenv('SUPABASE_SERVICE_KEY', KEY)
    assert client.get('/api/reports/7/pdf').content == content
    assert db.made == [(URL, KEY)]


def test_db_settings_added_to_env_file_are_used_on_the_next_request(env_file, client, db, storage,
                                                                     monkeypatch):
    content = write_pdf(storage, '2026/7.pdf')
    db.tables['reports'] = [tagged(7)]
    monkeypatch.delenv('SUPABASE_URL')
    monkeypatch.delenv('SUPABASE_SERVICE_KEY')
    assert client.get('/api/reports/7/pdf').json() == DB_NOT_READY
    env_file.write_text('SUPABASE_URL=https://from-env-file.test\nSUPABASE_SERVICE_KEY=k\n', encoding='utf-8')
    assert client.get('/api/reports/7/pdf').content == content
    assert db.made == [('https://from-env-file.test', 'k')]


def test_the_db_client_is_prepared_once(service, db):
    db.tables['reports'] = [tagged(1)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda _: service.report_row(1), range(16)))
    assert {row['id'] for row in rows} == {1}
    assert db.made == [(URL, KEY)]


def test_creating_the_service_reads_nothing(window):
    # no settings at all, and core.db.supabase_client refuses: still no error
    created = ReportsService()
    assert get_service() is get_service()
    assert isinstance(created, ReportsService)


# ── readiness: the stock list (spec §9.9, §8) ────────────────────────────────

UNREADABLE = {
    'missing': lambda path: None,
    'wrong header': lambda path: write_stock_csv(path, HEADER_LINE.replace('종목명', '회사명') + ROWS),
    'not utf-8': lambda path: path.write_bytes((HEADER_LINE + ROWS).encode('cp949')),
    'empty': lambda path: path.write_bytes(b''),
    'a folder': lambda path: path.mkdir(),
}


@pytest.mark.parametrize('make', list(UNREADABLE.values()), ids=list(UNREADABLE))
def test_an_unreadable_stock_list_stops_only_the_list(client, db, storage, analyze, summaries, tmp_path,
                                                      monkeypatch, make):
    path = tmp_path / 'krx.csv'
    make(path)
    monkeypatch.setenv('KRX_CSV_PATH', str(path))
    response = client.get('/api/stocks/016360/reports')
    assert response.status_code == 503
    assert response.json() == STOCKS_NOT_READY   # a fixed sentence: no path, no cause
    assert db.executed == [] and summaries.calls == []
    # the PDF and analyze addresses do not need the stock list
    content = write_pdf(storage, '2026/7.pdf')
    db.tables['reports'] = [tagged(7)]
    assert client.get('/api/reports/7/pdf').content == content
    assert client.post('/api/reports/7/analyze').status_code == 200


def test_db_settings_are_checked_before_the_stock_list(client, tmp_path, monkeypatch):
    # like the old service, which read the DB settings before the stock list
    monkeypatch.setenv('KRX_CSV_PATH', str(tmp_path / 'missing.csv'))
    assert client.get('/api/stocks/016360/reports').json() == DB_NOT_READY


def test_the_cause_of_an_unreadable_stock_list_goes_to_the_local_log(client, db, tmp_path, monkeypatch,
                                                                     caplog):
    missing = tmp_path / 'missing.csv'
    monkeypatch.setenv('KRX_CSV_PATH', str(missing))
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert client.get('/api/stocks/016360/reports').json() == STOCKS_NOT_READY
    assert any(str(missing) in r.getMessage() for r in caplog.records if r.name == LOGGER)


def test_the_stock_list_is_tried_again_on_the_next_request(client, db, summaries, tmp_path, monkeypatch):
    path = tmp_path / 'krx.csv'
    monkeypatch.setenv('KRX_CSV_PATH', str(path))
    assert client.get('/api/stocks/016360/reports').status_code == 503
    assert client.get('/api/stocks/016360/reports').status_code == 503
    write_stock_csv(path)   # fill in the missing file: no restart needed
    assert client.get('/api/stocks/016360/reports').json() == {'stock': SAMSUNG_SEC, 'reports': []}


def test_a_stock_list_path_added_to_env_is_used_on_the_next_request(env_file, client, db, summaries, tmp_path,
                                                                     monkeypatch):
    monkeypatch.chdir(tmp_path)   # the default docs/stock_data/... path does not exist here
    assert client.get('/api/stocks/016360/reports').json() == STOCKS_NOT_READY
    listed = write_stock_csv(tmp_path / 'listed.csv')
    env_file.write_text(f'KRX_CSV_PATH={listed.as_posix()}\n', encoding='utf-8')
    assert client.get('/api/stocks/016360/reports').json()['stock'] == SAMSUNG_SEC


def test_the_stock_list_is_read_once_and_kept(client, db, stock_csv, summaries):
    first = client.get('/api/stocks/016360/reports').json()
    stock_csv.unlink()
    version_file(stock_csv).unlink()
    assert client.get('/api/stocks/016360/reports').json() == first


def _mismatch(csv_path: Path) -> None:
    write_stock_csv(csv_path, HEADER_LINE + ROWS.replace('DRAM/NAND', 'DRAM'))


VERSION_PROBLEMS = {
    'missing': (lambda csv_path: version_file(csv_path).unlink(), '버전 정보 파일이 없습니다'),
    'mismatch': (_mismatch, '내용이 버전 정보와 다릅니다'),
    'invalid': (lambda csv_path: version_file(csv_path).write_text('not json', encoding='utf-8'),
                '버전 정보 파일을 읽을 수 없습니다'),
}


@pytest.mark.parametrize('break_version, text', list(VERSION_PROBLEMS.values()), ids=list(VERSION_PROBLEMS))
def test_a_stock_list_version_problem_only_logs_a_warning(client, db, stock_csv, summaries, caplog,
                                                          break_version, text):
    break_version(stock_csv)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        response = client.get('/api/stocks/016360/reports')
    assert response.status_code == 200
    assert response.json()['stock'] == SAMSUNG_SEC
    (record,) = [r for r in caplog.records if r.name == LOGGER]
    assert record.levelno == logging.WARNING
    assert text in record.getMessage()


def test_a_matching_stock_list_version_logs_nothing(client, db, stock_csv, summaries, caplog):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert client.get('/api/stocks/016360/reports').status_code == 200
    assert [r for r in caplog.records if r.name == LOGGER] == []


# ── the window other features use ────────────────────────────────────────────

def test_report_row_is_the_in_scope_row_with_its_internal_columns(window, db):
    db.tables['reports'] = [tagged(1), tagged(2, status='review_needed'), tagged(3, reason='fund')]
    row = reports.report_row(1)
    assert set(row) == set(EXPECTED_COLS)
    assert row['file_path'] == '2026/1.pdf'   # internal: other features never send it to the browser
    for rid in (2, 3, 4):
        with pytest.raises(HTTPException) as exc:
            reports.report_row(rid)
        assert (exc.value.status_code, exc.value.detail) == (404, REPORT_NOT_FOUND['detail'])


def test_get_report_is_the_public_shape_with_that_reports_summary(window, db, summaries):
    db.tables['reports'] = [tagged(1), tagged(2, status='pending')]
    saved = {'report_id': 1, 'target_price_new': 85000}
    summaries.saved = {1: saved}
    assert reports.get_report(1) == public_report(tagged(1), saved)
    assert summaries.calls == [[1]]
    with pytest.raises(HTTPException) as exc:
        reports.get_report(2)
    assert (exc.value.status_code, exc.value.detail) == (404, REPORT_NOT_FOUND['detail'])
    assert summaries.calls == [[1]]   # no summary read for a report that is not there


def test_period_rows_and_stock_rows_are_frames_of_the_sixteen_columns(window, db):
    db.tables['reports'] = [
        tagged(1, published='2026-04-01'),
        tagged(2, published='2026-05-11', codes=('005930',), publisher_type=None),
        tagged(3, status='verified', reason='ir_self', published=None,   # sent_at KST 2026-05-11
               publisher_type='other'),
        tagged(4, status='review_needed'),
    ]
    assert list(reports.period_rows('2026-05-01', False)['id']) == [2]
    assert list(reports.period_rows('2026-05-01', True)['id']) == [2, 3]
    assert list(reports.period_rows('2026-01-01', False)['id']) == [1, 2]
    assert list(reports.stock_rows('016360', '2026-01-01')['id']) == [1]
    assert list(reports.stock_rows('16360', '2026-01-01')['id']) == []   # no zero-padding for the DB
    for frame in (reports.period_rows('2026-05-01', True), reports.stock_rows('999999', '2026-01-01')):
        assert list(frame.columns) == list(EXPECTED_COLS)
    assert len(EXPECTED_COLS) == 16 and EXPECTED_COLS[-1] == 'publisher_type'
    # publisher_type comes through as stored, empty included
    assert list(reports.period_rows('2026-01-01', True)['publisher_type']) == ['broker', None, 'other']
    assert reports.report_row(1)['publisher_type'] == 'broker'


def test_the_window_needs_no_stock_list(window, db, summaries, tmp_path, monkeypatch):
    monkeypatch.setenv('KRX_CSV_PATH', str(tmp_path / 'missing.csv'))
    db.tables['reports'] = [tagged(1)]
    assert reports.report_row(1)['id'] == 1
    assert reports.get_report(1)['id'] == 1
    assert list(reports.period_rows('2026-01-01', True)['id']) == [1]
    assert list(reports.stock_rows('016360', '2026-01-01')['id']) == [1]
    assert list(reports.rows_for_stocks(['016360'], '2026-01-01')['id']) == [1]
    assert reports.latest_report_sent_at() == datetime(2026, 5, 11, 1, tzinfo=timezone.utc)
    assert reports.tagging_in_progress() is False


def test_the_window_uses_one_service_prepared_once(window, db):
    db.tables['reports'] = [tagged(1)]
    reports.report_row(1)
    reports.period_rows('2026-01-01', False)
    reports.stock_rows('016360', '2026-01-01')
    reports.rows_for_stocks(['016360'], '2026-01-01')
    reports.latest_report_sent_at()
    reports.tagging_in_progress()
    assert db.made == [(URL, KEY)]
    assert get_service() is get_service()


def test_latest_report_sent_at_is_when_the_newest_in_scope_report_arrived(window, db):
    assert reports.latest_report_sent_at() is None   # no in-scope row yet
    db.tables['reports'] = [
        tagged(1, sent_at='2026-05-10T01:00:00+00:00'),
        tagged(2, sent_at='2026-05-11T15:30:00.123456+00:00'),
        tagged(3, status='verified', reason='foreign', sent_at='2026-05-12T00:00:00+00:00'),
        tagged(4, status='processing', sent_at='2026-05-13T00:00:00+00:00'),
    ]
    latest = reports.latest_report_sent_at()
    assert latest == datetime(2026, 5, 11, 15, 30, 0, 123456, tzinfo=timezone.utc)
    assert latest.astimezone(KST).isoformat() == '2026-05-12T00:30:00.123456+09:00'
    for query in db.queries('reports'):   # one row each time, with the 16 columns
        assert (query.limit_size, query.columns) == (1, ', '.join(EXPECTED_COLS))


@pytest.mark.parametrize('stored, expected', [
    ('2026-05-11T01:00:00+00:00', datetime(2026, 5, 11, 1, tzinfo=timezone.utc)),
    ('2026-05-11T01:00:00Z', datetime(2026, 5, 11, 1, tzinfo=timezone.utc)),
    ('2026-05-11T10:00:00+09:00', datetime(2026, 5, 11, 1, tzinfo=timezone.utc)),
    ('2026-05-11T01:00:00.5+00:00', datetime(2026, 5, 11, 1, 0, 0, 500000, tzinfo=timezone.utc)),
    ('2026-05-11T01:00:00', datetime(2026, 5, 11, 1, tzinfo=timezone.utc)),   # no zone given: UTC
])
def test_latest_report_sent_at_is_an_aware_datetime(window, db, stored, expected):
    db.tables['reports'] = [tagged(1, sent_at=stored)]
    latest = reports.latest_report_sent_at()
    assert latest == expected and latest.utcoffset() is not None


# ── is the tagger working? ───────────────────────────────────────────────────

NOW = datetime(2026, 10, 9, 3, 0, tzinfo=timezone.utc)


def locked(rid, at, status='processing'):
    """A row a tagger took at ``at`` (an ISO time in UTC, or None)."""
    return tagged(rid, status=status, published=None, codes=(), tagging_locked_at=at)


@pytest.mark.parametrize('at, expected', [
    ('2026-10-09T02:59:59+00:00', True),
    ('2026-10-09T02:30:00+00:00', True),    # exactly 30 minutes before now
    ('2026-10-09T02:29:59+00:00', False),   # 30 minutes and 1 second
    (None, False),                          # no lock time
], ids=['just now', '30 min', '30 min 1 s', 'no lock time'])
def test_tagging_in_progress_looks_back_30_minutes_by_default(service, db, at, expected):
    db.tables['reports'] = [locked(1, at)]
    assert service.tagging_in_progress(now=NOW) is expected


def test_tagging_in_progress_ignores_rows_that_are_not_processing(service, db):
    fresh = '2026-10-09T02:59:00+00:00'
    db.tables['reports'] = [locked(n, fresh, status=status)
                            for n, status in enumerate(('pending', 'auto', 'review_needed', 'verified'), 1)]
    assert service.tagging_in_progress(now=NOW) is False
    db.tables['reports'].append(locked(9, fresh))
    assert service.tagging_in_progress(now=NOW) is True


def test_tagging_in_progress_takes_the_minutes_and_any_time_zone(service, db):
    db.tables['reports'] = [locked(1, '2026-10-09T02:55:00+00:00')]
    assert service.tagging_in_progress(5, now=NOW) is True
    assert service.tagging_in_progress(4, now=NOW) is False
    assert service.tagging_in_progress(5, now=NOW.astimezone(KST)) is True   # the same instant
    # the cutoff goes to the DB in UTC, like the stored lock times
    assert [q.filter_values('gte', 'tagging_locked_at') for q in db.queries('reports')] == [
        ['2026-10-09T02:55:00+00:00'], ['2026-10-09T02:56:00+00:00'], ['2026-10-09T02:55:00+00:00']]


def test_tagging_in_progress_counts_without_reading_rows(service, db):
    db.tables['reports'] = [locked(1, '2026-10-09T02:59:00+00:00'), locked(2, '2026-10-09T02:58:00+00:00')]
    assert service.tagging_in_progress(now=NOW) is True
    (query,) = db.queries('reports')
    assert (query.columns, query.count, query.head) == ('id', 'exact', True)


def test_tagging_in_progress_through_the_window(window, db):
    assert reports.tagging_in_progress() is False   # no rows at all
    now = datetime.now(timezone.utc)
    db.tables['reports'] = [locked(1, (now - timedelta(hours=2)).isoformat())]
    assert reports.tagging_in_progress() is False   # a lock older than 30 minutes
    assert reports.tagging_in_progress(minutes=180) is True
    db.tables['reports'].append(locked(2, (now - timedelta(minutes=1)).isoformat()))
    assert reports.tagging_in_progress() is True


def test_rows_for_stocks_is_a_frame_of_the_sixteen_columns(window, db):
    db.tables['reports'] = [
        tagged(1, publisher_type=None),
        tagged(2, codes=('005930', '000660')),
        tagged(3, codes=('000660',), published='2025-12-31'),            # before the start day
        tagged(4, codes=('000660',), status='verified', reason='foreign'),
        tagged(5, codes=('035420',)),
    ]
    df = reports.rows_for_stocks(['016360', '000660'], '2026-01-01')
    assert list(df.columns) == list(EXPECTED_COLS)
    assert list(df['id']) == [1, 2]
    assert list(df['publisher_type']) == [None, 'broker']
    assert list(reports.rows_for_stocks(['16360'], '2026-01-01')['id']) == []   # no zero-padding
    for empty in (reports.rows_for_stocks(['999999'], '2026-01-01'), reports.rows_for_stocks([], '2026-01-01')):
        assert list(empty.columns) == list(EXPECTED_COLS) and len(empty) == 0


# ── with the real analysis window (same DB stand-in) ─────────────────────────

@pytest.fixture
def real_analysis(monkeypatch, db):
    """The real analysis window on the DB stand-in.

    analysis keeps its Supabase client and its in-flight set at module level; both start empty
    here and are put back after the test, so the stand-in never leaks into other tests.
    """
    from research_desk.features.analysis import service as analysis_service
    monkeypatch.setattr(analysis_service, '_supabase_client', None)
    monkeypatch.setattr(analysis_service, '_analyzing', set())
    return db


def summary_row(rid, version='llm-summary@1.0', **fields):
    return {'report_id': rid, 'summary_version': version, 'one_line_summary': f'saved {rid}', **fields}


def test_reuse_works_end_to_end_without_a_model_key(real_analysis, client):
    db = real_analysis
    db.tables['reports'] = [tagged(1), tagged(2)]
    saved = summary_row(1, financial_details={'metrics': []})
    db.tables['report_summaries'] = [saved, summary_row(2, financial_details={'metrics': []})]
    response = client.post('/api/reports/1/analyze')
    assert response.status_code == 200
    assert response.json() == public_report(tagged(1), saved) | {'analysis_reused': True}
    # only the requested report's row and summary were read
    assert [q.filter_values('eq', 'id') for q in db.queries('reports')] == [[1]]
    assert [q.filter_values('in', 'report_id') for q in db.queries('report_summaries')] == [[[1]]]


def test_a_non_company_report_is_422_from_analysis(real_analysis, client):
    real_analysis.tables['reports'] = [tagged(9, report_type='산업')]
    response = client.post('/api/reports/9/analyze')
    assert response.status_code == 422
    assert response.json() == {'detail': '금융 정보 분석은 단일종목 보고서를 선택해 주세요.'}


def test_without_a_model_key_analysis_is_not_ready_only_right_before_the_ai_call(real_analysis, client):
    real_analysis.tables['reports'] = [tagged(5)]   # no saved summary: this one needs the AI
    response = client.post('/api/reports/5/analyze')
    assert response.status_code == 503
    assert response.json() == {'detail': f'분석 기능을 지금 쓸 수 없습니다: {KEY_REASON}'}
    assert [q.filter_values('in', 'report_id') for q in real_analysis.queries('report_summaries')] == [[[5]]]


def test_the_list_attaches_active_summaries_read_100_ids_at_a_time(real_analysis, client, stock_csv):
    db = real_analysis
    db.tables['reports'] = [tagged(rid) for rid in range(1, 151)]
    db.tables['report_summaries'] = [summary_row(5), summary_row(150), summary_row(7, version='llm-summary@0.9')]
    body = client.get('/api/stocks/016360/reports').json()
    assert ids(body) == list(range(150, 0, -1))
    by_id = {r['id']: r['summary'] for r in body['reports']}
    assert by_id[5] == summary_row(5) and by_id[150] == summary_row(150)
    assert by_id[7] is None   # another summary version is not shown
    assert sum(summary is not None for summary in by_id.values()) == 2
    assert [len(q.filter_values('in', 'report_id')[0]) for q in db.queries('report_summaries')] == [100, 50]
