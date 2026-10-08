"""Compare's two web addresses, with the reports and analysis windows replaced where compare looks
them up (``analysis.ai_slot`` stays real).

Ported from langgraph_tagger/workspace/tests/test_workspace.py::test_routes_and_explicit_analysis,
the /api/compare part (the earlier report comes back on the left; the same report twice is 422).

New (spec §6, §9.6, §9.9):
- the router serves exactly GET /api/compare and POST /api/compare/analyze with the old handler
  names, parameters and body model, so their /openapi.json entries stay the same;
- the answer is the comparison shape, and GET never calls the AI or saves anything;
- 404 for a report that is not in scope, and the three 422 texts, on both addresses;
- FastAPI's own 422 for a missing or non-numeric id, before any read;
- POST needs both analyses (422), gives back a narrative saved for exactly this pair without the
  AI or a key, and otherwise writes, saves and returns a new one;
- NotReady from the reports window or from analysis.phase2_llm → 503 with that feature's name.
"""
from __future__ import annotations

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from research_desk.core.settings import NotReady
from research_desk.features import compare, reports
from research_desk.features.compare import router

from .fakes import REPORT_NOT_FOUND, analysed, build_app, metric, tagged

NOT_SINGLE = '금융 비교는 단일종목 보고서 두 개를 선택해 주세요.'
SAME_REPORT = '서로 다른 보고서 두 개를 선택해 주세요.'
OTHER_COMPANY = '같은 기업의 보고서를 선택해 주세요.'
NOT_ANALYZED = '선택한 두 보고서를 먼저 분석해 주세요.'
KEY_REASON = 'OPENAI_API_KEY가 설정되지 않았습니다. .env에 추가 후 분석 다시 시도하세요.'
DB_REASON = 'DB 접속 설정(SUPABASE_URL, SUPABASE_SERVICE_KEY)이 없습니다'
COMPARISON_KEYS = ['left', 'right', 'same_publisher', 'metrics', 'narrative', 'target_price_change']

# The old app's /openapi.json entries for these addresses (langgraph_tagger/workspace/api.py).
_OK = {'200': {'description': 'Successful Response',
               'content': {'application/json': {'schema': {}}}}}
_INVALID = {'422': {'description': 'Validation Error', 'content': {'application/json': {
    'schema': {'$ref': '#/components/schemas/HTTPValidationError'}}}}}
OLD_GET = {'get': {
    'summary': 'Compare', 'operationId': 'compare_api_compare_get',
    'parameters': [
        {'name': 'left', 'in': 'query', 'required': True, 'schema': {'type': 'integer', 'title': 'Left'}},
        {'name': 'right', 'in': 'query', 'required': True, 'schema': {'type': 'integer', 'title': 'Right'}},
    ],
    'responses': _OK | _INVALID,
}}
OLD_POST = {'post': {
    'summary': 'Comparison Analysis', 'operationId': 'comparison_analysis_api_compare_analyze_post',
    'requestBody': {'required': True, 'content': {'application/json': {
        'schema': {'$ref': '#/components/schemas/ComparisonBody'}}}},
    'responses': _OK | _INVALID,
}}
OLD_BODY = {'title': 'ComparisonBody', 'type': 'object', 'required': ['left', 'right'],
            'properties': {'left': {'type': 'integer', 'title': 'Left'},
                           'right': {'type': 'integer', 'title': 'Right'}}}


@pytest.fixture
def client():
    with TestClient(build_app()) as test_client:
        yield test_client


def pair(world, narrative=None):
    """Two analysed KB reports on 삼성증권: 1 (February) and 3 (May, maybe with a narrative for 1)."""
    world.add(tagged(1, '2026-02-09'), analysed(1, 70000, metric(100)))
    saved = ({'prev_report_id': 1, 'prev_match_type': 'same_publisher', 'diff_narrative': narrative}
             if narrative else {})
    world.add(tagged(3, '2026-05-11'), analysed(3, 85000, metric(120), **saved))


def call(client, method, left, right):
    if method == 'GET':
        return client.get('/api/compare', params={'left': left, 'right': right})
    return client.post('/api/compare/analyze', json={'left': left, 'right': right})


# ── the window and the addresses ─────────────────────────────────────────────

def test_the_window_is_the_router():
    assert isinstance(compare.router, APIRouter)
    assert compare.router is router


