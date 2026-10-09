"""``GET /api/stocks/{code}/peers`` (spec §6.2–§6.4, §7, §9, §10, §12.1, §12.4) through a small app with
this feature's router and the web app's NotReady → 503 answer, over the world of ``web_world``.

- the answer's keys and value sets; the list (merge, rank, cut, exclusions), each row's matches,
  shared terms, industry, segments, report counts, price, reaction and candidate flag;
- the seed, the basis, the window, the chosen segment (chips) and a seed without segments;
- the check order: format 422 → readiness 503 → 404 not in the stock list → 404 no profile →
  segment 422;
- the latest public build is looked up on every request (a new one counts without a restart), its
  tables are read once per build id, and "no public build" is not remembered;
- harness rules: a readiness failure is a 503 with a fixed sentence on these addresses only and
  recovers on the next request, values added to ``.env`` count, creating the service reads
  nothing, another feature's NotReady passes through, no path or key in any answer.
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from research_desk.core import db as core_db
from research_desk.core.settings import NotReady
from research_desk.features import coverage, prices
from research_desk.features.peers import logic
from research_desk.features.peers import service as service_module
from research_desk.features.peers.service import PeersService, get_service
from research_desk.features.peers.store import BUILD_TABLE_COLUMNS, PeersStore

from .web_world import (
    FY,
    KEY,
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

DB_REASON = 'DB 접속 설정(SUPABASE_URL, SUPABASE_SERVICE_KEY)이 없습니다'
STOCK_LIST_REASON = '종목표 파일을 읽을 수 없습니다'
NO_BUILD_REASON = '아직 공개된 유사도 계산 결과가 없습니다(python -m research_desk peers build)'


def not_ready(reason, area='유사 기업'):
    return {'detail': f'{area} 기능을 지금 쓸 수 없습니다: {reason}'}


STOCK_NOT_FOUND = {'detail': '종목을 찾을 수 없습니다.'}
NO_PEER_DATA = {'detail': '이 종목은 유사 기업 자료가 없습니다.'}
NO_SEGMENT = {'detail': '그 사업부문이 없습니다.'}

TOP_KEYS = {'seed', 'basis', 'window', 'segment', 'price_as_of', 'seed_excess_pct', 'judgeable', 'peers'}
SEED_KEYS = {'code', 'name', 'market', 'sector_minor', 'profile', 'is_holding', 'is_financial', 'info_quality',
             'coverage', 'price'}
PROFILE_KEYS = {'niche_industry', 'summary', 'keywords', 'segments'}
BASIS_KEYS = {'fiscal_year', 'build_id', 'built_at', 'companies_profiled', 'companies_eligible'}
PEER_KEYS = {'rank', 'code', 'name', 'market', 'sector_minor', 'one_line', 'tier', 'company_match',
             'segment_match', 'segments', 'shared_terms', 'same_industry', 'is_holding', 'is_financial',
             'info_quality', 'coverage', 'price', 'reaction', 'candidate', 'not_candidate_reasons'}
COVERAGE_KEYS = {'stock_reports', 'sector_mentions', 'other_research', 'last_stock_report_date', 'brokers', 'label'}
PRICE_KEYS = {'as_of', 'close', 'market_cap', 'avg_value_20d', 'traded', 'returns', 'excess', 'flags'}
LISTED = ['111110', '032580', '000660', '777770', '888880', '444440']


def price_of(code):
    return {k: v for k, v in SNAPSHOTS[code].items() if k != 'market'}


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Everything ready: DB settings, the stock list, the peers tables with one public build, report
    counts and price snapshots (the window functions replaced), and a client of a test app."""
    monkeypatch.chdir(tmp_path)
    csv_path = write_stock_list(tmp_path / 'KRX_stocks_data.csv')
    db = make_db()
    made = []

    def supabase_client(url, key):
        made.append((url, key))
        return db

    monkeypatch.setenv('SUPABASE_URL', URL)
    monkeypatch.setenv('SUPABASE_SERVICE_KEY', KEY)
    monkeypatch.setenv('KRX_CSV_PATH', str(csv_path))
    monkeypatch.setattr(core_db, 'supabase_client', supabase_client)
    counts, snapshots = Counts(), Snapshots()
    monkeypatch.setattr(coverage, 'report_counts', counts)
    monkeypatch.setattr(prices, 'snapshots', snapshots)
    service = PeersService()
    return SimpleNamespace(db=db, made=made, csv=csv_path, counts=counts, snapshots=snapshots,
                           service=service, client=TestClient(build_app(service)), tmp=tmp_path)


