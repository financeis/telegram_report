"""features.reports store: reads of tagged reports (spec §4).

Ported from langgraph_tagger/analytics/tests/test_db.py (AnalyticsDB → ReportStore, same query
chains): paging by 1000 until a short page, the in-scope filter, the server-side period filter,
the include-OOS read that skips both server filters and keeps rows by effective date on the
client, and the 15 expected columns even for an empty result.

New: the 15 columns and the page size pinned, the stock-rows query (not tested before), the
single-row lookup that moved here from workspace/service.py, and checks on an in-memory
PostgREST stand-in that every read's in-scope filter is the shared rule in domain.reports.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from research_desk.domain.reports import IN_SCOPE_STATUSES, TAGGING_STATUSES, is_in_scope
from research_desk.features.reports.store import EXPECTED_COLS, PAGE, SELECT_COLS, ReportStore
from research_desk.features.reports.tests.fakes import FakeSupabase

# The column list of langgraph_tagger/analytics/db.py, in its order.
OLD_COLUMNS = (
    'id', 'published_at', 'sent_at', 'report_type', 'publisher',
    'stock_codes', 'company_names', 'sectors_major', 'sectors_minor',
    'products', 'tagging_status', 'out_of_scope_reason', 'file_path',
    'file_name', 'title',
)


def _make_supabase_with_pages(pages: list[list[dict]]) -> MagicMock:
    """Build a supabase-py client mock whose .range().execute() returns
    pages[i] for the i-th call. Subsequent calls return empty.

    Wires the shared range_mock to BOTH terminal chains so the helper
    serves the in-scope path (select.in_.is_.gte.range) and the
    OOS-on path (select.in_.range, which skips .is_/.gte).
    """
    sb = MagicMock()
    # Build the chainable mock
    table = sb.table.return_value
    select = table.select.return_value
    in_ret = select.in_.return_value
    chain = in_ret.is_.return_value.gte.return_value
    # The .range(...).execute() must yield pages in sequence
    range_mock = MagicMock()
    chain.range.return_value = range_mock
    # OOS-on path: select.in_().range(...) bypasses .is_/.gte and lands here.
    in_ret.range.return_value = range_mock

    page_iter = iter(pages + [[]])  # trailing empty for loop termination

    def _exec():
        return MagicMock(data=next(page_iter, []))
    range_mock.execute.side_effect = _exec
    return sb


def tagged(rid, *, status='auto', reason=None, published='2026-05-11',
           sent='2026-05-11T01:00:00+00:00', codes=('016360',), **extra):
    """A reports row as the tagger leaves it (OOS rows have published_at NULL)."""
    return {'id': rid, 'published_at': published, 'sent_at': sent, 'report_type': '단일종목',
            'publisher': 'KB', 'stock_codes': list(codes), 'company_names': ['삼성증권'],
            'sectors_major': ['금융'], 'sectors_minor': ['증권'], 'products': ['증권'],
            'tagging_status': status, 'out_of_scope_reason': reason,
            'file_path': f'{rid}.pdf', 'file_name': f'{rid}.pdf', 'title': f'report {rid}', **extra}


def ids(df: pd.DataFrame) -> list[int]:
    return [int(i) for i in df['id']]


# ── ported from analytics/tests/test_db.py ───────────────────────────────────

def test_fetch_inscope_rows_paginates_until_short_page():
    sb = _make_supabase_with_pages([
        [{'id': i} for i in range(1000)],
        [{'id': 1000}],   # short page → stops
    ])
    db = ReportStore(sb)
    df = db.fetch_inscope_rows(period_start_iso='2026-01-01')
    assert len(df) == 1001


def test_fetch_inscope_rows_applies_in_scope_filter():
    sb = _make_supabase_with_pages([[]])
    db = ReportStore(sb)
    _ = db.fetch_inscope_rows(period_start_iso='2026-01-01')
    # Confirm .in_('tagging_status', ['auto', 'verified']) and is_('out_of_scope_reason', 'null')
    table_select = sb.table.return_value.select.return_value
    table_select.in_.assert_called_with('tagging_status', ['auto', 'verified'])
    table_select.in_.return_value.is_.assert_called_with('out_of_scope_reason', 'null')


def test_fetch_inscope_rows_applies_period_filter():
    sb = _make_supabase_with_pages([[]])
    db = ReportStore(sb)
    _ = db.fetch_inscope_rows(period_start_iso='2026-01-15')
    chain = (sb.table.return_value.select.return_value
                       .in_.return_value
                       .is_.return_value)
    chain.gte.assert_called_with('published_at', '2026-01-15')


def test_fetch_inscope_or_oos_rows_off_applies_isnull_and_period():
    sb = _make_supabase_with_pages([[]])
    db = ReportStore(sb)
    _ = db.fetch_inscope_or_oos_rows(period_start_iso='2026-01-01', include_oos=False)
    select = sb.table.return_value.select.return_value
    select.in_.assert_called_with('tagging_status', ['auto', 'verified'])
    select.in_.return_value.is_.assert_called_with('out_of_scope_reason', 'null')
    select.in_.return_value.is_.return_value.gte.assert_called_with(
        'published_at', '2026-01-01'
    )


def test_fetch_inscope_or_oos_rows_on_skips_isnull_and_period_server_side():
    """OOS-on: server-side filter is tagging_status only.
    Period filter is client-side via effective_date."""
    sb = MagicMock()
    select = sb.table.return_value.select.return_value
    chain = select.in_.return_value.range.return_value
    chain.execute.return_value = MagicMock(data=[])
    db = ReportStore(sb)
    _ = db.fetch_inscope_or_oos_rows(period_start_iso='2026-01-01', include_oos=True)
    # is_('out_of_scope_reason', 'null') must NOT have been chained
    select.in_.return_value.is_.assert_not_called()
    # gte('published_at', ...) must NOT have been chained at the server side
    select.in_.return_value.gte.assert_not_called()


def test_fetch_inscope_or_oos_rows_on_applies_client_side_period_filter():
    """OOS-on: row with published_at=NULL and sent_at < period_start must
    be filtered out client-side via effective_date."""
    sb = _make_supabase_with_pages([[
        # Falls inside period (sent_at KST 2026-05-08)
        {'id': 1, 'published_at': None, 'sent_at': '2026-05-07T22:00:00+00:00',
         'report_type': 'IR자료', 'publisher': 'X',
         'stock_codes': ['005930'], 'company_names': ['삼성전자'],
         'sectors_major': [], 'sectors_minor': [], 'products': [],
         'tagging_status': 'verified', 'out_of_scope_reason': 'ir_self',
         'file_path': '1.pdf', 'file_name': '1.pdf', 'title': ''},
        # Falls outside period (sent_at KST 2026-04-30)
        {'id': 2, 'published_at': None, 'sent_at': '2026-04-29T22:00:00+00:00',
         'report_type': 'IR자료', 'publisher': 'Y',
         'stock_codes': [], 'company_names': [],
         'sectors_major': [], 'sectors_minor': [], 'products': [],
         'tagging_status': 'verified', 'out_of_scope_reason': 'ir_self',
         'file_path': '2.pdf', 'file_name': '2.pdf', 'title': ''},
    ]])
    db = ReportStore(sb)
    df = db.fetch_inscope_or_oos_rows(period_start_iso='2026-05-01', include_oos=True)
    assert len(df) == 1
    assert df.iloc[0]['id'] == 1


def test_empty_result_returns_dataframe_with_expected_columns():
    """Regression: empty fetches must still expose EXPECTED_COLS so the
    downstream aggregators don't KeyError when indexing by column."""
    sb = _make_supabase_with_pages([[]])
    db = ReportStore(sb)
    df = db.fetch_inscope_rows(period_start_iso='2026-01-01')
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 0
    for col in ('id', 'published_at', 'report_type', 'publisher',
                'stock_codes', 'sectors_major', 'sectors_minor', 'products',
                'sent_at', 'out_of_scope_reason'):
        assert col in df.columns


