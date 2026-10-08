"""features.review: the six review addresses (spec §6), through a small FastAPI app.

Ported from langgraph_tagger/workspace/tests/test_migration.py, unchanged in meaning:
- test_new_routes_validate_before_mutating, the review parts: an out-of-scope action without a
  reason and an unknown action are 422, then verify and its undo go through (the /api/market
  parts are in features/coverage), on the old MemoryDB
- test_review_preview_caps_pages_and_serves_png: the preview counts the pages, page 1 is a PNG,
  page 2 of a 1-page PDF and page 4 are 404

New (spec §6, §9.7, §9.8, §9.9):
- the router serves exactly the six addresses with the old handler names, parameters and body
  model, so their /openapi.json entries stay the same; the reasons are the shared OOS reasons;
- the queue answer (every column but file_path, pdf_url), skipped ids as repeated query values;
- the PDF, preview and page addresses: core.pdf confinement and its 404 texts, at most 3 preview
  pages, PNGs of pages 1–3 at 120 dpi with ``Cache-Control: private, max-age=300``, the page 404
  texts, and no DB read for a page outside 1–3;
- the action and undo answers, their 404 / 409 / 422 texts, FastAPI's 422 before any read;
- the coverage cache is cleared after each successful action and undo, never otherwise;
- without DB settings the addresses that need the DB are 503 ``검토 기능을 지금 쓸 수 없습니다: …``;
  settings added to .env are used on the next request; the process-wide service keeps the undo
  records across requests; answers never carry local paths or keys.
"""
from __future__ import annotations

import json
from typing import get_args

import pymupdf
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from research_desk.core.settings import NotReady
from research_desk.domain.reports import OOS_REASONS
from research_desk.features import review
from research_desk.features.review import router
from research_desk.features.review import service as service_module
from research_desk.features.review.router import ReviewAction
from research_desk.features.review.service import ReviewService, get_service

from .fakes import KEY, URL, waiting

ROW_NOT_FOUND = '검토할 보고서가 없습니다.'
NOT_IN_QUEUE = '다른 작업에서 처리한 보고서입니다. 목록을 새로고침해 주세요.'
CHANGED = '보고서 상태가 바뀌었습니다. 새로고침해 주세요.'
OOS_REASON_REQUIRED = '분석 대상 제외 사유를 선택해 주세요.'
UNDO_UNKNOWN = '되돌릴 작업이 없거나 서버가 재시작되었습니다.'
UNDO_LATER_WORK = '후속 작업이 처리한 보고서라 되돌릴 수 없습니다.'
UNDO_STARTED = '후속 작업이 시작되어 되돌릴 수 없습니다.'
PAGE_LIMIT = '미리보기는 첫 3페이지까지 제공됩니다.'
PAGE_MISSING = '페이지가 없습니다.'
NOT_IN_STORAGE = 'PDF를 찾을 수 없습니다.'
FILE_MISSING = '로컬 PDF 파일이 없습니다. 수집 상태를 확인해 주세요.'
DB_REASON = 'DB 접속 설정(SUPABASE_URL, SUPABASE_SERVICE_KEY)이 없습니다'
NOT_READY = {'detail': f'검토 기능을 지금 쓸 수 없습니다: {DB_REASON}'}

# The old app's /openapi.json entries for these addresses (langgraph_tagger/workspace/api.py).
_OK = {'200': {'description': 'Successful Response',
               'content': {'application/json': {'schema': {}}}}}
_INVALID = {'422': {'description': 'Validation Error', 'content': {'application/json': {
    'schema': {'$ref': '#/components/schemas/HTTPValidationError'}}}}}