def get(world, code=SEED, **params):
    return world.client.get(f'/api/stocks/{code}/peers', params=params)


def peers_by_code(body):
    return {peer['code']: peer for peer in body['peers']}


# ── the answer ───────────────────────────────────────────────────────────────

def test_the_answer_has_the_spec_keys_and_value_sets(world):
    response = get(world)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == TOP_KEYS
    assert set(body['seed']) == SEED_KEYS and set(body['seed']['profile']) == PROFILE_KEYS
    assert all(set(s) == {'no', 'name', 'revenue_share_pct', 'products'} for s in body['seed']['profile']['segments'])
    assert set(body['seed']['coverage']) == COVERAGE_KEYS and set(body['seed']['price']) == PRICE_KEYS
    assert set(body['basis']) == BASIS_KEYS
    assert set(body['segment']) == {'no', 'name', 'revenue_share_pct'}
    assert body['peers']
    for peer in body['peers']:
        assert set(peer) == PEER_KEYS
        assert peer['tier'] in {'very_high', 'high', 'related'}
        for match, keys in ((peer['company_match'], {'similarity', 'percentile', 'tier'}),
                            (peer['segment_match'], {'segment', 'revenue_share_pct', 'similarity', 'percentile',
                                                     'tier'})):
            assert match is None or (set(match) == keys and match['tier'] in {'very_high', 'high', 'related'})
        assert peer['company_match'] is not None or peer['segment_match'] is not None
        assert all(set(s) == {'no', 'name', 'revenue_share_pct'} for s in peer['segments'])
        assert all(set(t) == {'term', 'companies'} for t in peer['shared_terms'])
        assert set(peer['coverage']) == COVERAGE_KEYS and peer['coverage']['label'] in {'none', 'few', 'covered'}
        if peer['price'] is not None:
            assert set(peer['price']) == PRICE_KEYS
            assert set(peer['price']['returns']) == set(peer['price']['excess']) == {'1w', '1m', '3m'}
        assert peer['reaction'] in {'none', 'partial', 'reacted', 'undetermined'}
        assert isinstance(peer['candidate'], bool)
        assert set(peer['not_candidate_reasons']) <= {'has_reports', 'reacted', 'undetermined', 'not_traded',
                                                      'low_liquidity', 'holding', 'weak_similarity'}
        assert peer['candidate'] is (peer['not_candidate_reasons'] == [])


def test_the_list_is_merged_ranked_and_leaves_out_the_seed_delisted_profileless_and_weak_companies(world):
    body = get(world).json()
    assert [p['code'] for p in body['peers']] == LISTED
    assert [p['rank'] for p in body['peers']] == [1, 2, 3, 4, 5, 6]
    left_out = {SEED, '222220', '555550', '666660', '333330', '999990'}
    assert not left_out & {p['code'] for p in body['peers']}


