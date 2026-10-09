"""``POST /api/peers/search`` (spec §12.2, §12.4) through a small app with this feature's router, over
the world of ``web_world``, and the query embedding behind it.

- the answer's keys; RRF (k = 60) of the embedding ranking, the best-segment ranking and the exact
  term matches; the limit; only listed companies with a profile in the public build;
- the query is normalized with the synonym table, embedded with the build's model at 1536
  dimensions, and the last 256 (model, query) embeddings are kept;
- the embedding runs in this feature's own two slots (never ``analysis.ai_slot()``) within 15 s;
- a missing OPENAI_API_KEY is a 503 on this address only and a key added to ``.env`` counts;
- 422 before readiness; readiness failures and other features' NotReady as on the peers address.
"""
from __future__ import annotations

import ast
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from research_desk.core import db as core_db
from research_desk.core.settings import NotReady
from research_desk.features import analysis, coverage, prices
from research_desk.features.analysis import service as analysis_service
from research_desk.features.peers import logic
from research_desk.features.peers import service as service_module
from research_desk.features.peers.service import PeersService

from .fakes import DIMS, FakeLLM, pseudo_vector
from .web_world import (
    KEY,
    MODEL,
    SEED,
    SNAPSHOTS,
    URL,
    Counts,
    Snapshots,
    build_app,
    build_row,
    counts_record,
    make_db,
    write_stock_list,
)

OPENAI_KEY = 'sk-peers-test'
SYN = logic.parse_synonyms({'DRAM': ['디램', 'D램']})
DB_REASON = 'DB 접속 설정(SUPABASE_URL, SUPABASE_SERVICE_KEY)이 없습니다'
KEY_REASON = 'OPENAI_API_KEY가 설정되지 않았습니다'
NO_BUILD_REASON = '아직 공개된 유사도 계산 결과가 없습니다(python -m research_desk peers build)'
RESULT_KEYS = {'rank', 'code', 'name', 'market', 'sector_minor', 'one_line', 'matched_terms', 'segment_match',
               'coverage', 'price'}
# RRF of (a) 080220 032580 000660 777770 888880 444440 333330 111110 999990, (b) 080220 111110 032580
# 333330 000660 and (c) 032580 080220 000660 123450 888880 999990 (see the module docstring of web_world
# for the vectors; the query vector is 0.6 on axis 0 and 0.8 on axis 1).
FUSED = ['080220', '032580', '000660', '111110', '888880', '333330', '999990', '123450', '777770', '444440']


def not_ready(reason, area='유사 기업'):
    return {'detail': f'{area} 기능을 지금 쓸 수 없습니다: {reason}'}


def query_vector() -> list[float]:
    vector = [0.0] * DIMS
    vector[0], vector[1] = 0.6, 0.8
    return vector


def embedder(text: str) -> list[float]:
    return query_vector() if text in ('Legacy DRAM', '레거시 DRAM') else pseudo_vector(text)


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    csv_path = write_stock_list(tmp_path / 'KRX_stocks_data.csv')
    db = make_db()
    monkeypatch.setenv('SUPABASE_URL', URL)
    monkeypatch.setenv('SUPABASE_SERVICE_KEY', KEY)
    monkeypatch.setenv('KRX_CSV_PATH', str(csv_path))
    monkeypatch.setenv('OPENAI_API_KEY', OPENAI_KEY)
    monkeypatch.setattr(core_db, 'supabase_client', lambda url, key: db)
    counts, snapshots = Counts(), Snapshots()
    monkeypatch.setattr(coverage, 'report_counts', counts)
    monkeypatch.setattr(prices, 'snapshots', snapshots)
    llm = FakeLLM(embed=embedder)
    loads = []

    def synonyms():
        loads.append(1)
        return SYN

    service = PeersService(llm_client=lambda: llm, synonyms=synonyms)
    return SimpleNamespace(db=db, csv=csv_path, counts=counts, snapshots=snapshots, llm=llm, loads=loads,
                           service=service, client=TestClient(build_app(service)), tmp=tmp_path)


def search(world, q='Legacy DRAM', **fields):
    return world.client.post('/api/peers/search', json={'q': q, **fields})


# ── the answer ───────────────────────────────────────────────────────────────