def test_the_router_serves_exactly_the_two_addresses():
    assert sorted((sorted(r.methods), r.path) for r in router.routes) == [
        (['GET'], '/api/compare'),
        (['POST'], '/api/compare/analyze'),
    ]
    # the old handler names, so these /openapi.json entries stay the same
    assert sorted(r.name for r in router.routes) == ['compare', 'comparison_analysis']


def test_the_openapi_entries_are_the_old_apps():
    spec = build_app().openapi()
    assert spec['paths']['/api/compare'] == OLD_GET
    assert spec['paths']['/api/compare/analyze'] == OLD_POST
    assert spec['components']['schemas']['ComparisonBody'] == OLD_BODY


# ── ported ───────────────────────────────────────────────────────────────────

def test_routes_and_explicit_analysis(client, world):
    # the /api/compare part; the old fake service gave report(rid, f'2026-0{rid}-01')
    for rid in (1, 2):
        world.add(tagged(rid, f'2026-0{rid}-01'))
    result = client.get('/api/compare?left=2&right=1').json()
    assert result['left']['id'] == 1 and result['right']['id'] == 2
    assert client.get('/api/compare?left=1&right=1').status_code == 422


# ── GET /api/compare ─────────────────────────────────────────────────────────

def test_get_answers_the_comparison(client, world, ai):
    pair(world)
    response = client.get('/api/compare', params={'left': 3, 'right': 1})
    assert response.status_code == 200
    body = response.json()
    assert list(body) == COMPARISON_KEYS
    assert body['left'] == world.public(1) and body['right'] == world.public(3)
    assert body['same_publisher'] is True
    assert [(m['previous'], m['current'], m['change_label']) for m in body['metrics']] == [
        (100, 120, '+20.00%')]
    assert body['narrative'] is None
    assert body['target_price_change'] == {'delta': 15000, 'change_pct': pytest.approx(21.4285714),
                                           'change_label': '+21.43%'}
    assert world.reads == [3, 1]                     # read as asked: left first
    assert ai.prepared == 0 and world.saved == []    # GET never calls the AI or saves


def test_get_shows_only_the_narrative_saved_for_that_pair(client, world, ai):
    pair(world, narrative='KB는 목표주가를 높였다.')
    world.add(tagged(2, '2026-03-02', publisher='NH'), analysed(2, 80000))
    assert client.get('/api/compare?left=1&right=3').json()['narrative'] == 'KB는 목표주가를 높였다.'
    assert client.get('/api/compare?left=2&right=3').json()['narrative'] is None
    assert ai.prepared == 0


# ── 404 and 422 on both addresses ────────────────────────────────────────────

NOT_IN_SCOPE = {
    'no row': None,
    'review_needed': {'status': 'review_needed'},
    'pending': {'status': 'pending'},
    'out of scope': {'status': 'verified', 'reason': 'foreign'},
}


@pytest.mark.parametrize('method', ['GET', 'POST'])
@pytest.mark.parametrize('state', list(NOT_IN_SCOPE.values()), ids=list(NOT_IN_SCOPE))
@pytest.mark.parametrize('side', ['left', 'right'])
def test_a_report_that_is_not_in_scope_is_404(client, world, ai, method, state, side):
    pair(world)
    if state is None:
        del world.rows[3]
    else:
        world.rows[3] = tagged(3, '2026-05-11', **state)
    left, right = (3, 1) if side == 'left' else (1, 3)
    response = call(client, method, left, right)
    assert response.status_code == 404
    assert response.json() == {'detail': REPORT_NOT_FOUND}
    assert world.reads == ([3] if side == 'left' else [1, 3])   # no read after a missing left one
    assert ai.prepared == 0 and world.saved == []


@pytest.mark.parametrize('method', ['GET', 'POST'])
@pytest.mark.parametrize('left, right, detail', [
    (1, 1, SAME_REPORT), (1, 5, OTHER_COMPANY), (6, 3, NOT_SINGLE), (3, 6, NOT_SINGLE),
], ids=['same report', 'other company', 'industry left', 'industry right'])
def test_the_pair_rules_are_422(client, world, ai, method, left, right, detail):
    pair(world)
    world.add(tagged(5, '2026-04-01', codes=('005930',)), analysed(5, 1000))
    world.add(tagged(6, '2026-04-02', report_type='산업'), analysed(6))
    response = call(client, method, left, right)
    assert response.status_code == 422
    assert response.json() == {'detail': detail}
    assert ai.prepared == 0 and world.saved == []


