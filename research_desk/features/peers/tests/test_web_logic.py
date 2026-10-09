"""Peers web calculations (no DB, no network): the peers list (spec §6.2–§6.4), the price reaction
(§9), the candidate flag (§10) and theme search's query terms, term ranking and RRF (§12.2)."""
from __future__ import annotations

import pytest

from research_desk.features.peers import logic

SYN = logic.parse_synonyms({'DRAM': ['디램', 'D램'], '이차전지': ['2차전지'], 'NOR Flash': ['노어플래시']})

# Every point sits on an exact binary fraction, so the tier boundaries are hit exactly.
TABLE = [[95.0, 0.5], [98.0, 0.625], [99.5, 0.75], [100.0, 0.875]]


def company(code, similarity):
    return {'stock_code': code, 'similarity': similarity}


def segment(code, seg_no, similarity):
    return {'stock_code': code, 'seg_no': seg_no, 'similarity': similarity}


# ── segment order and the chosen segment (§6.2) ──────────────────────────────

def test_segments_are_put_in_revenue_share_order_unknown_last_ties_by_number():
    rows = [{'seg_no': 2, 'revenue_share_pct': -1.0}, {'seg_no': 0, 'revenue_share_pct': 1.9},
            {'seg_no': 3, 'revenue_share_pct': 1.9}, {'seg_no': 1, 'revenue_share_pct': 98.1}]
    assert [r['seg_no'] for r in logic.ordered_segments(rows)] == [1, 0, 3, 2]
    assert logic.ordered_segments([]) == []


# ── the matches of one side (§6.2, §6.4) ─────────────────────────────────────

def test_each_company_keeps_its_best_segment_only():
    rows = [segment('A', 0, 0.5), segment('B', 0, 0.7), segment('A', 2, 0.9), segment('A', 1, 0.9)]
    assert logic.best_segments(rows) == [segment('A', 1, 0.9), segment('B', 0, 0.7)]


def test_one_side_keeps_the_nearest_eligible_companies_up_to_its_cap():
    rows = [company('C', 0.7), company('SEED', 1.0), company('A', 0.9), company('GONE', 0.95),
            company('B', 0.9)]
    keep = {'A', 'B', 'C'}.__contains__
    assert logic.top_matches(rows, keep, 2) == [company('A', 0.9), company('B', 0.9)]
    assert logic.top_matches(rows, keep, 100) == [company('A', 0.9), company('B', 0.9), company('C', 0.7)]


@pytest.mark.parametrize('similarity, percentile, tier', [
    (0.4999, None, None),
    (0.5, 95.0, 'related'),
    (0.5625, 96.5, 'related'),
    (0.625, 98.0, 'high'),
    (0.75, 99.5, 'very_high'),
    (0.9, 100.0, 'very_high'),
])
def test_a_similarity_is_graded_by_the_builds_percentile_table(similarity, percentile, tier):
    graded = logic.grade(similarity, TABLE)
    if tier is None:
        assert graded is None
    else:
        assert graded == {'similarity': similarity, 'percentile': percentile, 'tier': tier}


def test_no_table_grades_nothing():
    assert logic.grade(0.99, None) is None and logic.grade(0.99, []) is None


# ── merging, sorting and cutting the list (§6.3, §6.4) ───────────────────────

def test_both_sides_merge_by_company_and_rank_by_the_higher_percentile():
    peers = logic.merge_peers(
        [company('A', 0.75), company('B', 0.5625), company('C', 0.4)],
        [segment('B', 2, 0.875), segment('D', 0, 0.625), segment('C', 0, 0.3)],
        TABLE, TABLE)
    assert [p['code'] for p in peers] == ['B', 'A', 'D']      # C is below related on both sides
    b, a, d = peers
    assert b['tier'] == 'very_high'                            # the higher of related and very_high
    assert b['company_match'] == {'similarity': 0.5625, 'percentile': 96.5, 'tier': 'related'}
    assert b['segment_match'] == {'seg_no': 2, 'similarity': 0.875, 'percentile': 100.0,
                                  'tier': 'very_high'}
    assert a['segment_match'] is None and a['tier'] == 'very_high'
    assert d['company_match'] is None and d['tier'] == 'high'