def test_the_answer_fuses_three_rankings_and_has_the_spec_keys(world):
    response = search(world)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {'query', 'basis', 'window', 'price_as_of', 'results'}
    assert (body['query'], body['window'], body['price_as_of']) == ('Legacy DRAM', '1m', '2026-10-08')
    assert body['basis'] == {'fiscal_year': 2025, 'build_id': 1, 'built_at': '2026-10-01T09:00:00+00:00',
                             'companies_profiled': 2350, 'companies_eligible': 2400}
    assert [r['code'] for r in body['results']] == FUSED
    assert [r['rank'] for r in body['results']] == list(range(1, 11))
    assert all(set(r) == RESULT_KEYS for r in body['results'])
    rows = {r['code']: r for r in body['results']}
    assert rows[SEED]['name'] == '제주반도체' and rows[SEED]['one_line'] == f'{SEED} 틈새 업종'
    assert rows[SEED]['matched_terms'] == ['Legacy DRAM', 'DRAM']
    assert rows[SEED]['segment_match'] == {'segment': '메모리', 'revenue_share_pct': 98.1}
    assert rows['032580']['matched_terms'] == ['Legacy DRAM', 'DRAM']
    assert rows['111110']['matched_terms'] == []
    assert rows['111110']['segment_match'] == {'segment': '메모리 모듈', 'revenue_share_pct': 70.0}
    assert rows['333330']['segment_match'] == {'segment': '기타', 'revenue_share_pct': 100.0}
    assert rows['123450']['segment_match'] is None and rows['123450']['matched_terms'] == ['DRAM']
    assert rows['000660']['market'] == 'KOSPI' and rows['111110']['sector_minor'] == ''
    assert rows['032580']['coverage'] == counts_record('none')
    assert rows['032580']['price'] == {k: v for k, v in SNAPSHOTS['032580'].items() if k != 'market'}
    assert rows['888880']['price'] is None
    assert world.counts.calls == [(FUSED, 365)] and world.snapshots.calls == [FUSED]


def test_the_fused_order_is_the_rrf_of_the_three_rankings(world):
    fused = logic.rrf([
        ['080220', '032580', '000660', '777770', '888880', '444440', '333330', '111110', '999990'],
        ['080220', '111110', '032580', '333330', '000660'],
        ['032580', '080220', '000660', '123450', '888880', '999990']])
    assert [code for code, _ in fused] == FUSED
    assert [r['code'] for r in search(world).json()['results']] == FUSED


def test_the_limit_cuts_the_fused_list(world):
    assert [r['code'] for r in search(world, limit=3).json()['results']] == FUSED[:3]
    assert len(search(world, limit=50).json()['results']) == len(FUSED)


def test_companies_not_listed_or_without_a_profile_never_come_back(world):
    codes = {r['code'] for r in search(world).json()['results']}
    # 222220 is near the query and holds 'legacydram' but is not in the stock list; 555550 has a
    # failed profile; 666660 only a 2024 one.
    assert not codes & {'222220', '555550', '666660'}


def test_terms_match_only_when_the_whole_key_is_equal(world):
    # 'legacy' is a piece of the query, but no profile holds exactly 'legacy' ('legacydram' is not it).
    body = search(world, 'Legacy').json()
    assert all(r['matched_terms'] == [] for r in body['results'])
    hits = {r['code']: r['matched_terms'] for r in search(world, '모듈, LPDDR').json()['results']}
    assert hits['111110'] == ['모듈'] and hits['032580'] == ['LPDDR']
    assert hits['000660'] == []


def test_the_window_is_echoed_and_prices_keep_every_period(world):
    body = search(world, window='3m').json()
    assert body['window'] == '3m'
    assert set(body['results'][0]['price']['excess']) == {'1w', '1m', '3m'}
    assert 'reaction' not in body['results'][0] and 'candidate' not in body['results'][0]


def test_no_result_reads_no_counts_or_prices(world):
    world.db.tables['peer_builds'] = [build_row(1, embed_model='text-embedding-3-small')]   # no vectors
    body = search(world, '없는 테마').json()
    assert body['results'] == [] and body['price_as_of'] is None
    assert world.counts.calls == [] and world.snapshots.calls == []


# ── the query embedding ──────────────────────────────────────────────────────

