"""features.coverage.logic: the coverage aggregations and the two response payloads.

Ported from langgraph_tagger:
- analytics/tests/test_aggregate.py, every test, unchanged but for the import
- workspace/tests/test_migration.py::test_macro_reuses_inscope_coverage_and_kst_oos_dates
- workspace/tests/test_migration.py::test_stock_activity_includes_industry_reports, the
  aggregation part (the report-list part is in features/reports)

New (spec §9.7): the period start is today in Korea minus ``days``; buckets per unit; the
market payload's empty period and its item list.

New (spec §7): the report counts of companies — stock reports, sector mentions and other
research, never overlapping; the 365-day window; the last stock report date and the brokers; the
labels none / few / covered and the 180-day rule.
"""
import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from research_desk.domain.reports import PUBLISHER_TYPES, REPORT_TYPES
from research_desk.features.coverage.logic import (
    BROKER,
    COUNT_DAYS,
    COVERED_MIN_REPORTS,
    FRESH_DAYS,
    OTHER_RESEARCH_PUBLISHERS,
    SECTOR,
    STOCK_REPORT_TYPES,
    _floor_to_unit,
    count_reports,
    market_payload,
    period_start,
    publisher_dist,
    report_type_timeseries,
    sector_ranking,
    sector_timeseries,
    stock_activity_payload,
    stock_monthly,
)
from research_desk.features.coverage.tests.rows import REPORT_COLUMNS, frame, frame_of, report, wider_frame
from research_desk.features.reports.store import EXPECTED_COLS


def test_the_test_frames_have_the_columns_of_the_reports_window():
    # the frames here stand in for what the reports window returns: the same 16 columns, in order
    assert REPORT_COLUMNS == EXPECTED_COLS
    assert list(frame().columns) == list(wider_frame().columns) == list(REPORT_COLUMNS)


# === sector_timeseries ===

def test_sector_timeseries_groups_by_unit_and_level(inscope_df):
    result = sector_timeseries(inscope_df, level='sectors_major', items=['반도체'], unit='D')
    # 4 inscope rows have '반도체' in sectors_major (rows 0,1,2,3)
    assert result['count'].sum() == 4
    assert '반도체' in result.columns or '반도체' in result['sector'].values


def test_sector_timeseries_overlap_not_contains(inscope_df):
    """multi-select returns rows with ANY of the selected sectors — overlap."""
    result = sector_timeseries(inscope_df,
                                level='sectors_major',
                                items=['반도체', '2차전지'],
                                unit='D')
    # 5 inscope rows have either 반도체 or 2차전지
    assert result['count'].sum() == 5


def test_sector_timeseries_empty_items_uses_top_n(inscope_df):
    """When items is empty, returns top N sectors by volume."""
    result = sector_timeseries(inscope_df, level='sectors_major', items=[], unit='D', top_n=3)
    # All non-empty sectors_major rows (5 rows: 4 반도체 + 1 2차전지)
    assert result['count'].sum() == 5


def test_sector_timeseries_excludes_empty_sectors(inscope_df):
    """Row 4 (산업 type, empty sectors_major) must not appear."""
    result = sector_timeseries(inscope_df, level='sectors_major', items=['반도체'], unit='D')
    assert result['count'].sum() == 4  # not 5 — the 산업 row has empty sectors


# === sector_ranking ===

def test_sector_ranking_counts_unnested_stock_codes(inscope_df):
    """When 반도체 selected, count stock_codes occurrences across matching rows.
    Rows 0,1: ['005930']. Row 2: ['000660']. Row 3: ['005930','000660'].
    Total: 005930 appears 3 times, 000660 appears 2 times.
    """
    result = sector_ranking(inscope_df,
                             level='sectors_major',
                             items=['반도체'],
                             limit=10)
    by_code = dict(zip(result['code'], result['count']))
    assert by_code['005930'] == 3
    assert by_code['000660'] == 2


