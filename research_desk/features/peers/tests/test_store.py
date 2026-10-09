"""PeersStore over the in-memory Supabase stand-in: paged reads, profile replacement, the terms
update, embeddings per model, builds and the two match functions (migration 008)."""
from __future__ import annotations

import pytest

from research_desk.features.peers import store as store_module
from research_desk.features.peers.store import PeersStore

from .fakes import DIMS, FakeSupabase, pseudo_vector, unit

FY, PV, MODEL = 2025, 'peer-profile@1.0', 'text-embedding-3-large'


def profile_row(code, status='ok', **extra):
    return {'fiscal_year': FY, 'profile_version': PV, 'stock_code': code, 'status': status,
            'rcept_no': 'R1', 'parser_version': '0.2.0', 'profile': {'niche_industry': code},
            'terms': ['old'], **extra}


def segment_row(code, seg_no, name='부문'):
    return {'fiscal_year': FY, 'profile_version': PV, 'stock_code': code, 'seg_no': seg_no,
            'name': name, 'products': ['p'], 'keywords': ['k'], 'revenue_share_pct': 50.0}


def embedding_row(code, model=MODEL, seg_no=None):
    row = {'fiscal_year': FY, 'profile_version': PV, 'stock_code': code, 'embed_model': model,
           'embedding': unit(pseudo_vector(f'{code}{seg_no}{model}'))}
    if seg_no is not None:
        row['seg_no'] = seg_no
    return row


def world(codes=('000010', '000020')):
    return FakeSupabase(
        company_profiles=[profile_row(c) for c in codes],
        company_segments=[segment_row(c, n) for c in codes for n in (0, 1)],
        company_embeddings=[embedding_row(c) for c in codes],
        segment_embeddings=[embedding_row(c, seg_no=n) for c in codes for n in (0, 1)],
    )


# ── paged reads ──────────────────────────────────────────────────────────────

def test_reads_go_1000_rows_at_a_time_until_a_short_page():
    db = FakeSupabase(company_profiles=[profile_row(f'{i:06d}') for i in range(2345)])
    rows = PeersStore(db).profiles(FY, PV, 'stock_code')
    assert len(rows) == 2345 and rows[0] == {'stock_code': '000000'}
    assert [op.window for op in db.ops('select', 'company_profiles')] == [(0, 999), (1000, 1999), (2000, 2999)]


def test_profiles_can_be_limited_to_one_status():
    db = FakeSupabase(company_profiles=[profile_row('000010'), profile_row('000020', status='failed')])
    assert [r['stock_code'] for r in PeersStore(db).profiles(FY, PV, 'stock_code', status='ok')] == ['000010']


def test_vectors_come_back_as_lists_of_floats():
    db = world()
    vectors = PeersStore(db).company_vectors(FY, PV, MODEL)
    assert [code for code, _ in vectors] == ['000010', '000020']
    assert all(isinstance(v, list) and len(v) == DIMS and isinstance(v[0], float) for _, v in vectors)
    assert vectors[0][1] == pytest.approx(db.rows('company_embeddings', stock_code='000010')[0]['embedding'])
    segments = PeersStore(db).segment_vectors(FY, PV, MODEL)
    assert [(code, no) for code, no, _ in segments] == [('000010', 0), ('000010', 1), ('000020', 0), ('000020', 1)]


def test_vector_reads_use_smaller_pages():
    db = FakeSupabase(company_profiles=[profile_row(f'{i:06d}') for i in range(250)],
                      company_embeddings=[embedding_row(f'{i:06d}') for i in range(250)])
    assert len(PeersStore(db).company_vectors(FY, PV, MODEL)) == 250
    assert [op.window for op in db.ops('select', 'company_embeddings')] == [(0, 199), (200, 399)]


def test_parse_vector_reads_text_and_lists():
    assert store_module.parse_vector('[0.5,-1,2e-3]') == [0.5, -1.0, 0.002]
    assert store_module.parse_vector([1, 2]) == [1.0, 2.0]


# ── profiles ─────────────────────────────────────────────────────────────────