def test_the_query_is_normalized_and_embedded_with_the_builds_model(world):
    search(world, '레거시 디램')
    assert world.llm.embed_calls == [{'model': MODEL, 'texts': ['레거시 DRAM'], 'dimensions': 1536}]
    search(world, '  레거시   D램 ')                    # the same normalized query: from the cache
    assert len(world.llm.embed_calls) == 1
    assert world.llm.closes == 1                          # each client is closed after its call


def test_the_same_query_is_answered_from_the_cache_but_counts_and_prices_are_read_again(world):
    first, second = search(world).json(), search(world).json()
    assert first == second and len(world.llm.embed_calls) == 1
    assert len(world.counts.calls) == 2 and len(world.snapshots.calls) == 2


def test_another_build_model_is_another_cache_entry(world):
    search(world)
    world.db.table('peer_builds').insert(build_row(2, embed_model='text-embedding-3-small',
                                                  finished_at='2026-10-09T00:00:00+00:00')).execute()
    body = search(world).json()
    assert body['basis']['build_id'] == 2
    assert [call['model'] for call in world.llm.embed_calls] == [MODEL, 'text-embedding-3-small']


async def test_the_cache_keeps_the_last_256_queries(world):
    service = world.service
    assert service_module.QUERY_CACHE_SIZE == 256
    for i in range(256):
        await service.query_vector(MODEL, f'q{i}')
    assert len(world.llm.embed_calls) == 256
    await service.query_vector(MODEL, 'q0')               # a hit: q0 is now the most recent
    await service.query_vector(MODEL, 'q256')             # pushes out the least recent, q1
    assert len(world.llm.embed_calls) == 257
    await service.query_vector(MODEL, 'q0')
    assert len(world.llm.embed_calls) == 257
    await service.query_vector(MODEL, 'q1')
    assert len(world.llm.embed_calls) == 258


async def test_the_vector_is_scaled_to_length_one(world):
    vector = await world.service.query_vector(MODEL, 'anything')
    assert len(vector) == DIMS and sum(x * x for x in vector) == pytest.approx(1.0)


async def test_a_wrong_number_of_dimensions_is_an_error_and_not_kept(world, monkeypatch):
    world.llm.embedder = lambda text: [1.0] * 3
    with pytest.raises(RuntimeError):
        await world.service.query_vector(MODEL, 'short')
    world.llm.embedder = embedder
    await world.service.query_vector(MODEL, 'short')
    assert len(world.llm.embed_calls) == 2


# ── this feature's own slots, the time limit ─────────────────────────────────

async def test_at_most_two_query_embeddings_run_at_once(world):
    world.llm.delay = 0.05
    await asyncio.gather(*(world.service.query_vector(MODEL, f'parallel {i}') for i in range(6)))
    assert len(world.llm.embed_calls) == 6
    assert world.llm.embed_max_active == service_module.EMBED_CONCURRENCY == 2


async def test_analysis_slots_never_hold_up_a_query_embedding(world):
    async with analysis.ai_slot():
        async with analysis.ai_slot():                    # both analysis slots are taken
            vector = await asyncio.wait_for(world.service.query_vector(MODEL, 'not blocked'), timeout=5)
    assert len(vector) == DIMS


def test_analysis_ai_slot_is_not_used(world, monkeypatch):
    def refuse():
        raise AssertionError('theme search must not use analysis.ai_slot()')

    monkeypatch.setattr(analysis, 'ai_slot', refuse)
    monkeypatch.setattr(analysis_service, 'ai_slot', refuse)
    assert search(world).status_code == 200
    source = Path(service_module.__file__).read_text(encoding='utf-8')
    imported = {alias.name for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom)
                for alias in node.names} | {node.module for node in ast.walk(ast.parse(source))
                                            if isinstance(node, ast.ImportFrom) and node.module}
    assert 'analysis' not in imported and 'research_desk.features.analysis' not in imported


async def test_a_call_over_the_time_limit_fails_frees_its_slot_and_is_not_kept(world, monkeypatch):
    assert service_module.EMBED_TIMEOUT_S == 15
    monkeypatch.setattr(service_module, 'EMBED_TIMEOUT_S', 0.05)
    world.llm.delay = 1.0
    with pytest.raises(TimeoutError):
        await world.service.query_vector(MODEL, 'slow')
    assert world.llm.closes == 1 and world.llm.embed_active == 0
    world.llm.delay = 0
    for i in range(3):                                    # both slots are free again
        await asyncio.wait_for(world.service.query_vector(MODEL, f'after {i}'), timeout=5)
    await world.service.query_vector(MODEL, 'slow')       # not kept: asked again
    assert [call['texts'] for call in world.llm.embed_calls].count(['slow']) == 2