def test_a_side_below_related_is_no_match_while_the_other_side_lists_the_company():
    peers = logic.merge_peers([company('A', 0.3)], [segment('A', 1, 0.625)], TABLE, TABLE)
    assert peers == [{'code': 'A', 'tier': 'high', 'company_match': None,
                      'segment_match': {'seg_no': 1, 'similarity': 0.625, 'percentile': 98.0,
                                        'tier': 'high'}}]


def test_ties_on_the_higher_percentile_go_to_the_other_percentile_then_similarity_then_code():
    peers = logic.merge_peers(
        [company('Z', 0.9), company('Y', 0.9), company('X', 0.95)],
        [segment('Z', 0, 0.5)],
        TABLE, TABLE)
    # All three reach 100; Z also has a segment match; X has the higher company similarity.
    assert [p['code'] for p in peers] == ['Z', 'X', 'Y']


def test_the_list_is_cut_after_ranking():
    rows = [company(f'{i:06d}', 0.5 + i / 1000) for i in range(80)]
    peers = logic.merge_peers(rows, [], TABLE, TABLE)
    assert len(peers) == logic.PEERS_MAX == 50
    assert peers[0]['code'] == '000079'
    assert [p['code'] for p in logic.merge_peers(rows, [], TABLE, TABLE, limit=3)] == ['000079', '000078',
                                                                                       '000077']


def test_the_shared_term_bonus_is_off_by_default_and_reorders_when_set():
    rows = [company('A', 0.6), company('B', 0.625)]
    shared = {'A': 3, 'B': 0}
    assert logic.SHARED_TERM_BONUS == 0
    assert [p['code'] for p in logic.merge_peers(rows, [], TABLE, TABLE, shared_counts=shared)] == ['B', 'A']
    bonus = logic.merge_peers(rows, [], TABLE, TABLE, shared_counts=shared, bonus=1.0)
    assert [p['code'] for p in bonus] == ['A', 'B']
    assert [p['tier'] for p in bonus] == ['related', 'high']   # the bonus never changes a tier


# ── shared terms and the industry (§6.4) ─────────────────────────────────────

def term_table(**counts):
    return {key: {'display': key.upper(), 'companies': n} for key, n in counts.items()}


def test_shared_terms_are_the_rarest_five_with_their_display_and_count():
    table = term_table(a=10, b=2, c=5, d=2, e=7, f=3)
    seed = ['a', 'b', 'c', 'd', 'e', 'f', 'x']
    peer = ['f', 'e', 'd', 'c', 'b', 'a', 'y']
    assert logic.shared_terms(seed, peer, table) == [
        {'term': 'B', 'companies': 2}, {'term': 'D', 'companies': 2}, {'term': 'F', 'companies': 3},
        {'term': 'C', 'companies': 5}, {'term': 'E', 'companies': 7}]
    assert logic.shared_count(seed, peer, table) == 6


def test_shared_terms_only_count_keys_of_the_builds_term_table():
    table = term_table(a=3)
    assert logic.shared_terms(['a', 'new'], ['new', 'a'], table) == [{'term': 'A', 'companies': 3}]
    assert logic.shared_count(['a', 'new'], ['new', 'a'], table) == 1


def test_no_shared_term_is_an_empty_list():
    assert logic.shared_terms(['a'], ['b'], term_table(a=1, b=1)) == []
    assert logic.shared_terms(None, ['b'], term_table(b=1)) == [] and logic.shared_count([], None, {}) == 0


@pytest.mark.parametrize('seed, peer, same', [
    ('비메모리_팹리스', '비메모리_팹리스', True),
    ('비메모리_팹리스', '메모리', False),
    ('', '메모리', None),
    ('메모리', '  ', None),
    (None, '메모리', None),
])
def test_same_industry_compares_the_middle_industry_names(seed, peer, same):
    assert logic.same_industry(seed, peer) is same