def test_replacing_a_profile_deletes_the_old_row_with_its_segments_and_embeddings_first():
    db = world()
    new = profile_row('000010', rcept_no='R2', terms=['new'])
    PeersStore(db).replace_profile(new, [segment_row('000010', 0, name='새 부문')])
    assert db.rows('company_profiles', stock_code='000010')[0]['rcept_no'] == 'R2'
    assert db.rows('company_profiles', stock_code='000010')[0]['status'] == 'ok'
    assert [s['name'] for s in db.rows('company_segments', stock_code='000010')] == ['새 부문']
    assert db.rows('company_embeddings', stock_code='000010') == []
    assert db.rows('segment_embeddings', stock_code='000010') == []
    assert len(db.rows('company_embeddings', stock_code='000020')) == 1      # others untouched
    kinds = [(op.kind, op.table) for op in db.log]
    assert kinds == [('delete', 'company_profiles'), ('insert', 'company_profiles'),
                     ('insert', 'company_segments'), ('update', 'company_profiles')]
    # The row is written without a status and marked ok only after its segments are in.
    assert db.log[1].payload['status'] is None and db.log[3].payload == {'status': 'ok'}


def test_a_failed_profile_is_written_as_is_without_segments():
    db = FakeSupabase()
    PeersStore(db).replace_profile(profile_row('000010', status='failed', fail_reason='시간 초과'))
    assert db.rows('company_profiles')[0]['status'] == 'failed'
    assert [(op.kind, op.table) for op in db.log] == [('delete', 'company_profiles'),
                                                      ('insert', 'company_profiles')]


def test_terms_are_rewritten_with_an_update_of_that_column_only():
    db = world()
    PeersStore(db).set_terms(FY, PV, '000010', ['dram', 'nor'])
    assert db.rows('company_profiles', stock_code='000010')[0]['terms'] == ['dram', 'nor']
    assert db.rows('company_profiles', stock_code='000020')[0]['terms'] == ['old']
    op = db.ops('update', 'company_profiles')[0]
    assert op.payload == {'terms': ['dram', 'nor']}
    assert {(c, v) for _, c, v in op.filters} == {('fiscal_year', FY), ('profile_version', PV),
                                                   ('stock_code', '000010')}
    assert db.ops('upsert') == []


def test_segments_of_a_profile_version_come_in_code_and_number_order():
    rows = PeersStore(world()).segments(FY, PV)
    assert [(r['stock_code'], r['seg_no']) for r in rows] == [('000010', 0), ('000010', 1),
                                                             ('000020', 0), ('000020', 1)]


# ── embeddings per model ─────────────────────────────────────────────────────

def test_saving_embeddings_of_a_new_model_leaves_the_other_model_alone():
    db = world()
    before = db.rows('company_embeddings')
    store = PeersStore(db)
    store.save_company_embeddings(FY, PV, 'text-embedding-3-small',
                                  [('000010', unit(pseudo_vector('a'))), ('000020', unit(pseudo_vector('b')))])
    store.save_segment_embeddings(FY, PV, 'text-embedding-3-small', [('000010', 0, unit(pseudo_vector('c')))])
    assert db.rows('company_embeddings', embed_model=MODEL) == before
    assert len(db.rows('company_embeddings', embed_model='text-embedding-3-small')) == 2
    assert store.company_embedding_codes(FY, PV, 'text-embedding-3-small') == {'000010', '000020'}
    assert store.segment_embedding_keys(FY, PV, 'text-embedding-3-small') == {('000010', 0)}
    assert store.segment_embedding_keys(FY, PV, MODEL) == {('000010', 0), ('000010', 1),
                                                           ('000020', 0), ('000020', 1)}
    upsert = db.ops('upsert', 'company_embeddings')[0]
    assert upsert.on_conflict == 'fiscal_year,profile_version,stock_code,embed_model'


def test_embeddings_are_written_100_rows_per_request():
    codes = [f'{i:06d}' for i in range(150)]
    db = FakeSupabase(company_profiles=[profile_row(c) for c in codes])
    PeersStore(db).save_company_embeddings(FY, PV, MODEL, [(c, unit(pseudo_vector(c))) for c in codes])
    assert [len(op.payload) for op in db.ops('upsert', 'company_embeddings')] == [100, 50]


def test_deleting_one_models_embeddings_keeps_the_profiles_and_other_models():
    db = world()
    store = PeersStore(db)
    store.save_company_embeddings(FY, PV, 'small', [('000010', unit(pseudo_vector('a')))])
    store.delete_embeddings(FY, PV, MODEL)
    assert db.rows('company_embeddings', embed_model=MODEL) == []
    assert db.rows('segment_embeddings', embed_model=MODEL) == []
    assert len(db.rows('company_embeddings', embed_model='small')) == 1
    assert len(db.rows('company_profiles')) == 2 and len(db.rows('company_segments')) == 4


