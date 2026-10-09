"""web.app: the assembled app (spec §6, §9.9, §13 items 1 and 3).

Ported from langgraph_tagger/workspace/tests/test_workspace.py:
- test_cross_origin_writes_are_rejected: through the assembled app, on the real companies service
  with a temp stock list holding 016360 and a temp favorites file: a write from another origin is
  403 with the old body, the app's own page gets the favorites back
- test_routes_and_explicit_analysis, its common part: /api/health, and the assembled app reaches
  every feature (the feature-specific checks were ported with each feature)
Ported from langgraph_tagger/workspace/tests/test_migration.py:
- test_new_routes_validate_before_mutating, the rest at app level: /api/market's bad unit and
  days are 422, the review action without a reason and an unknown action are 422 (all before
  anything is read), then verify and its undo go through, on the old MemoryDB

New: the route table is exactly spec §6's (with /assets, / and /openapi.json) and in the feature
order; the screen files and their default folder; /openapi.json on, the docs pages off; the host
and origin checks and their nesting; the 503 answers (NotReady: the feature's text and one
warning line; anything else: the fixed sentence and its traceback in the log); making the app
reads no setting and prepares no feature.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.staticfiles import StaticFiles

import research_desk
from research_desk.core import db as core_db
from research_desk.core import settings
from research_desk.core.settings import NotReady
from research_desk.features import companies, compare, coverage, reports, review
from research_desk.features.companies import service as companies_service
from research_desk.features.coverage import service as coverage_service
from research_desk.features.reports import service as reports_service
from research_desk.features.review import service as review_service
from research_desk.features.review.tests.fakes import KEY, URL, MemoryDB
from research_desk.web import app as app_module
from research_desk.web.app import FEATURES, create_app

from .fakes import (
    APP_JS,
    BUILD_FIRST,
    DB_REASON,
    FORBIDDEN,
    INDEX_HTML,
    KEY_REASON,
    NOT_ANALYZED,
    PDF_BYTES,
    PNG,
    SPEC_ROUTES,
    STOCKS_REASON,
    UNDO_UNKNOWN,
    UNEXPECTED,
    FakeCompanies,
    FakeCoverage,
    FakeReports,
    FakeReview,
    is_mount,
    not_ready,
    route_table,
    served_routes,
)

WEB_LOGGER = 'research_desk.web.app'
DATE = re.compile(r'\d{4}-\d{2}-\d{2}')
OWN_ORIGINS = ['http://127.0.0.1:8520', 'http://localhost:8520', 'http://127.0.0.1:5173',
               'http://localhost:5173']
OTHER_ORIGINS = ['https://example.com', 'http://127.0.0.1:9999', 'http://localhost',
                 'http://192.168.0.10:8520', 'null']
# A write that needs no DB: the review undo of an unknown token is 409 before anything is read.
UNDO = '/api/review/undo/unknown'


def web_records(caplog) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == WEB_LOGGER]


# ── the route table ──────────────────────────────────────────────────────────

def test_the_route_table_is_exactly_spec_section_6(app, dist):
    table = route_table(app)
    assert set(table) == SPEC_ROUTES
    assert len(table) == len(set(table))   # nothing registered twice
    (mount,) = [route.original_route for route in served_routes(app) if is_mount(route)]
    assert isinstance(mount.app, StaticFiles)
    assert Path(mount.app.directory) == dist / 'assets'
    # the only HEAD is the one Starlette adds next to GET on /openapi.json
    assert {route.path for route in served_routes(app) if 'HEAD' in (route.methods or ())} == {'/openapi.json'}


def test_the_features_are_registered_in_the_list_order(app):
    assert FEATURES == [companies, reports, compare, coverage, review]
    assert [route.path for route in served_routes(app)] == [
        '/openapi.json',
        '/api/health',
        '/api/workspace', '/api/favorites/{code}',                                              # companies
        '/api/stocks/{code}/reports', '/api/reports/{rid}/pdf', '/api/reports/{rid}/analyze',   # reports
        '/api/compare', '/api/compare/analyze',                                                 # compare
        '/api/market', '/api/stocks/{code}/activity',                                           # coverage
        '/api/review', '/api/review/{rid}/pdf', '/api/review/{rid}/preview',                    # review
        '/api/review/{rid}/pages/{page}', '/api/review/{rid}/action', '/api/review/undo/{token}',
        '/assets',
        '/',
    ]


def test_openapi_json_stays_on_and_the_docs_pages_are_off(app):
    with TestClient(app) as client:
        response = client.get('/openapi.json')
        for page in ('/docs', '/redoc', '/docs/oauth2-redirect'):
            assert client.get(page).status_code == 404
    assert response.status_code == 200
    spec = response.json()
    ok = {'200': {'description': 'Successful Response',
                  'content': {'application/json': {'schema': {}}}}}
    assert spec['info'] == {'title': 'Research Desk', 'version': '0.1.0'}
    # the web's own entries, as in the old app (the features' entries are pinned in their tests)
    assert spec['paths']['/api/health'] == {'get': {
        'summary': 'Health', 'operationId': 'health_api_health_get', 'responses': ok}}
    assert spec['paths']['/'] == {'get': {
        'summary': 'Index', 'operationId': 'index__get', 'responses': ok}}
    described = {(method.upper(), path) for path, operations in spec['paths'].items()
                 for method in operations}
    assert described == SPEC_ROUTES - {('GET', '/assets/*'), ('GET', '/openapi.json')}
    assert set(spec['components']['schemas']) == {
        'FavoriteBody', 'ComparisonBody', 'ReviewAction', 'HTTPValidationError', 'ValidationError'}


# ── the screen files ─────────────────────────────────────────────────────────

def test_the_screen_and_its_files_are_served(app):
    with TestClient(app) as client:
        index = client.get('/')
        asset = client.get('/assets/app.js')
        missing = client.get('/assets/missing.js')
    assert index.status_code == 200
    assert index.headers['cache-control'] == 'no-cache'
    assert index.headers['content-type'].startswith('text/html')
    assert index.content == INDEX_HTML
    assert (asset.status_code, asset.content) == (200, APP_JS)
    assert missing.status_code == 404


def test_without_a_screen_folder_there_is_no_assets_mount_and_the_screen_asks_for_a_build(tmp_path):
    app = create_app(dist=tmp_path / 'no-dist')
    assert set(route_table(app)) == SPEC_ROUTES - {('GET', '/assets/*')}
    assert not [route for route in served_routes(app) if is_mount(route)]
    with TestClient(app) as client:
        response = client.get('/')
        assert (response.status_code, response.json()) == (503, BUILD_FIRST)
        assert client.get('/assets/app.js').status_code == 404
        assert client.get('/api/health').json() == {'status': 'ok'}


def test_index_html_is_looked_up_on_each_request(dist):
    (dist / 'index.html').unlink()
    with TestClient(create_app(dist=dist)) as client:
        response = client.get('/')
        assert (response.status_code, response.json()) == (503, BUILD_FIRST)
        assert client.get('/assets/app.js').content == APP_JS   # mounted: the folder exists
        (dist / 'index.html').write_bytes(INDEX_HTML)
        assert client.get('/').content == INDEX_HTML


def test_the_default_screen_folder_is_frontend_dist_in_the_repository():
    repository = Path(research_desk.__file__).resolve().parent.parent
    assert app_module.DEFAULT_DIST == repository / 'frontend' / 'dist'
    assert app_module.DEFAULT_DIST.is_absolute()


def test_without_a_folder_the_app_uses_the_default_one(monkeypatch, dist):
    monkeypatch.setattr(app_module, 'DEFAULT_DIST', dist)
    with TestClient(create_app()) as client:
        assert client.get('/').content == INDEX_HTML
        assert client.get('/assets/app.js').content == APP_JS


# ── the common devices ───────────────────────────────────────────────────────

@pytest.mark.parametrize('host', ['127.0.0.1', 'localhost', 'testserver', '127.0.0.1:8520', 'localhost:5173'])
def test_the_local_hosts_are_served(app, host):
    with TestClient(app) as client:
        response = client.get('/api/health', headers={'host': host})
    assert (response.status_code, response.json()) == (200, {'status': 'ok'})


@pytest.mark.parametrize('host', ['example.com', '192.168.0.10:8520', 'evil.localhost', 'www.localhost'])
def test_a_host_that_is_not_allowed_is_400(app, host):
    with TestClient(app) as client:
        response = client.get('/api/health', headers={'host': host})
    assert (response.status_code, response.text) == (400, 'Invalid host header')


def test_cross_origin_writes_are_rejected(app, stock_csv, companies, favorites_path):
    with TestClient(app) as client:
        response = client.put('/api/favorites/016360', json={'enabled': True}, headers={'Origin': 'https://example.com'})
        assert response.status_code == 403
        assert response.json() == FORBIDDEN
        assert not favorites_path.exists()   # refused before the feature ran
        response = client.put('/api/favorites/016360', json={'enabled': True}, headers={'Origin': 'http://127.0.0.1:8520'})
        assert response.json() == {'favorites': ['016360']}


@pytest.mark.parametrize('method', ['POST', 'PUT', 'PATCH', 'DELETE'])
@pytest.mark.parametrize('origin', OTHER_ORIGINS)
def test_any_write_from_another_origin_is_403(app, origin, method):
    with TestClient(app) as client:
        response = client.request(method, UNDO, headers={'origin': origin})
    assert (response.status_code, response.json()) == (403, FORBIDDEN)


@pytest.mark.parametrize('origin', [None, *OWN_ORIGINS])
def test_writes_without_an_origin_or_from_the_apps_own_pages_go_through(app, origin):
    headers = {'origin': origin} if origin else {}
    with TestClient(app) as client:
        response = client.post(UNDO, headers=headers)
    assert (response.status_code, response.json()) == (409, UNDO_UNKNOWN)


@pytest.mark.parametrize('method,status', [('GET', 200), ('HEAD', 405), ('OPTIONS', 405)])
def test_reads_from_another_origin_are_not_refused(app, method, status):
    with TestClient(app) as client:
        response = client.request(method, '/api/health', headers={'origin': 'https://example.com'})
    assert response.status_code == status


def test_the_origin_check_still_runs_before_the_host_check(app):
    with TestClient(app) as client:
        other = client.post(UNDO, headers={'host': 'example.com', 'origin': 'https://example.com'})
        own = client.post(UNDO, headers={'host': 'example.com', 'origin': 'http://127.0.0.1:8520'})
    assert (other.status_code, other.json()) == (403, FORBIDDEN)
    assert (own.status_code, own.text) == (400, 'Invalid host header')


# ── the error answers ────────────────────────────────────────────────────────

class NotReadyCompanies:
    def bootstrap(self):
        raise NotReady('기업 목록', STOCKS_REASON)


class NotReadyAnalysis:
    async def analyze(self, rid):
        raise NotReady('분석', KEY_REASON)


def review_not_ready():
    raise NotReady('검토', DB_REASON)


@pytest.mark.parametrize('dependency,stand_in,method,address,body', [
    (companies_service.get_service, NotReadyCompanies, 'GET', '/api/workspace',
     not_ready('기업 목록', STOCKS_REASON)),
    (reports_service.get_service, NotReadyAnalysis, 'POST', '/api/reports/1/analyze',
     not_ready('분석', KEY_REASON)),
    (review_service.get_service, review_not_ready, 'GET', '/api/review',   # the preparation itself
     not_ready('검토', DB_REASON)),
], ids=['sync-handler', 'async-handler', 'dependency'])
def test_not_ready_is_503_with_the_features_text_and_one_warning_line(app, caplog, dependency, stand_in,
                                                                      method, address, body):
    app.dependency_overrides[dependency] = stand_in
    with caplog.at_level(logging.INFO, logger=WEB_LOGGER), TestClient(app) as client:
        response = client.request(method, address)
        health = client.get('/api/health')
    assert (response.status_code, response.json()) == (503, body)
    assert (health.status_code, health.json()) == (200, {'status': 'ok'})
    (record,) = web_records(caplog)
    assert record.levelno == logging.WARNING
    assert record.exc_info is None          # no traceback
    assert body['detail'] in record.getMessage()
    assert '\n' not in record.getMessage()  # one line


SECRET = r'C:\secret\reports\2026\1.pdf key=sk-secret'


class BrokenCompanies:
    def bootstrap(self):
        raise RuntimeError(f'connection lost: {SECRET}')


class BrokenReports:
    async def analyze(self, rid):
        raise RuntimeError(f'connection lost: {SECRET}')


@pytest.mark.parametrize('dependency,stand_in,method,address', [
    (companies_service.get_service, BrokenCompanies, 'GET', '/api/workspace'),
    (reports_service.get_service, BrokenReports, 'POST', '/api/reports/1/analyze'),
], ids=['sync-handler', 'async-handler'])
def test_an_unexpected_error_is_503_with_the_fixed_sentence_and_its_traceback_in_the_log(
        app, caplog, dependency, stand_in, method, address):
    app.dependency_overrides[dependency] = stand_in
    with caplog.at_level(logging.INFO, logger=WEB_LOGGER), \
            TestClient(app, raise_server_exceptions=False) as client:
        response = client.request(method, address)
        health = client.get('/api/health')
    assert (response.status_code, response.json()) == (503, UNEXPECTED)
    assert 'secret' not in response.text
    assert (health.status_code, health.json()) == (200, {'status': 'ok'})
    (record,) = web_records(caplog)
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None and isinstance(record.exc_info[1], RuntimeError)


# ── the assembled app reaches every feature ──────────────────────────────────

def test_routes_and_explicit_analysis(app, tmp_path, monkeypatch):
    # The old stub service answered for every feature; here each feature's own service is
    # replaced, and every address must reach it with what the browser sent.
    pdf = tmp_path / 'report.pdf'
    pdf.write_bytes(PDF_BYTES)
    fake_reports = FakeReports(pdf)
    app.dependency_overrides[companies_service.get_service] = FakeCompanies
    app.dependency_overrides[reports_service.get_service] = lambda: fake_reports
    app.dependency_overrides[coverage_service.get_service] = FakeCoverage
    app.dependency_overrides[review_service.get_service] = lambda: FakeReview(pdf)
    # compare reads the reports through the reports window, which uses the process-wide service
    monkeypatch.setattr(reports_service, '_service', fake_reports)
    with TestClient(app) as client:
        assert client.get('/api/health').json() == {'status': 'ok'}
        # companies
        assert client.get('/api/workspace').json() == {'stocks': [], 'favorites': []}
        assert client.put('/api/favorites/016360', json={'enabled': True}).json() == {'favorites': ['016360']}
        # reports
        listed = client.get('/api/stocks/016360/reports').json()
        assert listed['stock'] == {'code': '016360'} and [r['id'] for r in listed['reports']] == [1]
        pdf_response = client.get('/api/reports/1/pdf')
        assert pdf_response.headers['content-type'] == 'application/pdf'
        assert pdf_response.content == PDF_BYTES
        assert client.post('/api/reports/1/analyze').json()['summary']['one_line_summary'] == '검증된 응답'
        # compare, through the reports window
        result = client.get('/api/compare?left=2&right=1').json()
        assert result['left']['id'] == 1 and result['right']['id'] == 2
        assert client.get('/api/compare?left=1&right=1').status_code == 422
        narrative = client.post('/api/compare/analyze', json={'left': 2, 'right': 1})
        assert (narrative.status_code, narrative.json()) == (422, NOT_ANALYZED)
        # coverage
        market = client.get('/api/market', params={'days': 7, 'unit': 'D', 'level': 'products',
                                                   'items': ['DRAM', 'NAND'], 'include_oos': 'true'}).json()
        assert DATE.fullmatch(market.pop('since'))
        assert market == {'level': 'products', 'items': ['DRAM', 'NAND'], 'unit': 'D', 'include_oos': True}
        activity = client.get('/api/stocks/005930/activity?days=30&unit=M').json()
        assert DATE.fullmatch(activity.pop('since'))
        assert activity == {'code': '005930', 'unit': 'M'}
        # review
        assert client.get('/api/review?skipped=3&skipped=4').json() == {'remaining': 0, 'report': None,
                                                                      'skipped': [3, 4]}
        assert client.get('/api/review/1/pdf').content == PDF_BYTES
        assert client.get('/api/review/1/preview').json() == {'page_count': 1, 'preview_pages': 1}
        page = client.get('/api/review/1/pages/1')
        assert page.content == PNG and page.headers['cache-control'] == 'private, max-age=300'
        assert client.post('/api/review/7/action', json={'action': 'oos', 'reason': 'fund'}).json() == {
            'undo_token': 'oos-fund', 'report_id': 7}
        assert client.post('/api/review/undo/oos-fund').json() == {'report_id': 1, 'token': 'oos-fund'}


def test_new_routes_validate_before_mutating(app, monkeypatch):
    memory = MemoryDB()
    made = []

    def supabase_client(url, key):
        made.append((url, key))
        return memory

    monkeypatch.setenv('SUPABASE_URL', URL)
    monkeypatch.setenv('SUPABASE_SERVICE_KEY', KEY)
    monkeypatch.setattr(core_db, 'supabase_client', supabase_client)
    with TestClient(app) as client:
        assert client.get('/api/market?unit=bad').status_code == 422
        assert client.get('/api/market?days=-1').status_code == 422
        assert client.post('/api/review/1/action', json={'action': 'oos'}).status_code == 422
        assert client.post('/api/review/1/action', json={'action': 'destroy'}).status_code == 422
        assert made == []   # nothing was read or written yet
        saved = client.post('/api/review/1/action', json={'action': 'verify'}).json()
        assert client.post('/api/review/undo/'+saved['undo_token']).json() == {'report_id': 1}
    assert made == [(URL, KEY)]


# ── making the app ───────────────────────────────────────────────────────────

def test_making_the_app_reads_no_setting_and_prepares_no_feature(dist, monkeypatch):
    def no_reading(*args, **kwargs):
        raise AssertionError('making the app must not read .env')

    monkeypatch.setattr(settings, 'load_env', no_reading)
    first, second = create_app(dist=dist), create_app(dist=dist)
    assert isinstance(first, FastAPI) and first is not second
    for module in (companies_service, reports_service, coverage_service, review_service):
        assert module._service is None, module.__name__
    # and the module makes no app when it is imported (the web command makes one)
    assert not [value for value in vars(app_module).values() if isinstance(value, FastAPI)]
