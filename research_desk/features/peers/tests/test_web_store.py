"""PeersStore reads for the web side, over the in-memory Supabase stand-in: the latest public build
(no tables) and one build's tables, one company's profile, profiles and segments of a list of codes
(in parts of ``CODES_CHUNK`` codes, paged), one company's and one segment's embedding of a model,
and the profiles whose terms hold a query key (array overlap, every key quoted)."""
from __future__ import annotations

import pytest

from research_desk.features.peers import store as store_module
from research_desk.features.peers.store import PeersStore

from .fakes import FakeAPIError, FakeSupabase, parse_array_literal, pseudo_vector, unit

FY, PV, MODEL = 2025, 'peer-profile@1.0', 'text-embedding-3-large'
TABLES = ('company_quantiles', 'segment_quantiles', 'term_table')


def build_row(build_id, status='done', finished_at='2026-10-01T00:00:00+00:00', **extra):
    return {'build_id': build_id, 'fiscal_year': FY, 'profile_version': PV, 'embed_model': MODEL,
            'status': status, 'eligible': 10, 'profiled': 10, 'failed': 0, 'finished_at': finished_at,
            'company_quantiles': [[95.0, 0.5], [100.0, 1.0]], 'segment_quantiles': [[95.0, 0.6], [100.0, 1.0]],
            'term_table': {'dram': {'display': 'DRAM', 'companies': 2}}, **extra}


def profile_row(code, status='ok', fiscal_year=FY, terms=('dram',), **extra):
    return {'fiscal_year': fiscal_year, 'profile_version': PV, 'stock_code': code, 'status': status,
            'one_line': f'{code} 한 줄', 'is_holding': False, 'is_financial': False, 'info_quality': '충분',
            'terms': list(terms), 'profile': {'niche_industry': code}, **extra}


def segment_row(code, seg_no, fiscal_year=FY, share=50.0):
    return {'fiscal_year': fiscal_year, 'profile_version': PV, 'stock_code': code, 'seg_no': seg_no,
            'name': f'부문{seg_no}', 'products': ['p'], 'keywords': ['k'], 'revenue_share_pct': share}


def vector(text):
    return unit(pseudo_vector(text))


# ── builds ───────────────────────────────────────────────────────────────────

def test_the_latest_public_build_is_one_cheap_read_without_its_tables():
    db = FakeSupabase(peer_builds=[
        build_row(1, finished_at='2026-09-01T00:00:00+00:00'),
        build_row(2, finished_at='2026-10-01T00:00:00+00:00'),
        build_row(3, status='pilot', finished_at='2026-10-05T00:00:00+00:00'),
        build_row(4, status='running', finished_at=None),
        build_row(5, status='incomplete', finished_at='2026-10-06T00:00:00+00:00'),
        build_row(6, status='failed', finished_at='2026-10-07T00:00:00+00:00'),
    ])
    build = PeersStore(db).latest_done_build()
    assert build['build_id'] == 2 and build['status'] == 'done'
    assert build['fiscal_year'] == FY and build['embed_model'] == MODEL and build['profiled'] == 10
    assert not set(TABLES) & set(build)
    [op] = db.ops('select', 'peer_builds')
    assert op.limit == 1 and not any(name in op.columns for name in TABLES)


def test_a_later_finish_wins_over_a_higher_build_id():
    db = FakeSupabase(peer_builds=[build_row(7, finished_at='2026-09-01T00:00:00+00:00'),
                                   build_row(8, finished_at='2026-08-01T00:00:00+00:00')])
    assert PeersStore(db).latest_done_build()['build_id'] == 7


def test_no_public_build_is_none():
    db = FakeSupabase(peer_builds=[build_row(1, status='pilot'), build_row(2, status='running')])
    assert PeersStore(db).latest_done_build() is None


def test_a_builds_tables_are_read_by_its_id():
    db = FakeSupabase(peer_builds=[build_row(1), build_row(2, term_table={'nor': {'display': 'NOR', 'companies': 1}})])
    tables = PeersStore(db).build_tables(2)
    assert tables == {'company_quantiles': [[95.0, 0.5], [100.0, 1.0]],
                      'segment_quantiles': [[95.0, 0.6], [100.0, 1.0]],
                      'term_table': {'nor': {'display': 'NOR', 'companies': 1}}}
    [op] = db.ops('select', 'peer_builds')
    assert op.filters == [('eq', 'build_id', 2)]
    assert PeersStore(db).build_tables(99) is None


# ── profiles and segments ────────────────────────────────────────────────────

def test_one_profile_is_the_ok_profile_of_the_key():
    db = FakeSupabase(company_profiles=[profile_row('000010'), profile_row('000020', status='failed'),
                                        profile_row('000030', fiscal_year=2024)])
    store = PeersStore(db)
    assert store.profile(FY, PV, '000010', 'stock_code, one_line') == {'stock_code': '000010',
                                                                      'one_line': '000010 한 줄'}
    assert store.profile(FY, PV, '000020') is None
    assert store.profile(FY, PV, '000030') is None
    assert store.profile(2024, PV, '000030')['stock_code'] == '000030'


def test_profiles_of_codes_are_read_in_parts_and_only_ok_ones_come_back():
    codes = [f'{i:06d}' for i in range(250)]
    rows = [profile_row(code) for code in codes] + [profile_row('999999', status='failed')]
    db = FakeSupabase(company_profiles=rows)
    found = PeersStore(db).profiles_of(FY, PV, list(reversed(codes)) + ['999999', '000001'],
                                       'stock_code, one_line, terms')
    assert [r['stock_code'] for r in found] == codes
    assert set(found[0]) == {'stock_code', 'one_line', 'terms'}
    ins = [next(f for f in op.filters if f[0] == 'in') for op in db.ops('select', 'company_profiles')]
    assert [len(values) for _, _, values in ins] == [100, 100, 51]
    assert store_module.CODES_CHUNK == 100