# ── the price reaction (§9) ──────────────────────────────────────────────────

def price(excess=None, as_of='2026-10-08', traded=True, flags=(), avg=1_000_000_000, window='1m'):
    excesses = {'1w': None, '1m': None, '3m': None}
    excesses[window] = excess
    return {'as_of': as_of, 'close': 1000, 'market_cap': 10 ** 11, 'avg_value_20d': avg, 'traded': traded,
            'returns': {'1w': None, '1m': None, '3m': None}, 'excess': excesses, 'flags': list(flags)}


@pytest.mark.parametrize('xs, judged', [(10.0, True), (9.99, False), (35.0, True), (None, False),
                                         (-12.0, False)])
def test_only_a_seed_at_least_ten_points_above_its_market_is_judged(xs, judged):
    assert logic.judgeable(xs) is judged
    expected = 'none' if judged else 'undetermined'
    assert logic.reaction(xs, '2026-10-08', price(0.0), '1m') == expected


@pytest.mark.parametrize('peer, outcome', [
    (-3.0, 'none'),
    (2.4999, 'none'),
    (2.5, 'partial'),
    (5.999, 'partial'),
    (6.0, 'reacted'),
    (25.0, 'reacted'),
])
def test_the_reaction_follows_the_ratio_to_the_seed_with_its_boundaries(peer, outcome):
    assert logic.reaction(10.0, '2026-10-08', price(peer), '1m') == outcome


def test_the_ratio_boundaries_hold_for_another_seed_too():
    assert logic.reaction(20.0, '2026-10-08', price(5.0), '1m') == 'partial'
    assert logic.reaction(20.0, '2026-10-08', price(12.0), '1m') == 'reacted'


@pytest.mark.parametrize('peer', [
    None,                                              # no snapshot
    price(None),                                       # no excess return in that window
    price(1.0, as_of='2026-10-07'),                    # another as_of
    price(1.0, as_of=None),
    price(1.0, traded=False, flags=['halted']),        # halted
    price(1.0, traded=None),
    price(1.0, flags=['halted']),
    price(1.0, flags=['no_data']),                     # not received in the last run
])
def test_a_peer_without_comparable_data_is_undetermined(peer):
    assert logic.reaction(20.0, '2026-10-08', peer, '1m') == 'undetermined'


def test_a_seed_without_as_of_judges_nothing():
    assert logic.reaction(20.0, None, price(1.0, as_of=None), '1m') == 'undetermined'


def test_the_reaction_reads_the_chosen_window():
    peer = price(2.0, window='3m')
    assert logic.reaction(20.0, '2026-10-08', peer, '3m') == 'none'
    assert logic.reaction(20.0, '2026-10-08', peer, '1m') == 'undetermined'


def test_admin_issue_and_short_history_do_not_stop_the_judgement():
    assert logic.reaction(20.0, '2026-10-08', price(1.0, flags=['admin_issue', 'short_history']), '1m') == 'none'


# ── the candidate flag (§10) ─────────────────────────────────────────────────

GOOD = dict(label='none', reaction='none', price=price(1.0, avg=500_000_000), is_holding=False, tier='related')


def test_a_peer_meeting_all_six_conditions_is_a_candidate():
    assert logic.candidacy(**GOOD) == (True, [])
    assert logic.candidacy(**(GOOD | {'reaction': 'partial'})) == (True, [])
    assert logic.candidacy(**(GOOD | {'tier': 'very_high'})) == (True, [])