def test_the_default_client_has_the_key_and_the_15_second_limit(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', OPENAI_KEY)
    client = service_module.embedding_client()
    assert client.require_key(MODEL) == OPENAI_KEY
    assert client._sdk_kwargs['timeout'] == 15


# ── readiness ────────────────────────────────────────────────────────────────

def test_without_openai_api_key_only_search_is_503(world, monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY')
    response = search(world)
    assert response.status_code == 503 and response.json() == not_ready(KEY_REASON)
    assert OPENAI_KEY not in response.text
    assert world.client.get(f'/api/stocks/{SEED}/peers').status_code == 200
    assert world.client.get('/api/health').status_code == 200
    assert world.llm.embed_calls == []
    monkeypatch.setenv('OPENAI_API_KEY', OPENAI_KEY)
    assert search(world).status_code == 200


def test_an_openai_api_key_added_to_dotenv_counts_on_the_next_search(world, env_file, monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY')
    assert search(world).json() == not_ready(KEY_REASON)
    env_file.write_text(f'OPENAI_API_KEY={OPENAI_KEY}\n', encoding='utf-8')
    assert search(world).status_code == 200
    assert os.environ['OPENAI_API_KEY'] == OPENAI_KEY


@pytest.mark.parametrize('body', [
    {'q': 'a'}, {'q': '  a  '}, {'q': ''}, {'q': 'x' * 101}, {},
    {'q': 'DRAM', 'limit': 0}, {'q': 'DRAM', 'limit': 51}, {'q': 'DRAM', 'limit': 'x'},
    {'q': 'DRAM', 'limit': 1.5}, {'q': 'DRAM', 'limit': True}, {'q': 'DRAM', 'limit': '30'},
    {'q': 'DRAM', 'window': '2w'}, {'q': 12}, [],
])
def test_a_malformed_body_is_422_before_anything_is_prepared(body, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = TestClient(build_app(PeersService(llm_client=lambda: FakeLLM())))   # nothing configured
    response = client.post('/api/peers/search', json=body)
    assert response.status_code == 422
    assert isinstance(response.json()['detail'], list)


def test_a_query_of_two_to_a_hundred_characters_after_trimming_is_fine(world):
    assert search(world, '  ab  ').json()['query'] == 'ab'
    assert search(world, 'x' * 100).status_code == 200
    assert search(world, 'DRAM', limit=1, window='1w').status_code == 200


def test_search_readiness_failures_are_503_with_fixed_sentences(world, monkeypatch):
    monkeypatch.delenv('SUPABASE_URL')
    assert search(world).json() == not_ready(DB_REASON)
    monkeypatch.setenv('SUPABASE_URL', URL)
    world.db.tables['peer_builds'] = []
    assert search(world).json() == not_ready(NO_BUILD_REASON)
    world.db.table('peer_builds').insert(build_row(3)).execute()
    assert search(world).status_code == 200
    assert world.llm.embed_calls and all(call['model'] == MODEL for call in world.llm.embed_calls)


def test_another_features_not_ready_passes_through_search(world, monkeypatch):
    def refuse(*args, **kwargs):
        raise NotReady('리포트', DB_REASON)

    monkeypatch.setattr(coverage, 'report_counts', refuse)
    assert search(world).json() == not_ready(DB_REASON, '리포트')


def test_the_synonym_table_is_read_once_per_public_build(world):
    search(world)
    search(world, 'DRAM 모듈')
    assert len(world.loads) == 1
    world.db.table('peer_builds').insert(build_row(2, finished_at='2026-10-09T00:00:00+00:00')).execute()
    search(world)
    assert len(world.loads) == 2


def test_the_default_synonym_table_is_the_features_file(world, monkeypatch):
    service = PeersService(llm_client=lambda: world.llm)
    client = TestClient(build_app(service))
    assert client.post('/api/peers/search', json={'q': '디램'}).status_code == 200
    assert world.llm.embed_calls[-1]['texts'] == ['DRAM']


def test_the_answer_carries_no_key_url_or_local_path(world):
    text = search(world).text
    for secret in (KEY, URL, OPENAI_KEY, str(world.tmp), world.tmp.name):
        assert secret not in text