def test_no_codes_read_nothing():
    db = FakeSupabase()
    store = PeersStore(db)
    assert store.profiles_of(FY, PV, [], 'stock_code') == [] and store.segments_of(FY, PV, []) == []
    assert db.log == []


def test_segments_of_codes_come_by_code_and_number():
    db = FakeSupabase(company_profiles=[profile_row(c) for c in ('000010', '000020', '000030')]
                      + [profile_row('000010', fiscal_year=2024)],
                      company_segments=[segment_row('000020', 1), segment_row('000010', 2), segment_row('000010', 0),
                                        segment_row('000030', 0), segment_row('000010', 0, fiscal_year=2024)])
    rows = PeersStore(db).segments_of(FY, PV, ['000020', '000010'])
    assert [(r['stock_code'], r['seg_no']) for r in rows] == [('000010', 0), ('000010', 2), ('000020', 1)]
    assert rows[0]['name'] == '부문0' and rows[0]['revenue_share_pct'] == 50.0


def test_a_large_part_of_segments_is_paged():
    codes = [f'{i:06d}' for i in range(100)]
    db = FakeSupabase(company_profiles=[profile_row(c) for c in codes],
                      company_segments=[segment_row(c, n) for c in codes for n in range(12)])
    assert len(PeersStore(db).segments_of(FY, PV, codes)) == 1200
    assert [op.window for op in db.ops('select', 'company_segments')] == [(0, 999), (1000, 1999)]


# ── one company's embeddings ─────────────────────────────────────────────────

def embedding_world():
    return FakeSupabase(
        company_profiles=[profile_row('000010'), profile_row('000020')],
        company_segments=[segment_row('000010', 0), segment_row('000010', 1)],
        company_embeddings=[
            {'fiscal_year': FY, 'profile_version': PV, 'stock_code': '000010', 'embed_model': MODEL,
             'embedding': vector('c10')},
            {'fiscal_year': FY, 'profile_version': PV, 'stock_code': '000020', 'embed_model': 'other-model',
             'embedding': vector('c20')}],
        segment_embeddings=[
            {'fiscal_year': FY, 'profile_version': PV, 'stock_code': '000010', 'seg_no': 1, 'embed_model': MODEL,
             'embedding': vector('s10-1')}],
    )


def test_one_companys_vector_of_the_model_comes_back_as_floats():
    store = PeersStore(embedding_world())
    assert store.company_vector(FY, PV, MODEL, '000010') == pytest.approx(vector('c10'))
    assert all(isinstance(x, float) for x in store.company_vector(FY, PV, MODEL, '000010'))
    assert store.company_vector(FY, PV, MODEL, '000020') is None          # only another model's
    assert store.company_vector(FY, PV, 'other-model', '000020') == pytest.approx(vector('c20'))


def test_one_segments_vector_of_the_model():
    store = PeersStore(embedding_world())
    assert store.segment_vector(FY, PV, MODEL, '000010', 1) == pytest.approx(vector('s10-1'))
    assert store.segment_vector(FY, PV, MODEL, '000010', 0) is None
    assert store.segment_vector(FY, PV, 'other-model', '000010', 1) is None


# ── profiles holding a query key ─────────────────────────────────────────────

def test_profiles_whose_terms_hold_a_key_exactly():
    db = FakeSupabase(company_profiles=[
        profile_row('000010', terms=['dram', 'nor']), profile_row('000020', terms=['dram모듈']),
        profile_row('000030', terms=['legacydram']), profile_row('000040', status='failed', terms=['dram']),
        profile_row('000050', fiscal_year=2024, terms=['dram']), profile_row('000060', terms=['nand', 'nor'])])
    rows = PeersStore(db).profiles_with_terms(FY, PV, ['dram', 'nor'])
    assert rows == [{'stock_code': '000010', 'terms': ['dram', 'nor']},
                    {'stock_code': '000060', 'terms': ['nand', 'nor']}]


def test_every_key_is_quoted_so_commas_braces_quotes_and_backslashes_stay_inside_it():
    odd = ['a,b', '{x}', 'say "hi"', 'back\\slash', '공백 있음', 'NULL']
    db = FakeSupabase(company_profiles=[profile_row(f'00001{i}', terms=[key]) for i, key in enumerate(odd)]
                      + [profile_row('000099', terms=['a', 'b'])])
    rows = PeersStore(db).profiles_with_terms(FY, PV, odd)
    assert [r['terms'] for r in rows] == [[key] for key in odd]
    [op] = db.ops('select', 'company_profiles')
    assert ('ov', 'terms', odd) in op.filters


def test_the_array_literal_quotes_each_value():
    literal = store_module.array_literal(['a,b', 'c"d', 'e\\f'])
    assert literal == '{"a,b","c\\"d","e\\\\f"}'
    assert parse_array_literal(literal) == ['a,b', 'c"d', 'e\\f']
    # The stand-in reads an unquoted literal the way PostgreSQL does: a comma parts it.
    assert parse_array_literal('{a,b}') == ['a', 'b']
    with pytest.raises(FakeAPIError):
        parse_array_literal('{"open}')


def test_no_key_reads_nothing_and_many_matches_are_paged():
    db = FakeSupabase(company_profiles=[profile_row(f'{i:06d}', terms=['dram']) for i in range(1500)])
    store = PeersStore(db)
    assert store.profiles_with_terms(FY, PV, []) == [] and db.log == []
    assert len(store.profiles_with_terms(FY, PV, ['dram'])) == 1500
    assert [op.window for op in db.ops('select', 'company_profiles')] == [(0, 999), (1000, 1999)]