def test_sector_ranking_empty_items_uses_full_scope(inscope_df):
    """No filter: count over all rows with non-empty stock_codes."""
    result = sector_ranking(inscope_df, level='sectors_major', items=[], limit=10)
    by_code = dict(zip(result['code'], result['count']))
    assert by_code['005930'] == 3
    assert by_code['000660'] == 2
    assert by_code['373220'] == 1


# === report_type_timeseries ===

def test_report_type_timeseries_groups_by_type_and_unit(inscope_df):
    result = report_type_timeseries(inscope_df, unit='D')
    types = result['report_type'].unique().tolist()
    assert '단일종목' in types
    assert '섹터' in types
    assert '산업' in types
    # 단일종목 inscope rows: indices 0,1,2,5 → 4 rows
    type_counts = result.groupby('report_type')['count'].sum()
    assert type_counts['단일종목'] == 4
    assert type_counts['섹터'] == 1
    assert type_counts['산업'] == 1


def test_report_type_timeseries_oos_off_excludes_oos(with_oos_df):
    """Default (no OOS) — IR자료 OOS and foreign OOS not counted."""
    result = report_type_timeseries(with_oos_df, unit='D')
    types = result['report_type'].unique().tolist()
    assert 'IR자료' not in types
    # The foreign-tagged 단일종목 row (out_of_scope_reason='foreign') must also be excluded
    type_counts = result.groupby('report_type')['count'].sum()
    assert type_counts['단일종목'] == 4   # excludes the foreign OOS one


def test_report_type_timeseries_oos_on_includes_all(with_oos_df):
    """include_oos=True — IR자료 + foreign OOS rows counted.

    Regression: OOS rows have published_at=None and only sent_at set.
    The aggregator must derive effective_date from sent_at, otherwise
    these rows silently drop out of the count.
    """
    result = report_type_timeseries(with_oos_df, unit='D', include_oos=True)
    types = result['report_type'].unique().tolist()
    assert 'IR자료' in types
    type_counts = result.groupby('report_type')['count'].sum()
    assert type_counts['IR자료'] == 1
    assert type_counts['단일종목'] == 5   # includes the foreign OOS 단일종목 row


def test_report_type_timeseries_uses_sent_at_when_published_at_null():
    """Explicit regression: a row with only sent_at (no published_at) must
    appear in include_oos=True buckets, using sent_at KST date."""
    df = pd.DataFrame([
        {'published_at': None,
         'sent_at': '2026-05-07T22:00:00+00:00',   # 2026-05-08 KST
         'report_type': 'IR자료', 'publisher': 'X',
         'stock_codes': [], 'sectors_major': [], 'sectors_minor': [],
         'products': [], 'out_of_scope_reason': 'ir_self'},
    ])
    result = report_type_timeseries(df, unit='D', include_oos=True)
    assert len(result) == 1
    # The bucket date is the sent_at KST date (2026-05-08), not UTC (2026-05-07)
    assert result.iloc[0]['bucket'].strftime('%Y-%m-%d') == '2026-05-08'
    assert result.iloc[0]['report_type'] == 'IR자료'


# === stock_monthly ===

def test_stock_monthly_filters_by_code(inscope_df):
    result = stock_monthly(inscope_df, code='005930', unit='D')
    # Rows 0,1,3 have 005930 (in_df rows 0,1,3 — row 3 is the 섹터 multi-stock)
    assert result['count'].sum() == 3


def test_stock_monthly_unknown_code_returns_empty(inscope_df):
    result = stock_monthly(inscope_df, code='999999', unit='D')
    assert result['count'].sum() == 0


# === publisher_dist ===

def test_publisher_dist_counts_by_publisher(inscope_df):
    """For 005930 rows: 메리츠(1), 키움(1), NH(1)."""
    df_stock = inscope_df[inscope_df['stock_codes'].apply(lambda lst: '005930' in lst)]
    result = publisher_dist(df_stock, top_k=10)
    by_pub = dict(zip(result['publisher'], result['count']))
    assert by_pub['메리츠'] == 1
    assert by_pub['키움'] == 1
    assert by_pub['NH'] == 1


