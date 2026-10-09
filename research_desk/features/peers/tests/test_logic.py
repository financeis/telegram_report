"""Peers calculations (no DB, no network, no files): comparison keys and term tables (spec §5.4),
input assembly (§5.1), grounding and caps (§5.2, §5.4), embedding texts (§6.1), seed segment
order (§6.2), percentile tables, interpolation and tiers (§6.3), build status, the six-hour rule
and retention (§6.5)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from research_desk.features.peers import logic
from research_desk.features.peers.logic import AssembledInput, Section, Synonyms
from research_desk.features.peers.schemas import CompanyProfile

SYN = logic.parse_synonyms({'DRAM': ['디램', 'D램'], '이차전지': ['2차전지'], '양극재': ['양극활물질']})


def profile(**fields) -> CompanyProfile:
    base = dict(niche_industry='레거시 DRAM 팹리스', summary='레거시 DRAM을 설계해 판다', roles=['설계(팹리스)'],
                segments=[], products=[], keywords=[], applications=[], customers=[], competitors=[],
                is_holding=False, is_financial=False, info_quality='충분')
    return CompanyProfile(**(base | fields))


def seg(name='메모리', products=(), keywords=(), share=-1.0) -> dict:
    return {'name': name, 'products': list(products), 'keywords': list(keywords),
            'revenue_share_pct': share}


# ── comparison keys (§5.4) ───────────────────────────────────────────────────

@pytest.mark.parametrize('text', ['Legacy DRAM', 'legacy-dram', 'LEGACY  DRAM', 'Legacy·DRAM',
                                  'legacy ‐ dram', 'Legacy ㆍ DRAM', 'Legacy 디램', 'legacy D램'])
def test_the_spec_example_and_its_spellings_share_one_key(text):
    assert logic.comparison_key(text, SYN) == 'legacydram'


def test_synonyms_become_the_standard_spelling_before_case_and_spacing():
    assert logic.comparison_key('2차 전지', SYN) == logic.comparison_key('이차전지', SYN) == '이차전지'
    assert logic.comparison_key('하이니켈 양극활물질', SYN) == '하이니켈양극재'
    assert logic.comparison_key('NOR-Flash', SYN) == 'norflash'
    assert logic.comparison_key('  ', SYN) == ''


def test_without_synonyms_only_case_and_spacing_count():
    empty = logic.parse_synonyms({})
    assert logic.comparison_key('Legacy D램', empty) == 'legacyd램'
    assert empty.display('dram') is None


def test_the_display_of_a_key_is_the_standard_spelling():
    assert SYN.display('dram') == 'DRAM'
    assert SYN.display(SYN.key('2차전지')) == '이차전지'
    assert SYN.display('legacydram') is None


@pytest.mark.parametrize('mapping', [
    {'DRAM': ['디램'], '디램': ['D램']},          # a variant that is another entry's standard
    {'DRAM': ['디램'], 'NAND': ['디램']},         # one variant, two standards
    {'DRAM': '디램'},                             # not a list
    {'DRAM': [''], },                             # an empty spelling
])
def test_an_ambiguous_or_malformed_synonym_table_is_refused(mapping):
    with pytest.raises(ValueError):
        logic.parse_synonyms(mapping)


def test_term_keys_are_unique_non_empty_and_in_order():
    assert logic.term_keys(['D램', 'HBM', 'dram', ' ', 'Legacy-DRAM'], SYN) == ['dram', 'hbm', 'legacydram']


def test_profile_terms_are_keywords_products_then_segment_keywords_and_products():
    p = profile(keywords=['Legacy DRAM'], products=['NOR Flash', 'D램'],
                segments=[seg(products=['MCP'], keywords=['저전력 디램', 'nor flash'])]).model_dump()
    assert logic.profile_terms(p, SYN) == ['legacydram', 'norflash', 'dram', '저전력dram', 'mcp']


def test_the_term_table_counts_companies_and_shows_the_standard_or_first_spelling():
    first = profile(keywords=['Legacy DRAM', 'D램'], products=['legacy-dram']).model_dump()
    second = profile(keywords=['legacy dram', '디램', 'MCP']).model_dump()
    table = logic.term_table([first, second], SYN)
    assert table['legacydram'] == {'display': 'Legacy DRAM', 'companies': 2}   # first seen
    assert table['dram'] == {'display': 'DRAM', 'companies': 2}                # standard spelling
    assert table['mcp'] == {'display': 'MCP', 'companies': 1}
    assert set(table) == {'legacydram', 'dram', 'mcp'}


# ── input assembly (§5.1) ────────────────────────────────────────────────────

TABLE_KEEP = '사업부문 | 품목 | 구체적용도\n메모리 | DRAM | PC\n메모리 | NOR Flash | 차량'


def test_blocks_are_labelled_and_joined_in_the_spec_order():
    out = logic.assemble_input({
        '020100': Section(prose='짧은 개요'),
        '020200': Section(prose='제품 설명', tables=TABLE_KEEP),
        '020400': Section(prose='매출 설명'),
        '020700': Section(prose='기타 설명'),
    })
    assert out.text == ('[사업의 개요]\n짧은 개요\n\n[주요 제품 및 서비스]\n제품 설명\n\n'
                        f'[주요 제품 표]\n{TABLE_KEEP}\n\n[기타 참고사항]\n기타 설명\n\n[매출 및 수주]\n매출 설명')
    assert out.sections == ('020100', '020200', '020700', '020400')
    assert out.truncated is False


def test_each_block_is_cut_at_its_cap_and_marks_the_input_truncated():
    out = logic.assemble_input({
        '020100': Section(prose='가' * 6001),
        '020200': Section(prose='나' * 2600, tables=TABLE_KEEP + '\n' + '\n'.join(['메모리 | 다 | 라'] * 400)),
    })
    blocks = out.text.split('\n\n')
    assert blocks[0] == '[사업의 개요]\n' + '가' * 6000
    assert blocks[1] == '[주요 제품 및 서비스]\n' + '나' * 2500
    assert len(blocks[2]) == len('[주요 제품 표]\n') + 2500
    assert out.truncated is True


def test_a_block_exactly_at_its_cap_is_not_truncated():
    out = logic.assemble_input({'020100': Section(prose='가' * 6000)})
    assert out.text == '[사업의 개요]\n' + '가' * 6000
    assert out.truncated is False


@pytest.mark.parametrize('table, kept', [
    (TABLE_KEEP, True),
    ('제품 | 매출\n가 제품 | 매출 큼', True),
    ('주요 상표 | 비고\n가나 | 없음', True),
    ('용도 | 내용\n차량 | 센서', True),
    ('제품 | DRAM', False),                                         # one row
    ('품목 | 단가\nDRAM | 비쌈', False),                              # unit price
    ('품목 | 가격(천/톤)\n철근 | 비쌈', False),
    ('제품 | 가격(원/개)\n센서 | 비쌈', False),
    ('제품 | 가격($/kg)\n소재 | 비쌈', False),
    ('구분 | 내용\nDRAM | PC용', False),                              # no product word in the first row
    ('제품 | 2025 | 2024 | 2023\nDRAM | 1,234 | 1,111 | 900\nNAND | (12) | 3.5% | △7', False),  # 9/12
    ('제품 | 비중 | 비고\nDRAM | 70% | 주력\nNAND | 30% | -', True),    # 2/8 ("-" is empty)
])
def test_only_product_and_segment_tables_are_kept(table, kept):
    assert logic.is_product_table(table) is kept


def test_numeric_cells_exactly_at_seventy_percent_are_kept():
    # 10 non-empty cells, 7 numeric: not more than 70%.
    table = '제품 | 1 | 2 | 3 | 4\n가 | 5 | 6 | 7 | 나'
    assert logic.is_product_table(table) is True


def test_product_tables_are_filtered_then_joined_up_to_their_cap():
    tables = '\n\n'.join([TABLE_KEEP, '제품 | DRAM', '구분 | 내용\nA | B', '용도 | 내용\n차량 | 센서'])
    out = logic.assemble_input({'020100': Section(prose='개' * 900), '020200': Section(tables=tables)})
    assert out.text == '[사업의 개요]\n' + '개' * 900 + f'\n\n[주요 제품 표]\n{TABLE_KEEP}\n\n용도 | 내용\n차량 | 센서'
    assert out.sections == ('020100', '020200')


def test_a_short_overview_brings_in_other_notes_and_sales():
    sections = {'020100': Section(prose='가' * 799), '020400': Section(prose='매' * 1600),
                '020700': Section(prose='기' * 3100)}
    out = logic.assemble_input(sections)
    assert out.text == ('[사업의 개요]\n' + '가' * 799 + '\n\n[기타 참고사항]\n' + '기' * 3000
                        + '\n\n[매출 및 수주]\n' + '매' * 1500)
    assert out.truncated is True
    sections['020100'] = Section(prose='가' * 800)
    out = logic.assemble_input(sections)
    assert out.text == '[사업의 개요]\n' + '가' * 800
    assert out.sections == ('020100',)


def test_a_missing_overview_counts_as_short():
    out = logic.assemble_input({'020200': Section(prose='제품'), '020700': Section(prose='기타')})
    assert out.text == '[주요 제품 및 서비스]\n제품\n\n[기타 참고사항]\n기타'


def test_the_financial_format_replaces_the_product_blocks_with_its_business_section():
    out = logic.assemble_input({
        '020100': Section(prose='개' * 900),
        '020200': Section(prose='쓰지 않음', tables=TABLE_KEEP),
        '020800': Section(prose='영업 현황', tables='구분 | 잔액\n대출 | 100'),
    })
    assert out.text == '[사업의 개요]\n' + '개' * 900 + '\n\n[영업의 현황]\n영업 현황\n\n구분 | 잔액\n대출 | 100'
    assert out.sections == ('020100', '020800')


def test_the_financial_block_is_capped_at_six_thousand_and_the_whole_input_at_twelve():
    out = logic.assemble_input({'020100': Section(prose='개' * 6000),
                                '020800': Section(prose='영' * 5000, tables='표' * 5000)})
    assert len(out.text) == 12000
    assert out.text.startswith('[사업의 개요]\n' + '개' * 6000 + '\n\n[영업의 현황]\n' + '영' * 5000 + '\n\n표')
    assert out.truncated is True


def test_empty_sections_give_an_empty_input():
    assert logic.assemble_input({}) == AssembledInput(text='', sections=(), truncated=False)
    assert logic.assemble_input({'020100': Section(prose='  ')}).text == ''


# ── grounding (§5.4) and caps (§5.2) ─────────────────────────────────────────

TEXT = ('[사업의 개요]\n당사는 Legacy DRAM과 NOR Flash를 설계합니다. 주요 제품은 디램 모듈이며 '
        '삼성전자에 공급합니다. 차량용 MCP도 개발 중입니다.')


def test_terms_missing_from_the_input_are_dropped_and_counted():
    p = profile(keywords=['legacy-dram', 'NOR flash', 'HBM'], products=['D램 모듈', '   '],
                customers=['삼성전자', 'SK하이닉스'], competitors=['마이크론'],
                applications=['서버'],        # not checked
                segments=[seg(products=['NOR Flash'], keywords=['MCP', '하이니켈 양극재'])])
    grounded, kept, produced = logic.ground_profile(p, TEXT, SYN)
    assert grounded.keywords == ['legacy-dram', 'NOR flash']
    assert grounded.products == ['D램 모듈']
    assert grounded.customers == ['삼성전자']
    assert grounded.competitors == []
    assert grounded.applications == ['서버']
    assert grounded.segments[0].products == ['NOR Flash']
    assert grounded.segments[0].keywords == ['MCP']
    assert (kept, produced) == (6, 10)


def test_the_grounding_ratio_is_kept_over_produced_and_one_without_terms():
    assert logic.grounding_ratio(4, 5) == 0.8
    assert logic.grounding_ratio(0, 0) == 1.0
    _, kept, produced = logic.ground_profile(profile(), TEXT, SYN)
    assert (kept, produced) == (0, 0)


def test_counts_are_capped_after_grounding_and_duplicates_dropped():
    many = [f'제품{i}' for i in range(20)]
    p = profile(niche_industry='가' * 50, summary='나' * 200, roles=['소재', '부품', '장비'],
                products=many, keywords=many + ['제품0', '제 품 0'], applications=many,
                customers=many, competitors=many,
                segments=[seg(name=f'부문{i}', products=many, keywords=many) for i in range(8)])
    capped = logic.cap_profile(p, SYN)
    assert len(capped.niche_industry) == 40 and len(capped.summary) == 160
    assert capped.roles == ['소재', '부품']
    assert capped.products == many[:12]
    assert capped.keywords == many[:15]
    assert (len(capped.applications), len(capped.customers), len(capped.competitors)) == (8, 8, 8)
    assert [s.name for s in capped.segments] == [f'부문{i}' for i in range(6)]
    assert all(len(s.products) == 6 and len(s.keywords) == 6 for s in capped.segments)


def test_caps_drop_nameless_segments_and_impossible_shares():
    p = profile(segments=[seg(name=' ', share=50), seg(name='A', share=120.0),
                          seg(name='B', share=-3), seg(name='C', share=0.0), seg(name='D', share=-1)])
    capped = logic.cap_profile(p, SYN)
    assert [(s.name, s.revenue_share_pct) for s in capped.segments] == [
        ('A', -1.0), ('B', -1.0), ('C', 0.0), ('D', -1.0)]


# ── embedding texts (§6.1) ───────────────────────────────────────────────────

def test_the_company_text_follows_the_template_without_customers_or_competitors():
    p = profile(products=['NOR Flash', 'MCP'], keywords=['Legacy DRAM'], applications=['차량', 'IoT'],
                customers=['삼성전자'], competitors=['윈본드'],
                segments=[seg('메모리', ['DRAM', 'NOR'], share=98.1), seg('기타', ['용역'], share=-1),
                          seg('상품', ['모듈'], share=50.0)]).model_dump()
    text = logic.company_text(p)
    assert text == ('업종: 레거시 DRAM 팹리스\n요약: 레거시 DRAM을 설계해 판다\n제품: NOR Flash, MCP\n'
                    '키워드: Legacy DRAM\n사업부문: 메모리(98.1%): DRAM, NOR; 기타: 용역; 상품(50%): 모듈\n'
                    '적용처: 차량, IoT')
    assert '삼성전자' not in text and '윈본드' not in text


def test_the_segment_text_names_the_niche_the_segment_its_products_and_keywords():
    assert logic.segment_text('레거시 DRAM 팹리스', seg('메모리', ['DRAM', 'NOR'], ['저전력'])) == (
        '레거시 DRAM 팹리스 — 메모리: DRAM, NOR / 저전력')


def test_vectors_are_scaled_to_length_one():
    v = logic.normalize([3.0, 4.0])
    assert v == pytest.approx([0.6, 0.8])
    with pytest.raises(ValueError):
        logic.normalize([0.0, 0.0])


# ── seed segment order (§6.2) ────────────────────────────────────────────────

def test_segments_go_by_share_unknown_last_ties_in_original_order():
    assert logic.segment_order([10.0, -1.0, 60.0, 10.0, -1.0, 0.0]) == [2, 0, 3, 5, 1, 4]
    assert logic.segment_order([]) == []


# ── percentile tables, interpolation, tiers (§6.3) ───────────────────────────

def test_the_percentile_grid_is_95_to_99_9_by_tenths_then_99_95_99_99_100():
    grid = logic.PERCENTILES
    assert grid[:3] == (95.0, 95.1, 95.2) and grid[49] == 99.9
    assert grid[-3:] == (99.95, 99.99, 100.0)
    assert len(grid) == 53


def random_unit(n, dims=8, seed=1):
    v = np.random.default_rng(seed).normal(size=(n, dims))
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def test_the_company_table_uses_every_pair_but_self():
    vectors = random_unit(40)
    table = logic.quantile_table(vectors)
    sims = vectors @ vectors.T
    pairs = sims[np.triu_indices(40, k=1)]
    assert [p for p, _ in table] == list(logic.PERCENTILES)
    assert [c for _, c in table] == pytest.approx(list(np.percentile(pairs, logic.PERCENTILES)))
    assert table[-1][1] == pytest.approx(pairs.max())


def test_the_segment_table_leaves_out_pairs_of_one_company():
    vectors = random_unit(6)
    groups = ['A', 'A', 'B', 'B', 'C', 'C']
    table = logic.quantile_table(vectors, groups)
    sims = vectors @ vectors.T
    pairs = [sims[i, j] for i in range(6) for j in range(i + 1, 6) if groups[i] != groups[j]]
    assert len(pairs) == 12
    assert [c for _, c in table] == pytest.approx(list(np.percentile(pairs, logic.PERCENTILES)))


def test_over_the_pair_limit_a_random_sample_of_that_size_is_used(monkeypatch):
    vectors = random_unit(100)
    seen = {}
    real = logic._percentile_values

    def spy(sims):
        seen['n'] = len(sims)
        return real(sims)

    monkeypatch.setattr(logic, '_percentile_values', spy)
    table = logic.quantile_table(vectors, max_pairs=1000, seed=7)
    assert seen['n'] == 1000
    assert table == logic.quantile_table(vectors, max_pairs=1000, seed=7)   # same seed, same table
    exact = logic.quantile_table(vectors)
    assert seen['n'] == 100 * 99 // 2
    assert abs(table[0][1] - exact[0][1]) < 0.1


def test_no_pairs_no_table():
    assert logic.quantile_table(random_unit(1)) is None
    assert logic.quantile_table(random_unit(2), ['A', 'A']) is None


TABLE = [[95.0, 0.50], [98.0, 0.60], [99.5, 0.70], [99.9, 0.80], [100.0, 0.90]]


@pytest.mark.parametrize('similarity, expected', [
    (0.4999, None),          # below the 95th value: not listed
    (0.50, 95.0),
    (0.55, 96.5),            # halfway between 95 and 98
    (0.60, 98.0),
    (0.75, 99.7),
    (0.90, 100.0),
    (0.95, 100.0),           # above the 100th value
])
def test_the_percentile_is_interpolated_between_the_two_neighbouring_points(similarity, expected):
    if expected is None:
        assert logic.percentile_of(similarity, TABLE) is None
    else:
        assert logic.percentile_of(similarity, TABLE) == pytest.approx(expected)


def test_equal_cosines_in_the_table_give_the_highest_percentile():
    flat = [[95.0, 0.5], [96.0, 0.5], [97.0, 0.6], [100.0, 0.6]]
    assert logic.percentile_of(0.5, flat) == 96.0
    assert logic.percentile_of(0.6, flat) == 100.0
    assert logic.percentile_of(0.5, None) is None


@pytest.mark.parametrize('percentile, tier', [
    (None, None), (94.99, None), (95.0, 'related'), (97.99, 'related'), (98.0, 'high'),
    (99.49, 'high'), (99.5, 'very_high'), (100.0, 'very_high'),
])
def test_tiers_and_their_boundaries(percentile, tier):
    assert logic.tier_of(percentile) == tier


# ── build status, the six-hour rule, retention (§6.5) ────────────────────────

@pytest.mark.parametrize('eligible, profiled, status', [
    (20, 19, 'done'), (20, 18, 'incomplete'), (100, 95, 'done'), (100, 94, 'incomplete'),
    (1, 1, 'done'), (0, 0, 'incomplete'),
])
def test_a_build_is_done_at_95_percent_of_its_targets(eligible, profiled, status):
    assert logic.build_status(eligible, profiled, pilot=False) == status


def test_a_pilot_is_always_a_pilot():
    assert logic.build_status(20, 1, pilot=True) == 'pilot'


def test_a_running_build_is_stale_only_after_six_hours():
    now = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
    assert logic.is_stale(now - timedelta(hours=6), now) is False
    assert logic.is_stale(now - timedelta(hours=6, seconds=1), now) is True
    assert logic.is_stale(now, now) is False


def build(build_id, status, fy=2025, pv='p1', model='m1', finished=None):
    return {'build_id': build_id, 'status': status, 'fiscal_year': fy, 'profile_version': pv,
            'embed_model': model, 'finished_at': finished}


def test_retention_keeps_the_three_latest_done_builds_and_what_the_rest_still_use():
    builds = [build(1, 'done', pv='p0', finished='2026-01-01T00:00:00+00:00'),
              build(2, 'done', model='m0', finished='2026-02-01T00:00:00+00:00'),
              build(3, 'pilot', pv='p2', finished='2026-02-15T00:00:00+00:00'),
              build(4, 'done', finished='2026-03-01T00:00:00+00:00'),
              build(5, 'done', finished='2026-04-01T00:00:00+00:00'),
              build(6, 'done', finished='2026-05-01T00:00:00+00:00')]
    profile_keys = {(2025, 'p0'), (2025, 'p1'), (2025, 'p2'), (2024, 'p1'), (2024, 'p9'), (2025, 'p-old')}
    embedding_keys = {(2025, 'p0', 'm1'), (2025, 'p1', 'm0'), (2025, 'p1', 'm1'), (2025, 'p2', 'm1'),
                      (2024, 'p9', 'm1'), (2025, 'p-old', 'm1')}
    plan = logic.retention_plan(builds, profile_keys, embedding_keys, current_profile_version='p9')
    assert plan.delete_build_ids == (1, 2)
    # p0 was used only by build 1; p-old by nothing; p9 is the current profile version.
    assert set(plan.delete_profile_keys) == {(2025, 'p0'), (2024, 'p1'), (2025, 'p-old')}
    # m0 was used only by build 2; the rows under deleted profiles go with them anyway.
    assert set(plan.delete_embedding_keys) == {(2025, 'p0', 'm1'), (2025, 'p1', 'm0'),
                                               (2025, 'p-old', 'm1')}


def test_retention_deletes_nothing_with_three_done_builds_or_fewer():
    builds = [build(1, 'done', finished='2026-01-01T00:00:00+00:00'), build(2, 'failed'),
              build(3, 'done', finished='2026-02-01T00:00:00+00:00')]
    plan = logic.retention_plan(builds, {(2025, 'p1')}, {(2025, 'p1', 'm1')}, current_profile_version='p1')
    assert plan.delete_build_ids == () and plan.delete_profile_keys == () and plan.delete_embedding_keys == ()


# ── pilot neighbours ─────────────────────────────────────────────────────────

def test_top_companies_leave_out_the_seed_and_sort_by_similarity():
    codes = ['A', 'B', 'C', 'D']
    matrix = np.array([[1, 0], [0.8, 0.6], [0, 1], [0.6, 0.8]], dtype=float)
    assert logic.top_companies('A', matrix[0], codes, matrix, k=2) == [('B', pytest.approx(0.8)),
                                                                         ('D', pytest.approx(0.6))]


def test_top_segments_keep_the_best_segment_of_each_other_company():
    codes = ['A', 'B', 'B', 'C']
    seg_nos = [0, 0, 1, 0]
    matrix = np.array([[1, 0], [0.6, 0.8], [0.8, 0.6], [0, 1]], dtype=float)
    assert logic.top_segments('A', matrix[0], codes, seg_nos, matrix, k=10) == [
        ('B', 1, pytest.approx(0.8)), ('C', 0, pytest.approx(0.0))]