_RID = {'name': 'rid', 'in': 'path', 'required': True, 'schema': {'type': 'integer', 'title': 'Rid'}}
OLD_PATHS = {
    '/api/review': {'get': {
        'summary': 'Review', 'operationId': 'review_api_review_get',
        'parameters': [{'name': 'skipped', 'in': 'query', 'required': False, 'schema': {
            'type': 'array', 'items': {'type': 'integer'}, 'default': [], 'title': 'Skipped'}}],
        'responses': _OK | _INVALID}},
    '/api/review/{rid}/pdf': {'get': {
        'summary': 'Review Pdf', 'operationId': 'review_pdf_api_review__rid__pdf_get',
        'parameters': [_RID], 'responses': _OK | _INVALID}},
    '/api/review/{rid}/preview': {'get': {
        'summary': 'Review Preview', 'operationId': 'review_preview_api_review__rid__preview_get',
        'parameters': [_RID], 'responses': _OK | _INVALID}},
    '/api/review/{rid}/pages/{page}': {'get': {
        'summary': 'Review Page', 'operationId': 'review_page_api_review__rid__pages__page__get',
        'parameters': [_RID, {'name': 'page', 'in': 'path', 'required': True,
                              'schema': {'type': 'integer', 'title': 'Page'}}],
        'responses': _OK | _INVALID}},
    '/api/review/{rid}/action': {'post': {
        'summary': 'Review Action', 'operationId': 'review_action_api_review__rid__action_post',
        'parameters': [_RID],
        'requestBody': {'required': True, 'content': {'application/json': {
            'schema': {'$ref': '#/components/schemas/ReviewAction'}}}},
        'responses': _OK | _INVALID}},
    '/api/review/undo/{token}': {'post': {
        'summary': 'Review Undo', 'operationId': 'review_undo_api_review_undo__token__post',
        'parameters': [{'name': 'token', 'in': 'path', 'required': True,
                        'schema': {'type': 'string', 'title': 'Token'}}],
        'responses': _OK | _INVALID}},
}
OLD_REVIEW_ACTION = {
    'properties': {
        'action': {'type': 'string', 'enum': ['verify', 'oos', 'retag'], 'title': 'Action'},
        'reason': {'anyOf': [{'type': 'string',
                              'enum': ['foreign', 'fund', 'digital', 'private', 'ir_self']},
                             {'type': 'null'}],
                   'title': 'Reason'},
    },
    'type': 'object', 'required': ['action'], 'title': 'ReviewAction',
}


def build_app(service: ReviewService | None = None) -> FastAPI:
    """This feature's router plus the NotReady → 503 answer the web app adds (spec §6). Without
    ``service`` the router's own process-wide service is used."""
    app = FastAPI()
    app.include_router(router)
    if service is not None:
        app.dependency_overrides[get_service] = lambda: service

    @app.exception_handler(NotReady)
    async def not_ready(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=503)

    return app


def make_pdf(path, pages: int = 1):
    path.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as doc:
        for number in range(1, pages + 1):
            doc.new_page().insert_text((50, 50), f'Review preview page {number}')
        doc.save(path)
    return path


def write_file(path, content: bytes = b'%PDF-1.4 review') -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return content


def old_png(path, page: int) -> bytes:
    """The page image as the old app made it."""
    with pymupdf.open(path) as doc:
        return doc[page - 1].get_pixmap(matrix=pymupdf.Matrix(120 / 72, 120 / 72), alpha=False).tobytes('png')


def answer(response) -> tuple[int, str]:
    return response.status_code, response.json()['detail']


def act(client, rid, action='verify', reason=None):
    return client.post(f'/api/review/{rid}/action', json={'action': action, 'reason': reason})


@pytest.fixture
def service() -> ReviewService:
    return ReviewService()


@pytest.fixture
def client(service):
    with TestClient(build_app(service)) as test_client:
        yield test_client


@pytest.fixture
def window(monkeypatch):
    """The process-wide service starts fresh here and is put back after the test."""
    monkeypatch.setattr(service_module, '_service', None)


# ── the window and the addresses ─────────────────────────────────────────────

def test_the_window_is_the_router():
    assert isinstance(review.router, APIRouter)
    assert review.router is router


def test_the_router_serves_exactly_the_six_addresses_in_the_old_order():
    assert [(sorted(r.methods), r.path, r.name) for r in router.routes] == [
        (['GET'], '/api/review', 'review'),
        (['GET'], '/api/review/{rid}/pdf', 'review_pdf'),
        (['GET'], '/api/review/{rid}/preview', 'review_preview'),
        (['GET'], '/api/review/{rid}/pages/{page}', 'review_page'),
        (['POST'], '/api/review/{rid}/action', 'review_action'),
        (['POST'], '/api/review/undo/{token}', 'review_undo'),
    ]


def test_the_openapi_entries_are_the_old_apps():
    spec = build_app().openapi()
    assert spec['paths'] == OLD_PATHS
    assert spec['components']['schemas']['ReviewAction'] == OLD_REVIEW_ACTION