def test_publisher_dist_top_k_groups_into_other(inscope_df):
    """If top_k < unique publishers, the rest go to '기타'."""
    result = publisher_dist(inscope_df, top_k=2)
    pubs = result['publisher'].tolist()
    assert '기타' in pubs
    # All rows accounted for
    assert result['count'].sum() == len(inscope_df)


# === the payloads (ported from workspace/tests/test_migration.py) ===

def test_macro_reuses_inscope_coverage_and_kst_oos_dates():
    krx = pd.DataFrame([{'code': '016360', 'name': '삼성증권'}])
    result = market_payload(frame(), krx, 'sectors_major', [], 'D', True)
    assert result['total'] == 3 and result['inscope'] == 2 and result['oos'] == 1
    assert result['coverage'] == [{'bucket': '2026-05-11T00:00:00.000', 'sector': '금융', 'count': 2}]
    assert result['ranking'][0] == {'code': '016360', 'count': 2, 'name': '삼성증권'}
    assert next(r for r in result['types'] if r['report_type'] == '기타')['bucket'].startswith('2026-05-12')
    excluded = market_payload(frame(), krx, 'sectors_major', ['제조'], 'D', False)
    assert excluded['coverage'] == [] and excluded['ranking'] == []
    assert all(r['report_type'] != '기타' for r in excluded['types'])


def test_stock_activity_includes_industry_reports():
    # ported: the aggregation part; the 산업 report of 016360 counts in the company's timeline
    result = stock_activity_payload(frame(), '016360', 'W')
    assert sum(r['count'] for r in result['timeline']) == 2


# === new: the period start, buckets and the market payload's edges ===

def test_period_start_is_today_in_korea_minus_days():
    late_utc = datetime(2026, 5, 11, 16, 0, tzinfo=timezone.utc)   # 2026-05-12 01:00 in Korea
    assert period_start(1, now=late_utc) == '2026-05-11'
    assert period_start(7, now=late_utc) == '2026-05-05'
    assert period_start(36500, now=late_utc) == (date(2026, 5, 12) - timedelta(days=36500)).isoformat()


def test_period_start_uses_the_current_time_in_korea_by_default():
    before = datetime.now(ZoneInfo('Asia/Seoul')).date()
    value = period_start(30)
    after = datetime.now(ZoneInfo('Asia/Seoul')).date()
    assert value in {(before - timedelta(days=30)).isoformat(), (after - timedelta(days=30)).isoformat()}


def test_buckets_are_days_weeks_from_monday_or_months():
    stamps = pd.Series(pd.to_datetime(['2026-05-13', '2026-05-17', '2026-05-18']))

    def floored(unit):
        return list(_floor_to_unit(stamps, unit).dt.strftime('%Y-%m-%d'))

    assert floored('D') == ['2026-05-13', '2026-05-17', '2026-05-18']
    assert floored('W') == ['2026-05-11', '2026-05-11', '2026-05-18']
    assert floored('M') == ['2026-05-01', '2026-05-01', '2026-05-01']
    assert floored('X') == floored('D')   # an unknown unit counts by day


def test_market_payload_keys_in_order():
    names = pd.DataFrame([{'code': '016360', 'name': '삼성증권'}])
    assert list(market_payload(frame(), names, 'sectors_major', [], 'W', False)) == [
        'total', 'inscope', 'oos', 'publishers', 'latest', 'earliest', 'available_items',
        'coverage', 'ranking', 'types']
    assert list(stock_activity_payload(frame(), '016360', 'W')) == ['timeline', 'publishers', 'total']


def test_market_payload_of_an_empty_period():
    empty = pd.DataFrame([], columns=list(REPORT_COLUMNS))
    names = pd.DataFrame([], columns=['code', 'name'])
    assert market_payload(empty, names, 'sectors_major', [], 'W', True) == {
        'total': 0, 'inscope': 0, 'oos': 0, 'publishers': 0, 'latest': None, 'earliest': None,
        'available_items': [], 'coverage': [], 'ranking': [], 'types': []}
    assert stock_activity_payload(empty, '016360', 'W') == {'timeline': [], 'publishers': [], 'total': 0}