def test_each_row_carries_its_matches_terms_industry_and_segments(world):
    rows = peers_by_code(get(world).json())
    fidelix = rows['032580']
    assert (fidelix['name'], fidelix['market'], fidelix['sector_minor']) == ('피델릭스', 'KOSDAQ', '비메모리_팹리스')
    assert fidelix['one_line'] == '032580 틈새 업종' and fidelix['tier'] == 'very_high'
    assert fidelix['company_match']['similarity'] == pytest.approx(0.8)
    assert fidelix['company_match']['percentile'] == pytest.approx(99.7)
    assert fidelix['company_match']['tier'] == 'very_high'
    assert fidelix['segment_match']['segment'] == '메모리' and fidelix['segment_match']['revenue_share_pct'] == 60.0
    assert fidelix['segment_match']['similarity'] == pytest.approx(0.85)
    assert fidelix['segment_match']['percentile'] == pytest.approx(99.75)
    # Every segment of the peer, in revenue-share order (unknown last).
    assert fidelix['segments'] == [{'no': 2, 'name': '모듈', 'revenue_share_pct': 80.0},
                                   {'no': 0, 'name': '메모리', 'revenue_share_pct': 60.0},
                                   {'no': 1, 'name': '기타', 'revenue_share_pct': -1.0}]
    # Six shared terms: the five held by the fewest companies, with the term table's display.
    assert fidelix['shared_terms'] == [{'term': 'Legacy DRAM', 'companies': 2}, {'term': 'MCP', 'companies': 3},
                                       {'term': 'LPDDR', 'companies': 4}, {'term': 'NOR Flash', 'companies': 5},
                                       {'term': 'DRAM', 'companies': 9}]
    assert fidelix['same_industry'] is True
    assert (fidelix['is_holding'], fidelix['is_financial'], fidelix['info_quality']) == (False, False, '충분')
    assert fidelix['coverage'] == counts_record('none') and fidelix['price'] == price_of('032580')

    module = rows['111110']                     # listed by its segment only
    assert module['company_match'] is None and module['tier'] == 'very_high'
    assert module['segment_match']['segment'] == '메모리 모듈'
    assert module['segment_match']['revenue_share_pct'] == 70.0 and module['segment_match']['percentile'] == 100.0
    assert module['shared_terms'] == [] and module['same_industry'] is None

    hynix = rows['000660']                      # its segment is below related
    assert hynix['segment_match'] is None and hynix['tier'] == 'high'
    assert hynix['company_match']['percentile'] == pytest.approx(98.3)
    assert hynix['shared_terms'] == [{'term': 'DRAM', 'companies': 9}] and hynix['same_industry'] is False
    assert hynix['market'] == 'KOSPI' and hynix['segments'] == [{'no': 0, 'name': 'DRAM', 'revenue_share_pct': 70.0}]
    assert rows['444440']['is_holding'] is True and rows['888880']['segments'] == []


def test_reaction_and_candidate_per_peer(world):
    rows = peers_by_code(get(world).json())
    expected = {
        '032580': ('none', True, []),
        '111110': ('partial', False, ['low_liquidity']),
        '000660': ('reacted', False, ['has_reports', 'reacted']),
        '777770': ('undetermined', False, ['undetermined', 'not_traded']),
        '888880': ('undetermined', False, ['undetermined', 'not_traded', 'low_liquidity']),
        '444440': ('undetermined', False, ['undetermined', 'holding']),
    }
    assert {code: (r['reaction'], r['candidate'], r['not_candidate_reasons']) for code, r in rows.items()} == expected
    assert rows['888880']['price'] is None


def test_the_seed_and_the_basis(world):
    body = get(world).json()
    assert body['seed'] == {
        'code': SEED, 'name': '제주반도체', 'market': 'KOSDAQ', 'sector_minor': '비메모리_팹리스',
        'profile': {'niche_industry': '레거시 DRAM 팹리스', 'summary': '레거시 DRAM과 MCP를 설계해 판다',
                    'keywords': ['Legacy DRAM', 'MCP', 'LPDDR'],
                    'segments': [{'no': 1, 'name': '메모리', 'revenue_share_pct': 98.1,
                                  'products': ['Legacy DRAM', 'MCP']},
                                 {'no': 0, 'name': '기타', 'revenue_share_pct': 1.9, 'products': ['기타']}]},
        'is_holding': False, 'is_financial': False, 'info_quality': '충분',
        'coverage': counts_record('covered', 3), 'price': price_of(SEED)}
    assert body['basis'] == {'fiscal_year': FY, 'build_id': 1, 'built_at': '2026-10-01T09:00:00+00:00',
                             'companies_profiled': 2350, 'companies_eligible': 2400}
    assert body['window'] == '1m'
    assert body['segment'] == {'no': 1, 'name': '메모리', 'revenue_share_pct': 98.1}     # the largest share
    assert (body['price_as_of'], body['seed_excess_pct'], body['judgeable']) == ('2026-10-08', 20.0, True)