def test_the_actions_and_reasons_are_the_old_values_and_the_shared_reasons():
    assert get_args(ReviewAction.model_fields['action'].annotation) == ('verify', 'oos', 'retag')
    reason, none = get_args(ReviewAction.model_fields['reason'].annotation)
    assert get_args(reason) == OOS_REASONS and none is type(None)


# ── ported ───────────────────────────────────────────────────────────────────

def test_new_routes_validate_before_mutating(memory):
    # the review parts; the old app also got the coverage cache here (now coverage.invalidate)
    with TestClient(build_app(ReviewService())) as client:
        assert client.post('/api/review/1/action', json={'action': 'oos'}).status_code == 422
        assert client.post('/api/review/1/action', json={'action': 'destroy'}).status_code == 422
        saved = client.post('/api/review/1/action', json={'action': 'verify'}).json()
        assert client.post('/api/review/undo/'+saved['undo_token']).json() == {'report_id': 1}


def test_review_preview_caps_pages_and_serves_png(tmp_path, monkeypatch, memory):
    # the old stand-in service gave {'file_path': 'review.pdf'} for any id, with tmp_path as the
    # storage folder; here the row comes from the old MemoryDB and the folder from STORAGE_BASE_DIR
    path = tmp_path / 'review.pdf'
    with pymupdf.open() as doc:
        doc.new_page().insert_text((50, 50), 'Review preview')
        doc.save(path)
    memory.rows[0]['file_path'] = 'review.pdf'
    monkeypatch.setenv('STORAGE_BASE_DIR', str(tmp_path))
    with TestClient(build_app(ReviewService())) as client:
        assert client.get('/api/review/1/preview').json() == {'page_count': 1, 'preview_pages': 1}
        image = client.get('/api/review/1/pages/1')
        assert image.status_code == 200 and image.content.startswith(b'\x89PNG')
        assert client.get('/api/review/1/pages/2').status_code == 404
        assert client.get('/api/review/1/pages/4').status_code == 404


# ── GET /api/review ──────────────────────────────────────────────────────────

def test_the_queue_answers_the_next_row_without_its_local_path(client, db):
    oldest = waiting(2, '2026-05-01T00:00:00+00:00')
    db.rows = [waiting(1, '2026-05-02T00:00:00+00:00'), oldest]
    response = client.get('/api/review')
    assert response.status_code == 200
    body = response.json()
    assert list(body) == ['remaining', 'report']
    assert body['remaining'] == 2
    expected = {k: v for k, v in oldest.items() if k != 'file_path'} | {'pdf_url': '/api/review/2/pdf'}
    assert body['report'] == expected and list(body['report']) == list(expected)


def test_skipped_ids_come_as_repeated_query_values(client, db):
    db.rows = [waiting(rid, f'2026-05-0{rid}T00:00:00+00:00') for rid in (1, 2, 3)]
    assert client.get('/api/review?skipped=1&skipped=2').json()['report']['id'] == 3
    assert client.get('/api/review', params={'skipped': [1, 2, 3]}).json() == {'remaining': 3, 'report': None}


def test_an_empty_queue_answers_null(client, db):
    assert client.get('/api/review').json() == {'remaining': 0, 'report': None}


@pytest.mark.parametrize('query', ['skipped=abc', 'skipped=1&skipped=x', 'skipped='])
def test_a_skipped_id_must_be_a_number(client, db, query):
    assert client.get(f'/api/review?{query}').status_code == 422
    assert db.executed == []


# ── GET /api/review/{rid}/pdf · preview · pages ──────────────────────────────

def test_the_pdf_of_a_review_row_is_served(client, db, storage):
    db.rows = [waiting(1)]
    content = write_file(storage / '2026' / '1.pdf')
    response = client.get('/api/review/1/pdf')
    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/pdf'
    assert response.content == content


@pytest.mark.parametrize('status', ['review_needed', 'verified', 'pending', 'auto'])
def test_the_pdf_is_found_by_id_whatever_the_rows_status(client, db, storage, status):
    db.rows = [waiting(1, tagging_status=status)]
    content = write_file(storage / '2026' / '1.pdf')
    assert client.get('/api/review/1/pdf').content == content


PREVIEW_ADDRESSES = ('/api/review/1/pdf', '/api/review/1/preview', '/api/review/1/pages/1')