def test_fetch_returns_dataframe_with_expected_columns():
    sb = _make_supabase_with_pages([[
        {'id': 1, 'published_at': '2026-05-01', 'report_type': '단일종목',
         'publisher': 'NH', 'stock_codes': ['005930'], 'company_names': ['삼성전자'],
         'sectors_major': ['반도체'], 'sectors_minor': ['메모리반도체'],
         'products': ['DRAM'], 'tagging_status': 'auto',
         'out_of_scope_reason': None, 'file_path': '1.pdf', 'file_name': '1.pdf',
         'title': 't', 'sent_at': '2026-05-01T08:00:00+00:00'},
    ]])
    db = ReportStore(sb)
    df = db.fetch_inscope_rows(period_start_iso='2026-01-01')
    assert isinstance(df, pd.DataFrame)
    for col in ('id', 'published_at', 'report_type', 'publisher', 'stock_codes',
                'sectors_major', 'sectors_minor', 'products'):
        assert col in df.columns


# ── columns, table, paging ───────────────────────────────────────────────────

def test_the_fifteen_columns_and_the_page_size_are_unchanged():
    assert EXPECTED_COLS == OLD_COLUMNS
    assert SELECT_COLS == ', '.join(OLD_COLUMNS)
    assert PAGE == 1000