def test_report_counts_and_prices_are_asked_once_for_the_seed_and_the_peers(world):
    get(world)
    assert world.counts.calls == [([SEED] + LISTED, 365)]
    assert world.snapshots.calls == [[SEED] + LISTED]
    assert logic.COVERAGE_DAYS == 365


def test_the_window_picks_the_period_of_the_seed_and_the_peers(world):
    week = get(world, window='1w').json()
    assert (week['window'], week['seed_excess_pct'], week['judgeable']) == ('1w', 5.0, False)
    assert {p['reaction'] for p in week['peers']} == {'undetermined'}
    assert not any(p['candidate'] for p in week['peers'])
    quarter = peers_by_code(get(world, window='3m').json())
    assert quarter['032580']['reaction'] == 'reacted'                        # 30 / 40 = 0.75
    assert quarter['111110']['reaction'] == 'undetermined'                   # no 3m excess return
    assert [p['code'] for p in get(world, window='3m').json()['peers']] == LISTED   # the list does not change


def test_another_segment_chip_compares_that_segment(world):
    body = get(world, segment=0).json()
    assert body['segment'] == {'no': 0, 'name': '기타', 'revenue_share_pct': 1.9}
    rows = peers_by_code(body)
    assert [p['code'] for p in body['peers']] == ['111110', '032580', '000660', '777770', '888880', '444440']
    assert rows['111110']['segment_match']['segment'] == '기타 부품'
    assert rows['111110']['segment_match']['percentile'] == pytest.approx(99.75)
    assert rows['032580']['segment_match']['segment'] == '기타'
    assert rows['032580']['segment_match']['revenue_share_pct'] == -1.0
    assert rows['032580']['segment_match']['tier'] == 'related'
    assert rows['032580']['tier'] == 'very_high'                             # its company match
    assert get(world, segment=1).json()['segment']['name'] == '메모리'


def test_a_seed_without_segments_has_no_segment_side(world):
    body = get(world, '999990').json()
    assert body['segment'] is None and body['seed']['profile']['segments'] == []
    assert [p['code'] for p in body['peers']] == ['777770']
    assert body['peers'][0]['segment_match'] is None and body['peers'][0]['company_match']['tier'] == 'high'
    assert get(world, '999990', segment=0).json() == NO_SEGMENT


def test_a_company_without_a_profile_row_is_left_out_even_when_a_match_names_it(world, monkeypatch):
    original = PeersStore.match_company_profiles

    def with_a_stray_code(self, *args, **kwargs):
        return original(self, *args, **kwargs) + [{'stock_code': '555550', 'similarity': 0.99}]

    monkeypatch.setattr(PeersStore, 'match_company_profiles', with_a_stray_code)
    assert '555550' not in {p['code'] for p in get(world).json()['peers']}


def test_an_empty_price_table_is_not_a_readiness_problem(world, monkeypatch):
    monkeypatch.setattr(prices, 'snapshots', Snapshots({}))
    body = get(world).json()
    assert body['seed']['price'] is None
    assert (body['price_as_of'], body['seed_excess_pct'], body['judgeable']) == (None, None, False)
    assert all(p['price'] is None and p['reaction'] == 'undetermined' for p in body['peers'])


# ── the check order ──────────────────────────────────────────────────────────