def test_market_payload_items_dates_and_names():
    names = pd.DataFrame([{'code': '016360', 'name': '삼성증권'}, {'code': '005930', 'name': '삼성전자'},
                          {'code': '000660', 'name': 'SK하이닉스'}])
    result = market_payload(wider_frame(), names, 'products', [], 'W', True)
    # in-scope values only (the ir_self row has none), sorted, no duplicates
    assert result['available_items'] == ['DRAM', 'LNG선', 'NAND']
    assert (result['earliest'], result['latest']) == ('2026-04-27', '2026-05-15')   # 17: sent_at in Korea
    assert (result['total'], result['inscope'], result['oos'], result['publishers']) == (7, 6, 1, 4)
    # in-scope rows only; a code missing from the stock list keeps an empty name
    assert result['ranking'] == [
        {'code': '005930', 'count': 3, 'name': '삼성전자'},
        {'code': '000660', 'count': 2, 'name': 'SK하이닉스'},
        {'code': '999999', 'count': 1, 'name': ''},
    ]
    assert [list(r) for r in result['ranking']] == [['code', 'count', 'name']] * 3
    selected = market_payload(wider_frame(), names, 'products', ['NAND'], 'W', True)
    assert selected['coverage'] == [{'bucket': '2026-05-04T00:00:00.000', 'sector': 'NAND', 'count': 1},
                                    {'bucket': '2026-05-11T00:00:00.000', 'sector': 'NAND', 'count': 1}]
    assert selected['ranking'] == [{'code': '000660', 'count': 2, 'name': 'SK하이닉스'},
                                   {'code': '005930', 'count': 1, 'name': '삼성전자'}]


@pytest.mark.parametrize('unit', ['D', 'W', 'M'])
def test_stock_activity_counts_the_company_by_unit(unit):
    result = stock_activity_payload(wider_frame(), '005930', unit)
    assert sum(r['count'] for r in result['timeline']) == 3
    assert result['total'] == 7   # every row it was given
    assert sum(r['count'] for r in result['publishers']) == 7


# === report counts of companies (spec §7) ===

TODAY = date(2026, 10, 9)   # in Korea
COUNTS = ('stock_reports', 'sector_mentions', 'other_research')
ENTRY_KEYS = ['stock_reports', 'sector_mentions', 'other_research', 'last_stock_report_date', 'brokers',
              'label']
NO_REPORTS = {'stock_reports': 0, 'sector_mentions': 0, 'other_research': 0,
              'last_stock_report_date': None, 'brokers': 0, 'label': 'none'}


def day(age: int) -> str:
    """The ISO date ``age`` days before TODAY."""
    return (TODAY - timedelta(days=age)).isoformat()


def counts_of(entry: dict) -> dict:
    return {name: entry[name] for name in COUNTS}


def spec_bucket(publisher_type, report_type, alone):
    """The table of spec §7, read row by row."""
    by_broker = publisher_type in ('broker', None, '')
    if by_broker and report_type in ('단일종목', '기타', None, '') and alone:
        return 'stock_reports'
    if by_broker and report_type == '섹터':
        return 'sector_mentions'
    if publisher_type in ('data_provider', 'ir_agency', 'other'):
        return 'other_research'
    return None


@pytest.mark.parametrize('alone', [True, False], ids=['one code', 'two codes'])
@pytest.mark.parametrize('report_type', ['단일종목', '기타', None, '', '섹터', '산업', 'IR자료', '전략·시황'],
                         ids=repr)