READS = {
    'in-scope': lambda store: store.fetch_inscope_rows('2026-01-01'),
    'period without OOS': lambda store: store.fetch_inscope_or_oos_rows('2026-01-01', False),
    'period with OOS': lambda store: store.fetch_inscope_or_oos_rows('2026-01-01', True),
    'stock': lambda store: store.fetch_stock_rows('016360', '2026-01-01'),
    'one row': lambda store: store.fetch_report_row(1),
}


@pytest.mark.parametrize('read', list(READS.values()), ids=list(READS))
def test_every_read_selects_the_fifteen_columns_from_reports(read):
    db = FakeSupabase(reports=[tagged(1, caption='internal', file_hash_sha256='ab' * 32)])
    result = read(ReportStore(db))
    assert [q.table for q in db.executed] == ['reports']
    assert db.executed[0].columns == SELECT_COLS
    rows = [result] if isinstance(result, dict) else result.to_dict('records')
    assert rows and all(set(r) == set(EXPECTED_COLS) for r in rows)   # no caption, no hash


@pytest.mark.parametrize('count, windows', [
    (2500, [(0, 999), (1000, 1999), (2000, 2999)]),
    (2000, [(0, 999), (1000, 1999), (2000, 2999)]),   # a full last page needs one more read
    (999, [(0, 999)]),
    (0, [(0, 999)]),
])
def test_paging_reads_consecutive_windows_of_1000(count, windows):
    db = FakeSupabase(reports=[tagged(i) for i in range(1, count + 1)])
    df = ReportStore(db).fetch_inscope_rows('2026-01-01')
    assert [q.window for q in db.executed] == windows
    assert ids(df) == list(range(1, count + 1))


def test_the_single_row_lookup_reads_once_without_paging():
    db = FakeSupabase(reports=[tagged(1)])
    ReportStore(db).fetch_report_row(1)
    (query,) = db.executed
    assert query.window is None


# ── the in-scope rule is domain.reports' ─────────────────────────────────────

def every_status_and_reason() -> list[dict]:
    rows = []
    for status in TAGGING_STATUSES:
        for reason in (None, 'foreign'):
            rows.append(tagged(len(rows) + 1, status=status, reason=reason))
    return rows


IN_SCOPE_READS = {
    'in-scope': lambda store: store.fetch_inscope_rows('2026-01-01'),
    'period without OOS': lambda store: store.fetch_inscope_or_oos_rows('2026-01-01', False),
    'stock': lambda store: store.fetch_stock_rows('016360', '2026-01-01'),
}


@pytest.mark.parametrize('read', list(IN_SCOPE_READS.values()), ids=list(IN_SCOPE_READS))
def test_in_scope_reads_follow_the_shared_rule(read):
    rows = every_status_and_reason()
    db = FakeSupabase(reports=rows)
    assert ids(read(ReportStore(db))) == [r['id'] for r in rows if is_in_scope(r)]
    (query,) = db.executed
    assert query.filter_values('in', 'tagging_status') == [list(IN_SCOPE_STATUSES)]
    assert query.filter_values('is', 'out_of_scope_reason') == [None]


def test_every_single_row_follows_the_shared_rule():
    rows = every_status_and_reason()
    store = ReportStore(FakeSupabase(reports=rows))
    for row in rows:
        found = store.fetch_report_row(row['id'])
        assert (found is not None) == is_in_scope(row), row
        if found is not None:
            assert found['id'] == row['id']


def test_the_period_read_with_oos_keeps_final_statuses_and_every_reason():
    rows = every_status_and_reason()
    db = FakeSupabase(reports=rows)
    df = ReportStore(db).fetch_inscope_or_oos_rows('2026-01-01', include_oos=True)
    assert ids(df) == [r['id'] for r in rows if r['tagging_status'] in IN_SCOPE_STATUSES]
    (query,) = db.executed
    assert query.filters == [('in', 'tagging_status', list(IN_SCOPE_STATUSES))]


# ── period filters ───────────────────────────────────────────────────────────

def test_the_server_side_period_filter_is_published_at_from_the_start_day():
    rows = [tagged(1, published='2025-12-31'), tagged(2, published='2026-01-01'),
            tagged(3, published='2026-05-11'), tagged(4, published=None)]
    db = FakeSupabase(reports=rows)
    assert ids(ReportStore(db).fetch_inscope_rows('2026-01-01')) == [2, 3]
    assert db.executed[0].filter_values('gte', 'published_at') == ['2026-01-01']