@pytest.mark.parametrize('path', [
    '/api/stocks/abc/peers', '/api/stocks/08022/peers', '/api/stocks/0802201/peers',
    '/api/stocks/a80220/peers', '/api/stocks/08022 /peers',
    f'/api/stocks/{SEED}/peers?window=2m', f'/api/stocks/{SEED}/peers?window=',
    f'/api/stocks/{SEED}/peers?segment=-1', f'/api/stocks/{SEED}/peers?segment=x',
    f'/api/stocks/{SEED}/peers?segment=1.5',
])
def test_a_malformed_request_is_422_before_anything_is_prepared(path, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = TestClient(build_app(PeersService()))     # no DB settings, no stock list
    response = client.get(path)
    assert response.status_code == 422
    assert isinstance(response.json()['detail'], list)


def test_letters_in_a_code_are_fine(world):
    assert get(world, '0001A0').json() == STOCK_NOT_FOUND


def test_readiness_comes_before_the_404s(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = TestClient(build_app(PeersService()))
    assert client.get('/api/stocks/ZZZZZZ/peers').json() == not_ready(DB_REASON)


def test_a_code_not_in_the_stock_list_is_404_before_the_profile_and_segment_checks(world):
    for code in ('ZZZZZZ', '222220'):                 # 222220 has a profile but is not listed
        response = get(world, code, segment=9)
        assert response.status_code == 404 and response.json() == STOCK_NOT_FOUND


@pytest.mark.parametrize('code', ['555550', '666660', '123450'])
def test_a_listed_company_without_peers_data_in_the_public_build_is_404(world, code):
    # 555550: failed profile; 666660: a 2024 profile only; 123450: a profile but no embedding.
    response = get(world, code, segment=9)
    assert response.status_code == 404 and response.json() == NO_PEER_DATA


def test_a_segment_number_the_seed_does_not_have_is_422_last(world):
    response = get(world, segment=2)
    assert response.status_code == 422 and response.json() == NO_SEGMENT


# ── the public build ─────────────────────────────────────────────────────────

def table_reads(db):
    return [op for op in db.ops('select', 'peer_builds') if op.columns == BUILD_TABLE_COLUMNS]


def test_the_latest_public_build_is_looked_up_every_time_and_its_tables_read_once(world):
    for _ in range(3):
        assert get(world).json()['basis']['build_id'] == 1
    assert len(world.db.ops('select', 'peer_builds')) == 3 + 1
    assert [op.filters for op in table_reads(world.db)] == [[('eq', 'build_id', 1)]]

    # Unpublished builds never count; a new public build counts from the next request.
    for build_id, status in ((2, 'pilot'), (3, 'running'), (4, 'incomplete'), (5, 'failed')):
        world.db.table('peer_builds').insert(build_row(build_id, status=status,
                                                      finished_at='2026-10-08T00:00:00+00:00')).execute()
    assert get(world).json()['basis']['build_id'] == 1
    newer = [[95.0, 0.5], [98.0, 0.55], [99.5, 0.6], [100.0, 0.65]]       # 000660 (0.65) now tops it
    world.db.table('peer_builds').insert(build_row(6, finished_at='2026-10-09T00:00:00+00:00',
                                                  company_quantiles=newer)).execute()
    body = get(world).json()
    assert body['basis']['build_id'] == 6
    assert peers_by_code(body)['000660']['tier'] == 'very_high'
    get(world)
    assert [op.filters for op in table_reads(world.db)] == [[('eq', 'build_id', 1)], [('eq', 'build_id', 6)]]
    assert world.made == [(URL, KEY)]                     # one DB client for the process


def test_a_build_whose_tables_cannot_be_read_grades_nothing_and_is_not_kept(world, monkeypatch):
    calls = []
    original = PeersStore.build_tables

    def gone_once(self, build_id):
        calls.append(build_id)
        return None if len(calls) == 1 else original(self, build_id)

    monkeypatch.setattr(PeersStore, 'build_tables', gone_once)
    assert get(world).json()['peers'] == []
    assert [p['code'] for p in get(world).json()['peers']] == LISTED
    assert calls == [1, 1]


def test_no_public_build_is_a_503_that_is_not_remembered(world):
    world.db.tables['peer_builds'] = [build_row(1, status='incomplete')]
    assert get(world).json() == not_ready(NO_BUILD_REASON)
    assert get(world).status_code == 503
    world.db.table('peer_builds').insert(build_row(2)).execute()
    response = get(world)
    assert response.status_code == 200 and response.json()['basis']['build_id'] == 2


# ── readiness (harness rules) ────────────────────────────────────────────────

def test_without_db_settings_only_these_addresses_are_503_and_they_recover(world, monkeypatch):
    monkeypatch.delenv('SUPABASE_SERVICE_KEY')
    response = get(world)
    assert response.status_code == 503 and response.json() == not_ready(DB_REASON)
    assert world.client.get('/api/health').json() == {'status': 'ok'}
    assert world.made == []
    monkeypatch.setenv('SUPABASE_SERVICE_KEY', KEY)
    assert get(world).status_code == 200


def test_an_unreadable_stock_list_is_503_with_the_fixed_sentence_and_recovers(world, monkeypatch, caplog):
    missing = world.tmp / 'nowhere' / 'stocks.csv'
    monkeypatch.setenv('KRX_CSV_PATH', str(missing))
    response = get(world)
    assert response.json() == not_ready(STOCK_LIST_REASON)
    assert str(missing) not in response.text and 'nowhere' not in response.text
    assert 'nowhere' in caplog.text                       # the cause stays in the local log
    write_stock_list(world.tmp / 'later.csv')
    monkeypatch.setenv('KRX_CSV_PATH', str(world.tmp / 'later.csv'))
    assert get(world).status_code == 200


def test_the_db_is_prepared_before_the_stock_list(world, monkeypatch):
    monkeypatch.delenv('SUPABASE_URL')
    monkeypatch.setenv('KRX_CSV_PATH', str(world.tmp / 'nowhere.csv'))
    assert get(world).json() == not_ready(DB_REASON)


def test_values_added_to_dotenv_count_on_the_next_request(world, env_file, monkeypatch):
    for name in ('SUPABASE_URL', 'SUPABASE_SERVICE_KEY', 'KRX_CSV_PATH'):
        monkeypatch.delenv(name)
    assert get(world).json() == not_ready(DB_REASON)
    env_file.write_text(f'SUPABASE_URL={URL}\nSUPABASE_SERVICE_KEY={KEY}\n', encoding='utf-8')
    assert get(world).json() == not_ready(STOCK_LIST_REASON)
    with env_file.open('a', encoding='utf-8') as f:
        f.write(f'KRX_CSV_PATH={world.csv}\n')
    assert get(world).status_code == 200


def test_creating_the_service_reads_nothing(env_file, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    env_file.write_text(f'SUPABASE_URL={URL}\nSUPABASE_SERVICE_KEY={KEY}\n', encoding='utf-8')
    monkeypatch.setattr(service_module, '_service', None)
    PeersService()
    assert get_service() is get_service()
    assert 'SUPABASE_URL' not in os.environ                 # .env not read
    # (core.db.supabase_client refuses in every peers test: no client was made either.)


def test_the_router_uses_one_service_for_the_process(world, monkeypatch):
    monkeypatch.setattr(service_module, '_service', None)
    client = TestClient(build_app())
    assert client.get(f'/api/stocks/{SEED}/peers').status_code == 200
    assert client.get(f'/api/stocks/{SEED}/peers').status_code == 200
    assert world.made == [(URL, KEY)]
    assert service_module._service is not None


@pytest.mark.parametrize('area, reason, window', [
    ('리포트', DB_REASON, 'report_counts'),
    ('주가', DB_REASON, 'snapshots'),
])
def test_another_features_not_ready_passes_through_unchanged(world, monkeypatch, area, reason, window):
    def refuse(*args, **kwargs):
        raise NotReady(area, reason)

    monkeypatch.setattr(coverage if window == 'report_counts' else prices, window, refuse)
    response = get(world)
    assert response.status_code == 503 and response.json() == not_ready(reason, area)


def test_no_answer_carries_a_key_a_url_or_a_local_path(world, monkeypatch):
    texts = [get(world).text, get(world, segment=0).text]
    monkeypatch.setenv('KRX_CSV_PATH', str(world.tmp / 'gone.csv'))
    world.service._stocks = None
    texts.append(get(world).text)
    for text in texts:
        assert KEY not in text and URL not in text and str(world.tmp) not in text
        json.loads(text)