def test_key_listings_and_profile_deletion():
    db = world()
    store = PeersStore(db)
    assert store.profile_keys() == {(FY, PV)}
    assert store.embedding_keys() == {(FY, PV, MODEL)}
    store.delete_profiles(FY, PV)
    assert all(db.rows(t) == [] for t in ('company_profiles', 'company_segments',
                                          'company_embeddings', 'segment_embeddings'))


# ── builds ───────────────────────────────────────────────────────────────────

def build_row(status, **extra):
    return {'fiscal_year': FY, 'profile_version': PV, 'embed_model': MODEL, 'status': status, **extra}


def test_starting_a_build_returns_its_row_with_an_id():
    db = FakeSupabase()
    row = PeersStore(db).start_build(build_row('running', started_at='2026-10-09T00:00:00+00:00',
                                               heartbeat_at='2026-10-09T00:00:00+00:00'))
    assert row['build_id'] == 1 and row['status'] == 'running'


def test_running_and_done_builds():
    db = FakeSupabase(peer_builds=[
        build_row('done', finished_at='2026-01-01T00:00:00+00:00'),
        build_row('running'),
        build_row('done', finished_at='2026-03-01T00:00:00+00:00'),
        build_row('pilot', finished_at='2026-04-01T00:00:00+00:00'),
    ])
    store = PeersStore(db)
    assert [b['build_id'] for b in store.running_builds()] == [2]
    assert [b['build_id'] for b in store.done_builds()] == [3, 1]
    assert [b['build_id'] for b in store.recent_builds(3)] == [4, 3, 2]
    assert [b['build_id'] for b in store.builds()] == [1, 2, 3, 4]


def test_failing_a_build_changes_only_a_running_build():
    db = FakeSupabase(peer_builds=[build_row('running'), build_row('done')])
    store = PeersStore(db)
    store.fail_build(1, 'boom', '2026-10-09T01:00:00+00:00')
    store.fail_build(2, 'boom', '2026-10-09T01:00:00+00:00')
    assert [(b['status'], b['message']) for b in db.rows('peer_builds')] == [('failed', 'boom'), ('done', None)]


def test_progress_updates_can_be_limited_to_a_running_build():
    db = FakeSupabase(peer_builds=[build_row('done', profiled=5)])
    PeersStore(db).update_build(1, {'profiled': 6}, only_running=True)
    assert db.rows('peer_builds')[0]['profiled'] == 5
    PeersStore(db).update_build(1, {'profiled': 7})
    assert db.rows('peer_builds')[0]['profiled'] == 7


def test_deleting_builds_by_id():
    db = FakeSupabase(peer_builds=[build_row('done'), build_row('done'), build_row('done')])
    PeersStore(db).delete_builds([1, 3])
    assert [b['build_id'] for b in db.rows('peer_builds')] == [2]
    PeersStore(db).delete_builds([])
    assert len(db.rows('peer_builds')) == 1


# ── match functions ──────────────────────────────────────────────────────────

def test_match_company_profiles_sends_the_query_and_reads_codes_and_similarities():
    db = world()
    query = db.rows('company_embeddings', stock_code='000020')[0]['embedding']
    rows = PeersStore(db).match_company_profiles(FY, PV, MODEL, query, limit=5)
    assert rows[0] == {'stock_code': '000020', 'similarity': pytest.approx(1.0)}
    assert [r['stock_code'] for r in rows] == ['000020', '000010']
    assert all(isinstance(r['similarity'], float) for r in rows)
    call = db.ops('rpc')[0]
    assert call.table == 'match_company_profiles'
    assert call.payload['p_fiscal_year'] == FY and call.payload['p_profile_version'] == PV
    assert call.payload['p_embed_model'] == MODEL and call.payload['p_limit'] == 5
    assert len(call.payload['p_query']) == DIMS


def test_match_company_segments_reads_one_row_per_segment():
    db = world()
    query = db.rows('segment_embeddings', stock_code='000010', seg_no=1)[0]['embedding']
    rows = PeersStore(db).match_company_segments(FY, PV, MODEL, query)
    assert rows[0] == {'stock_code': '000010', 'seg_no': 1, 'similarity': pytest.approx(1.0)}
    assert len(rows) == 4
    assert db.ops('rpc')[0].payload['p_limit'] == 200