def test_a_missing_row_has_no_pdf_preview_or_page(client, db, storage):
    make_pdf(storage / '2026' / '1.pdf')
    for address in PREVIEW_ADDRESSES:
        assert answer(client.get(address)) == (404, ROW_NOT_FOUND)


@pytest.mark.parametrize('file_path,detail', [
    ('../outside.pdf', NOT_IN_STORAGE),
    ('2026/1.txt', NOT_IN_STORAGE),
    ('2026/missing.pdf', FILE_MISSING),
])
def test_pdf_paths_stay_inside_the_storage_folder(client, db, storage, file_path, detail):
    make_pdf(storage.parent / 'outside.pdf')     # exists, but outside the storage folder
    write_file(storage / '2026' / '1.txt')       # exists, but not a PDF
    db.rows = [waiting(1, file_path=file_path)]
    for address in PREVIEW_ADDRESSES:
        assert answer(client.get(address)) == (404, detail)


def test_an_absolute_path_elsewhere_is_refused(client, db, storage, tmp_path):
    elsewhere = make_pdf(tmp_path / 'elsewhere' / 'x.pdf')
    db.rows = [waiting(1, file_path=str(elsewhere))]
    for address in PREVIEW_ADDRESSES:
        assert answer(client.get(address)) == (404, NOT_IN_STORAGE)