@pytest.mark.parametrize('publisher_type', ['broker', None, '', 'data_provider', 'ir_agency', 'other'], ids=repr)
def test_a_row_counts_in_one_of_the_three_at_most(publisher_type, report_type, alone):
    codes = ['005930'] if alone else ['005930', '000660']
    rows = frame_of(report(1, codes, publisher_type=publisher_type, report_type=report_type))
    entry = count_reports(rows, ['005930'], TODAY)['005930']
    expected = spec_bucket(publisher_type, report_type, alone)
    assert counts_of(entry) == {name: int(name == expected) for name in COUNTS}


def test_the_three_counts_over_a_mix_of_rows():
    rows = frame_of(
        report(1, ['005930']),                                                  # stock report
        report(2, ['005930'], publisher_type=None, report_type=None),           # stock report: both empty
        report(3, ['005930'], report_type='기타'),                              # stock report
        report(4, ['005930', '000660']),                                        # 단일종목 with two codes: nowhere
        report(5, ['005930'], report_type='섹터'),                              # 섹터 with one code: a mention
        report(6, ['005930', '000660'], report_type='섹터'),                    # a mention for both codes
        report(7, ['005930'], publisher_type='data_provider'),                 # other research
        report(8, ['005930', '000660'], publisher_type='ir_agency', report_type='IR자료'),   # for both
        report(9, ['005930'], publisher_type='other', report_type='섹터'),      # other research, not a mention
        report(10, ['005930'], report_type='산업'),                             # a broker's 산업 report: nowhere
        report(11, ['035420']),                                                 # another company
    )
    result = count_reports(rows, ['005930', '000660'], TODAY)
    assert counts_of(result['005930']) == {'stock_reports': 3, 'sector_mentions': 2, 'other_research': 3}
    assert counts_of(result['000660']) == {'stock_reports': 0, 'sector_mentions': 1, 'other_research': 1}


@pytest.mark.parametrize('published, sent, counted', [
    (day(365), None, True),                       # exactly 365 days ago
    (day(366), None, False),
    (day(0), None, True),
    # no published_at: sent_at as a date in Korea
    (None, f'{day(366)}T15:00:00Z', True),        # 00:00 in Korea, 365 days ago
    (None, f'{day(366)}T14:59:59Z', False),       # 23:59:59 in Korea, 366 days ago
    (day(366), f'{day(10)}T01:00:00Z', False),    # published_at wins over sent_at
    (None, None, False),                          # no date at all
], ids=['365 days', '366 days', 'today', 'sent 365 days ago in Korea', 'sent 366 days ago in Korea',
        'published wins', 'no date'])
def test_rows_count_from_365_days_ago(published, sent, counted):
    rows = frame_of(report(1, published=published, sent=sent))
    entry = count_reports(rows, ['005930'], TODAY)['005930']
    assert entry['stock_reports'] == int(counted)


def test_the_window_takes_other_day_counts():
    rows = frame_of(report(1, published=day(30)), report(2, published=day(31)))
    assert count_reports(rows, ['005930'], TODAY, days=30)['005930']['stock_reports'] == 1
    assert count_reports(rows, ['005930'], TODAY, days=31)['005930']['stock_reports'] == 2


@pytest.mark.parametrize('ages, label', [
    ((), 'none'),
    ((10,), 'few'),
    ((10, 20), 'few'),
    ((10, 20, 30), 'covered'),
    ((1, 2, 3, 4, 5), 'covered'),
    ((180, 200, 300), 'covered'),   # the last one exactly 180 days ago
    ((181, 200, 300), 'few'),       # the last one 181 days ago
    ((181,), 'few'),
], ids=['no report', 'one', 'two', 'three', 'five', 'last 180 days ago', 'last 181 days ago',
        'one old'])
def test_labels_from_the_stock_reports(ages, label):
    rows = frame_of(*[report(n, published=day(age)) for n, age in enumerate(ages, 1)])
    entry = count_reports(rows, ['005930'], TODAY)['005930']
    assert (entry['stock_reports'], entry['label']) == (len(ages), label)