def test_with_oos_the_day_is_published_at_else_the_kst_day_of_sent_at():
    rows = [
        # no published_at: sent_at 15:00 UTC is 00:00 KST on the start day → kept
        tagged(1, status='verified', reason='ir_self', published=None, sent='2026-04-30T15:00:00+00:00'),
        # one second earlier is still the day before in KST → dropped
        tagged(2, status='verified', reason='ir_self', published=None, sent='2026-04-30T14:59:59+00:00'),
        # published_at wins over sent_at, both ways
        tagged(3, published='2026-05-01', sent='2026-04-20T00:00:00+00:00'),
        tagged(4, published='2026-04-30', sent='2026-05-05T00:00:00+00:00'),
        # no date at all → dropped
        tagged(5, status='verified', reason='foreign', published=None, sent=None),
        # not a final status → never read
        tagged(6, status='pending', published=None, sent='2026-05-05T00:00:00+00:00'),
        tagged(7, status='verified', reason='foreign', published=None, sent='2026-05-02T00:00:00+00:00'),
    ]
    df = ReportStore(FakeSupabase(reports=rows)).fetch_inscope_or_oos_rows('2026-05-01', include_oos=True)
    assert ids(df) == [1, 3, 7]
    assert list(df.index) == [0, 1, 2]   # index reset after the client-side filter
    assert list(df.columns) == list(EXPECTED_COLS)


# ── stock rows ───────────────────────────────────────────────────────────────

def test_fetch_stock_rows_chain():
    sb = MagicMock()
    in_ret = sb.table.return_value.select.return_value.in_.return_value
    contains = in_ret.is_.return_value.contains
    contains.return_value.gte.return_value.range.return_value.execute.return_value = MagicMock(data=[])
    df = ReportStore(sb).fetch_stock_rows('016360', '2000-01-01')
    sb.table.assert_called_once_with('reports')
    sb.table.return_value.select.assert_called_once_with(SELECT_COLS)
    sb.table.return_value.select.return_value.in_.assert_called_once_with('tagging_status', ['auto', 'verified'])
    in_ret.is_.assert_called_once_with('out_of_scope_reason', 'null')
    contains.assert_called_once_with('stock_codes', ['016360'])
    contains.return_value.gte.assert_called_once_with('published_at', '2000-01-01')
    assert list(df.columns) == list(EXPECTED_COLS) and len(df) == 0


def test_stock_rows_are_in_scope_rows_holding_the_code_since_the_start_day():
    rows = [
        tagged(1),                                            # kept
        tagged(2, codes=('005930', '016360')),                # one of several codes → kept
        tagged(3, codes=('005930',)),                         # another company
        tagged(4, status='verified', reason='foreign'),       # out of scope
        tagged(5, status='review_needed'),                    # not final
        tagged(6, published='1999-12-31'),                    # before the start day
        tagged(7, codes=()),                                  # no codes (e.g. an industry report)
    ]
    store = ReportStore(FakeSupabase(reports=rows))
    assert ids(store.fetch_stock_rows('016360', '2000-01-01')) == [1, 2]
    # the DB is asked with the code as given: no zero-padding here
    assert ids(store.fetch_stock_rows('16360', '2000-01-01')) == []


# ── one row by id ────────────────────────────────────────────────────────────

def test_fetch_report_row_chain():
    sb = MagicMock()
    chain = sb.table.return_value.select.return_value.eq.return_value.in_.return_value.is_.return_value
    chain.execute.return_value = MagicMock(data=[{'id': 7, 'title': 'one'}])
    assert ReportStore(sb).fetch_report_row(7) == {'id': 7, 'title': 'one'}
    sb.table.assert_called_once_with('reports')
    sb.table.return_value.select.assert_called_once_with(SELECT_COLS)
    sb.table.return_value.select.return_value.eq.assert_called_once_with('id', 7)
    (sb.table.return_value.select.return_value.eq.return_value.in_
     .assert_called_once_with('tagging_status', ['auto', 'verified']))
    (sb.table.return_value.select.return_value.eq.return_value.in_.return_value.is_
     .assert_called_once_with('out_of_scope_reason', 'null'))


def test_fetch_report_row_is_none_without_an_in_scope_row():
    store = ReportStore(FakeSupabase(reports=[tagged(1), tagged(2, status='verified', reason='ir_self')]))
    assert store.fetch_report_row(1)['id'] == 1
    assert store.fetch_report_row(2) is None   # out of scope
    assert store.fetch_report_row(3) is None   # no such row