@pytest.mark.parametrize('path', [
    '/api/compare', '/api/compare?left=1', '/api/compare?right=1',
    '/api/compare?left=a&right=1', '/api/compare?left=1&right=1.5',
])
def test_bad_query_parameters_are_422_before_any_read(client, world, path):
    assert client.get(path).status_code == 422
    assert world.reads == []


@pytest.mark.parametrize('body', [
    {}, {'left': 1}, {'right': 3}, {'left': 'a', 'right': 3}, {'left': None, 'right': 3}, [1, 3],
])
def test_a_bad_body_is_422_before_any_read(client, world, ai, body):
    assert client.post('/api/compare/analyze', json=body).status_code == 422
    assert world.reads == [] and ai.prepared == 0


def test_no_body_is_422_before_any_read(client, world, ai):
    assert client.post('/api/compare/analyze').status_code == 422
    assert world.reads == [] and ai.prepared == 0


# ── POST /api/compare/analyze ────────────────────────────────────────────────

@pytest.mark.parametrize('missing', [(1,), (3,), (1, 3)], ids=['earlier', 'later', 'both'])
def test_the_narrative_needs_both_analyses(client, world, missing):
    # no key either: the 422 comes first, the key is checked only right before the AI call
    pair(world)
    for rid in missing:
        del world.summaries[rid]
    response = client.post('/api/compare/analyze', json={'left': 1, 'right': 3})
    assert response.status_code == 422
    assert response.json() == {'detail': NOT_ANALYZED}
    assert world.saved == []


def test_a_saved_narrative_for_the_pair_comes_back_without_the_ai_or_a_key(client, world):
    # no `ai` stand-in and no key: a call to analysis.phase2_llm would answer 503
    pair(world, narrative='KB는 목표주가를 높였다.')
    response = client.post('/api/compare/analyze', json={'left': 3, 'right': 1})
    assert response.status_code == 200
    body = response.json()
    assert list(body) == COMPARISON_KEYS
    assert body['narrative'] == 'KB는 목표주가를 높였다.'
    assert body == client.get('/api/compare?left=3&right=1').json()
    assert world.saved == []


def test_a_new_narrative_is_written_saved_and_then_reused(client, world, ai):
    pair(world)
    before = client.get('/api/compare?left=1&right=3').json()
    response = client.post('/api/compare/analyze', json={'left': 1, 'right': 3})
    assert response.status_code == 200
    body = response.json()
    assert body == before | {'narrative': ai.narrative}
    assert list(body) == COMPARISON_KEYS
    assert ai.prepared == 1 and len(ai.calls) == 1
    assert world.saved == [(3, 1, 'same_publisher', ai.narrative,
                            {'metrics': before['metrics'], 'previous_publisher': 'KB',
                             'previous_published_at': '2026-02-09'})]
    # now saved for this pair: shown by GET, and the next POST does not call the AI
    assert client.get('/api/compare?left=3&right=1').json()['narrative'] == ai.narrative
    assert client.post('/api/compare/analyze', json={'left': 3, 'right': 1}).json()[
        'narrative'] == ai.narrative
    assert ai.prepared == 1 and len(ai.calls) == 1 and len(world.saved) == 1


# ── features that are not ready (spec §9.9) ──────────────────────────────────

@pytest.mark.parametrize('method', ['GET', 'POST'])
def test_not_ready_reports_shows_the_reports_feature(client, monkeypatch, ai, method):
    def get_report(rid):
        raise NotReady('리포트', DB_REASON)

    monkeypatch.setattr(reports, 'get_report', get_report)
    response = call(client, method, 1, 3)
    assert response.status_code == 503
    assert response.json() == {'detail': f'리포트 기능을 지금 쓸 수 없습니다: {DB_REASON}'}
    assert ai.prepared == 0


def test_without_a_model_key_a_new_narrative_is_analysis_not_ready(client, world):
    pair(world)
    response = client.post('/api/compare/analyze', json={'left': 1, 'right': 3})
    assert response.status_code == 503
    assert response.json() == {'detail': f'분석 기능을 지금 쓸 수 없습니다: {KEY_REASON}'}
    assert world.saved == []
    assert client.get('/api/compare?left=1&right=3').status_code == 200   # the comparison still works