@pytest.mark.parametrize('change, reasons', [
    ({'label': 'few'}, ['has_reports']),
    ({'label': 'covered'}, ['has_reports']),
    ({'reaction': 'reacted'}, ['reacted']),
    ({'reaction': 'undetermined'}, ['undetermined']),
    ({'price': price(1.0, traded=False, flags=['halted'])}, ['not_traded']),
    ({'price': price(1.0, flags=['halted'])}, ['not_traded']),
    ({'price': price(1.0, flags=['no_data'])}, ['not_traded']),
    ({'price': price(1.0, traded=None)}, ['not_traded']),
    ({'price': price(1.0, avg=499_999_999)}, ['low_liquidity']),
    ({'price': price(1.0, avg=None)}, ['low_liquidity']),
    ({'price': None}, ['not_traded', 'low_liquidity']),
    ({'is_holding': True}, ['holding']),
    ({'is_holding': None}, ['holding']),                # unknown: not shown to be a non-holding
    ({'tier': None}, ['weak_similarity']),
])
def test_each_missing_condition_gives_its_reason(change, reasons):
    assert logic.candidacy(**(GOOD | change)) == (False, reasons)


def test_reasons_come_in_the_order_of_the_conditions():
    candidate, reasons = logic.candidacy(label='covered', reaction='undetermined', price=None,
                                         is_holding=True, tier=None)
    assert candidate is False
    assert reasons == ['has_reports', 'undetermined', 'not_traded', 'low_liquidity', 'holding',
                       'weak_similarity']
    assert set(reasons) | {'reacted'} == set(logic.NOT_CANDIDATE_REASONS)
    assert logic.NOT_CANDIDATE_REASONS == ('has_reports', 'reacted', 'undetermined', 'not_traded',
                                           'low_liquidity', 'holding', 'weak_similarity')


# ── theme search: query, query terms, term ranking, RRF (§12.2) ──────────────

@pytest.mark.parametrize('query, normalized', [
    ('레거시 디램', '레거시 DRAM'),
    ('  D램   모듈 ', 'DRAM 모듈'),
    ('디램', 'DRAM'),
    ('2차전지·양극재', '이차전지·양극재'),
    ('노어플래시', 'NOR Flash'),
    ('시디램프', '시디램프'),
])
def test_the_query_is_normalized_with_the_synonym_table(query, normalized):
    assert logic.normalize_query(query, SYN) == normalized


def test_query_terms_are_the_whole_query_and_its_pieces_of_two_characters_or_more():
    assert logic.query_terms('레거시 디램, NAND·Flash D', SYN) == [
        ('레거시디램,nandflashd', '레거시 디램, NAND·Flash D'),
        ('레거시', '레거시'), ('dram', '디램'), ('nand', 'NAND'), ('flash', 'Flash')]


def test_query_terms_are_each_given_once():
    assert logic.query_terms('DRAM 디램', SYN) == [('dramdram', 'DRAM 디램'), ('dram', 'DRAM')]
    assert logic.query_terms('Legacy-DRAM', SYN) == [('legacydram', 'Legacy-DRAM')]
    assert logic.query_terms('2차전지', SYN) == [('이차전지', '2차전지')]


def test_term_matches_rank_by_matched_terms_then_rarity_then_code():
    matched = {'A': ['x'], 'B': ['x', 'y'], 'C': ['y'], 'D': ['x'], 'E': ['new']}
    table = term_table(x=10, y=3)
    # A key missing from the term table counts as held by one company.
    assert logic.term_ranking(matched, table) == ['B', 'E', 'C', 'A', 'D']
    assert logic.term_ranking({}, table) == []


def test_rrf_adds_one_over_sixty_plus_rank_from_each_ranking():
    fused = logic.rrf([['A', 'B'], ['C', 'A'], []])
    assert [code for code, _ in fused] == ['A', 'C', 'B']
    scores = dict(fused)
    assert scores['A'] == pytest.approx(1 / 61 + 1 / 62)
    assert scores['C'] == pytest.approx(1 / 61) and scores['B'] == pytest.approx(1 / 62)
    assert logic.RRF_K == 60


def test_rrf_ties_go_to_the_code():
    assert [code for code, _ in logic.rrf([['B', 'A'], ['A', 'B']])] == ['A', 'B']