def test_the_storage_folder_defaults_to_reports_in_the_current_folder(client, db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db.rows = [waiting(1)]
    content = write_file(tmp_path / 'reports' / '2026' / '1.pdf', b'%PDF default folder')
    assert client.get('/api/review/1/pdf').content == content


@pytest.mark.parametrize('pages,preview', [(1, 1), (2, 2), (3, 3), (5, 3)])
def test_the_preview_counts_the_pages_and_offers_at_most_three(client, db, storage, pages, preview):
    db.rows = [waiting(1)]
    make_pdf(storage / '2026' / '1.pdf', pages)
    response = client.get('/api/review/1/preview')
    assert response.status_code == 200
    assert response.json() == {'page_count': pages, 'preview_pages': preview}


def test_the_first_three_pages_are_pngs_at_120_dpi(client, db, storage):
    db.rows = [waiting(1)]
    path = make_pdf(storage / '2026' / '1.pdf', 5)
    for page in (1, 2, 3):
        response = client.get(f'/api/review/1/pages/{page}')
        assert response.status_code == 200
        assert response.headers['content-type'] == 'image/png'
        assert response.headers['cache-control'] == 'private, max-age=300'
        assert response.content == old_png(path, page)
    # an A4 page (595 x 842 pt) at 120 dpi
    png = client.get('/api/review/1/pages/1').content
    assert (int.from_bytes(png[16:20], 'big'), int.from_bytes(png[20:24], 'big')) == (992, 1404)


@pytest.mark.parametrize('page', [0, 4, 5, -1, 100])
def test_pages_outside_one_to_three_are_404_before_any_read(client, db, page):
    assert answer(client.get(f'/api/review/1/pages/{page}')) == (404, PAGE_LIMIT)
    assert db.executed == []


def test_a_page_the_pdf_does_not_have_is_404(client, db, storage):
    db.rows = [waiting(1)]
    make_pdf(storage / '2026' / '1.pdf', 2)
    assert answer(client.get('/api/review/1/pages/3')) == (404, PAGE_MISSING)
    assert client.get('/api/review/1/pages/2').status_code == 200


@pytest.mark.parametrize('address', ['/api/review/abc/pdf', '/api/review/abc/preview',
                                     '/api/review/abc/pages/1', '/api/review/1/pages/x'])
def test_ids_and_pages_must_be_numbers(client, db, address):
    assert client.get(address).status_code == 422
    assert db.executed == []


# ── POST /api/review/{rid}/action ────────────────────────────────────────────

@pytest.mark.parametrize('body,status', [
    ({'action': 'verify'}, 'verified'),
    ({'action': 'oos', 'reason': 'ir_self'}, 'verified'),
    ({'action': 'retag'}, 'pending'),
])
def test_an_action_answers_its_undo_token_and_the_report_id(client, db, body, status):
    db.rows = [waiting(1)]
    response = client.post('/api/review/1/action', json=body)
    assert response.status_code == 200
    body = response.json()
    assert list(body) == ['undo_token', 'report_id'] and body['report_id'] == 1
    assert len(body['undo_token']) == 32
    assert db.row(1)['tagging_status'] == status


def test_a_reason_given_with_verify_or_retag_is_ignored(client, db):
    db.rows = [waiting(1), waiting(2)]
    act(client, 1, 'verify', 'fund')
    assert db.row(1) == waiting(1) | {'tagging_status': 'verified'}
    act(client, 2, 'retag', 'fund')
    assert db.row(2)['tagging_status'] == 'pending' and db.row(2)['out_of_scope_reason'] is None


@pytest.mark.parametrize('body', [{'action': 'oos'}, {'action': 'oos', 'reason': None}])
def test_out_of_scope_without_a_reason_is_422_with_the_text_before_any_read(client, db, body):
    db.rows = [waiting(1)]
    assert answer(client.post('/api/review/1/action', json=body)) == (422, OOS_REASON_REQUIRED)
    assert db.executed == []


@pytest.mark.parametrize('body', [
    {'action': 'destroy'}, {'action': 'VERIFY'}, {'action': None}, {'reason': 'fund'}, {},
    {'action': 'oos', 'reason': 'unknown'}, {'action': 'oos', 'reason': ''},
    {'action': 'verify', 'reason': 'FOREIGN'},
])
def test_an_action_or_reason_outside_the_values_is_422_before_any_read(client, db, body):
    db.rows = [waiting(1)]
    response = client.post('/api/review/1/action', json=body)
    assert response.status_code == 422
    assert isinstance(response.json()['detail'], list)   # FastAPI's own validation answer
    assert db.executed == []


def test_an_action_needs_a_body_and_a_numeric_id(client, db):
    assert client.post('/api/review/1/action').status_code == 422
    assert client.post('/api/review/abc/action', json={'action': 'verify'}).status_code == 422
    assert db.executed == []


def test_action_refusals_answer_their_texts(client, db):
    db.rows = [waiting(1, tagging_status='auto'), waiting(2)]
    assert answer(act(client, 99)) == (404, ROW_NOT_FOUND)
    assert answer(act(client, 1)) == (409, NOT_IN_QUEUE)
    db.before_write.append(lambda: db.row(2).update(tagging_status='processing'))
    assert answer(act(client, 2, 'retag')) == (409, CHANGED)
    assert db.row(2)['tagging_status'] == 'processing'


# ── POST /api/review/undo/{token} ────────────────────────────────────────────

@pytest.mark.parametrize('action,reason', [('verify', None), ('oos', 'foreign'), ('retag', None)])
def test_undo_answers_the_report_id_and_puts_the_row_back(client, db, action, reason):
    db.rows = [waiting(1)]
    token = act(client, 1, action, reason).json()['undo_token']
    response = client.post(f'/api/review/undo/{token}')
    assert (response.status_code, response.json()) == (200, {'report_id': 1})
    assert db.row(1) == waiting(1)


def test_undo_refusals_answer_their_texts(client, db):
    db.rows = [waiting(1)]
    assert answer(client.post('/api/review/undo/unknown')) == (409, UNDO_UNKNOWN)
    token = act(client, 1).json()['undo_token']
    undo = f'/api/review/undo/{token}'
    db.row(1)['tagging_notes'] = 'later'
    assert answer(client.post(undo)) == (409, UNDO_LATER_WORK)
    db.row(1)['tagging_notes'] = 'krx_unmatched_in_scope'
    db.before_write.append(lambda: db.row(1).update(tagging_worker_id='run-1'))
    assert answer(client.post(undo)) == (409, UNDO_STARTED)
    db.rows.clear()
    assert answer(client.post(undo)) == (404, ROW_NOT_FOUND)


def test_a_token_undoes_once(client, db):
    db.rows = [waiting(1)]
    token = act(client, 1).json()['undo_token']
    assert client.post(f'/api/review/undo/{token}').status_code == 200
    assert answer(client.post(f'/api/review/undo/{token}')) == (409, UNDO_UNKNOWN)


# ── the coverage cache (spec §9.7) ───────────────────────────────────────────

def test_each_successful_action_and_undo_clears_the_coverage_cache(client, db, invalidations):
    db.rows = [waiting(1), waiting(2), waiting(3)]
    expected = 0
    for rid, action, reason in ((1, 'verify', None), (2, 'oos', 'fund'), (3, 'retag', None)):
        token = act(client, rid, action, reason).json()['undo_token']
        expected += 1
        assert invalidations.count == expected
        assert client.post(f'/api/review/undo/{token}').status_code == 200
        expected += 1
        assert invalidations.count == expected


def test_reads_and_refused_requests_leave_the_coverage_cache(client, db, storage, invalidations):
    db.rows = [waiting(1, tagging_status='auto'), waiting(2)]
    make_pdf(storage / '2026' / '2.pdf')
    requests = [
        lambda: client.get('/api/review'),
        lambda: client.get('/api/review/2/pdf'),
        lambda: client.get('/api/review/2/preview'),
        lambda: client.get('/api/review/2/pages/1'),
        lambda: client.post('/api/review/2/action', json={'action': 'oos'}),       # 422 text
        lambda: client.post('/api/review/2/action', json={'action': 'destroy'}),   # 422
        lambda: act(client, 99),                                                     # 404
        lambda: act(client, 1),                                                      # 409
        lambda: client.post('/api/review/undo/unknown'),                             # 409
    ]
    for request in requests:
        request()
    assert invalidations.count == 0
    token = act(client, 2).json()['undo_token']
    db.row(2)['tagging_notes'] = 'later'
    assert client.post(f'/api/review/undo/{token}').status_code == 409
    assert invalidations.count == 1   # the successful action only


# ── readiness (spec §9.9) ────────────────────────────────────────────────────

def test_without_db_settings_the_addresses_that_need_the_db_are_503_with_the_reason(client, storage,
                                                                                    invalidations):
    make_pdf(storage / '2026' / '1.pdf')
    responses = [client.get('/api/review'), client.get('/api/review/1/pdf'),
                 client.get('/api/review/1/preview'), client.get('/api/review/1/pages/1'),
                 act(client, 1), act(client, 1, 'oos', 'fund'), act(client, 1, 'retag')]
    for response in responses:
        assert response.status_code == 503
        assert response.json() == NOT_READY   # a fixed sentence: no URL, no key, no path
    assert invalidations.count == 0


def test_db_settings_fixed_later_work_without_a_restart(client, db, monkeypatch):
    db.rows = [waiting(1)]
    monkeypatch.delenv('SUPABASE_URL')
    assert client.get('/api/review').json() == NOT_READY
    assert client.get('/api/review').json() == NOT_READY
    monkeypatch.setenv('SUPABASE_URL', URL)
    assert client.get('/api/review').json()['remaining'] == 1
    assert db.made == [(URL, KEY)]


def test_db_settings_added_to_env_file_are_used_on_the_next_request(env_file, client, db, monkeypatch):
    db.rows = [waiting(1)]
    monkeypatch.delenv('SUPABASE_URL')
    monkeypatch.delenv('SUPABASE_SERVICE_KEY')
    assert client.get('/api/review').json() == NOT_READY
    env_file.write_text('SUPABASE_URL=https://from-env-file.test\nSUPABASE_SERVICE_KEY=k\n', encoding='utf-8')
    assert client.get('/api/review').json()['report']['id'] == 1
    assert db.made == [('https://from-env-file.test', 'k')]


def test_the_process_wide_service_keeps_the_undo_records_across_requests(window, db):
    db.rows = [waiting(1)]
    with TestClient(build_app()) as client:          # the router's own get_service
        token = act(client, 1).json()['undo_token']
        assert client.post(f'/api/review/undo/{token}').json() == {'report_id': 1}
    assert get_service() is get_service()
    assert db.made == [(URL, KEY)]
    assert db.row(1) == waiting(1)


def test_creating_the_process_wide_service_reads_nothing(window):
    # no settings at all, and core.db.supabase_client refuses: still no error
    assert isinstance(get_service(), ReviewService)


def test_answers_never_carry_local_paths_or_keys(client, db, storage):
    db.rows = [waiting(1)]
    make_pdf(storage / '2026' / '1.pdf', 2)
    texts = [client.get('/api/review').text, client.get('/api/review/1/preview').text]
    token_answer = act(client, 1)
    texts.append(token_answer.text)
    texts.append(client.post(f"/api/review/undo/{token_answer.json()['undo_token']}").text)
    secrets = ('file_path', '"2026/1.pdf"', KEY, URL, str(storage), storage.as_posix(),
               json.dumps(str(storage))[1:-1])
    for text in texts:
        for secret in secrets:
            assert secret not in text, secret