def test_without_stock_reports_the_label_is_none_whatever_else_there_is():
    rows = frame_of(report(1, report_type='섹터'), report(2, publisher_type='ir_agency'),
                    report(3, ['005930', '000660']))
    entry = count_reports(rows, ['005930'], TODAY)['005930']
    assert counts_of(entry) == {'stock_reports': 0, 'sector_mentions': 1, 'other_research': 1}
    assert (entry['label'], entry['last_stock_report_date'], entry['brokers']) == ('none', None, 0)


def test_the_last_date_and_the_brokers_come_from_the_stock_reports_only():
    rows = frame_of(
        report(1, published='2026-09-01', publisher='KB'),
        report(2, published='2026-09-20', publisher='NH'),
        report(3, published='2026-09-10', publisher='KB'),                       # KB again
        report(4, published='2026-09-15', publisher=None),                       # no publisher name
        report(5, published='2026-09-12', publisher=''),
        report(6, published=None, sent='2026-09-29T15:30:00Z', publisher='삼성'),   # 2026-09-30 in Korea
        # later, but not stock reports
        report(7, published='2026-10-05', publisher='키움', report_type='섹터'),
        report(8, published='2026-10-06', publisher='FnGuide', publisher_type='data_provider'),
        report(9, ['005930', '000660'], published='2026-10-07', publisher='미래에셋'),
    )
    entry = count_reports(rows, ['005930'], TODAY)['005930']
    assert entry['stock_reports'] == 6
    assert entry['last_stock_report_date'] == '2026-09-30'
    assert entry['brokers'] == 3   # KB, NH, 삼성


def test_every_code_asked_is_answered_once_in_the_order_given():
    rows = frame_of(report(1, ['005930'], published=day(3)))
    result = count_reports(rows, ['000660', '005930', '000660', '035420'], TODAY)
    assert list(result) == ['000660', '005930', '035420']
    assert result['000660'] == result['035420'] == NO_REPORTS
    assert result['005930'] == {'stock_reports': 1, 'sector_mentions': 0, 'other_research': 0,
                                'last_stock_report_date': day(3), 'brokers': 1, 'label': 'few'}
    assert all(list(entry) == ENTRY_KEYS for entry in result.values())


def test_codes_are_matched_as_given():
    rows = frame_of(report(1, ['005930']))
    assert count_reports(rows, ['5930'], TODAY) == {'5930': NO_REPORTS}   # no zero-padding


def test_no_rows_or_no_codes():
    assert count_reports(frame_of(), ['005930', '000660'], TODAY) == {'005930': NO_REPORTS,
                                                                      '000660': NO_REPORTS}
    assert count_reports(frame_of(report(1)), [], TODAY) == {}


def test_one_plain_string_of_codes_is_refused():
    with pytest.raises(TypeError):
        count_reports(frame_of(report(1)), '005930', TODAY)


def test_the_rows_given_are_not_changed():
    rows = frame_of(report(1), report(2, published=None, sent='2026-09-29T15:30:00Z'))
    before = rows.copy(deep=True)
    count_reports(rows, ['005930'], TODAY)
    pd.testing.assert_frame_equal(rows, before)   # no effective_date column added in place


def test_the_answer_is_plain_json():
    rows = frame_of(*[report(n, published=day(n)) for n in range(1, 5)])
    result = count_reports(rows, ['005930'], TODAY)
    assert json.loads(json.dumps(result)) == result
    entry = result['005930']
    assert all(type(entry[name]) is int for name in (*COUNTS, 'brokers'))


def test_the_kinds_counted_are_values_of_the_shared_vocabulary():
    assert set(STOCK_REPORT_TYPES) | {SECTOR} <= set(REPORT_TYPES)
    # every publisher type is a broker's or other research: a new one must be placed on purpose
    assert {BROKER, *OTHER_RESEARCH_PUBLISHERS} == set(PUBLISHER_TYPES)
    assert BROKER not in OTHER_RESEARCH_PUBLISHERS


def test_the_adjustable_numbers():
    assert (COUNT_DAYS, FRESH_DAYS, COVERED_MIN_REPORTS) == (365, 180, 3)
