"""DB reads of tagged reports (Supabase REST). Read-only.

This feature owns the reads of finished classifications in the reports table (spec §4): the
material for the report list, the detail, the PDF and coverage. Ported from
langgraph_tagger/analytics/db.py with the same queries:

- every read selects the 16 ``EXPECTED_COLS``: the old 15 columns, then ``publisher_type`` (to
  count broker reports apart from other research); the list reads page through ``.range()``
  ``PAGE`` (1000) rows at a time until a short page;
- the in-scope filter is the shared rule of ``domain.reports``: ``tagging_status`` in
  ``IN_SCOPE_STATUSES`` and ``out_of_scope_reason`` IS NULL;
- in-scope rows: the period filter ``published_at >= start`` runs on the server, which is safe
  because the tagger always sets published_at for in-scope rows;
- with out-of-scope rows included: the tagger sets published_at NULL for those rows, so a
  server-side published_at filter would silently drop them. The server filters on the final
  statuses only, and rows are kept on the client by their effective date: published_at, else
  sent_at as a KST date;
- stock rows: in-scope rows whose ``stock_codes`` contain the code (as given), published since
  the start day;
- rows for several stocks: in-scope rows whose ``stock_codes`` share at least one of the codes
  (each as given). The server filters on the in-scope rule and the array overlap only; rows are
  kept on the client by their effective date, like the period read with out-of-scope rows. The
  codes are asked ``CODES_PER_QUERY`` at a time, each batch paged in id order;
- one row by id: in-scope only, None when there is none;
- the row sent last: the in-scope row with the latest ``sent_at`` (one row), None when there is
  none;
- rows being tagged: how many rows are ``processing`` under a lock taken since a given time, an
  exact count from the server that fetches no row (``head=True``).

All group-by / unnest / bucketing happens in the callers.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

import pandas as pd

from research_desk.domain.reports import IN_SCOPE_STATUSES

PAGE = 1000
# Codes in one array-overlap query: keeps the request address short.
CODES_PER_QUERY = 100
# The tagging status of a row a tagger has taken and is working on (one of
# domain.reports.TAGGING_STATUSES).
PROCESSING = 'processing'

EXPECTED_COLS: tuple[str, ...] = (
    'id', 'published_at', 'sent_at', 'report_type', 'publisher',
    'stock_codes', 'company_names', 'sectors_major', 'sectors_minor',
    'products', 'tagging_status', 'out_of_scope_reason', 'file_path',
    'file_name', 'title', 'publisher_type',
)

SELECT_COLS = ', '.join(EXPECTED_COLS)


def _paged_fetch(chain) -> list[dict]:
    """Loop .range(offset, offset+PAGE-1).execute() until a short page."""
    rows: list[dict] = []
    offset = 0
    while True:
        result = chain.range(offset, offset + PAGE - 1).execute()
        batch = result.data or []
        rows.extend(batch)
        if len(batch) < PAGE:
            break
        offset += PAGE
    return rows


def _to_frame(rows: list[dict]) -> pd.DataFrame:
    """Build a DataFrame that always has the expected columns, even for
    empty results — downstream aggregators index columns by name."""
    df = pd.DataFrame(rows, columns=list(EXPECTED_COLS))
    return df


def _effective_dates(df: pd.DataFrame) -> pd.Series:
    """Each row's effective date: published_at, else sent_at as a KST date (NaT without both)."""
    pub = pd.to_datetime(df['published_at'], errors='coerce')
    sent = pd.to_datetime(df['sent_at'], errors='coerce', utc=True)
    sent_kst = (sent.dt.tz_convert('Asia/Seoul')
                    .dt.tz_localize(None)
                    .dt.normalize())
    return pub.fillna(sent_kst)


def _on_or_after(df: pd.DataFrame, period_start_iso: str) -> pd.DataFrame:
    """The rows whose effective date is on or after the start day, index renumbered from 0.
    UTC 15:00 is 00:00 of the next day in KST; a row with neither date is dropped."""
    return df[_effective_dates(df) >= pd.Timestamp(period_start_iso)].reset_index(drop=True)


def _codes_to_ask(codes: Iterable[str]) -> list[str]:
    """The codes for an array filter: each once, in the given order, exactly as given.

    One plain string is refused: iterating it would ask for its characters one by one. Codes that
    are not plain letters and digits (empty, spaced, punctuated, not text) are left out: no stored
    code looks like that, and inside the array literal they would break the query or match wrong.
    """
    if isinstance(codes, str):
        raise TypeError('codes must be a collection of stock codes, not one string')
    return [code for code in dict.fromkeys(codes) if isinstance(code, str) and code.isalnum()]


def _final_status(chain):
    """Rows whose classification is final: tagging_status in IN_SCOPE_STATUSES."""
    return chain.in_('tagging_status', list(IN_SCOPE_STATUSES))


def _in_scope(chain):
    """The in-scope rule of domain.reports: a final status and no out-of-scope reason."""
    return _final_status(chain).is_('out_of_scope_reason', 'null')


class ReportStore:
    """Read-only access to tagged reports over a supabase-py client."""

    def __init__(self, client: Any) -> None:
        self._sb = client

    def _select(self):
        return self._sb.table('reports').select(SELECT_COLS)

    def fetch_inscope_rows(self, period_start_iso: str) -> pd.DataFrame:
        """In-scope rows only. tagging_status IN ('auto','verified') AND
        out_of_scope_reason IS NULL AND published_at >= period_start.
        """
        chain = _in_scope(self._select()).gte('published_at', period_start_iso)
        rows = _paged_fetch(chain)
        return _to_frame(rows)

    def fetch_inscope_or_oos_rows(self,
                                  period_start_iso: str,
                                  include_oos: bool) -> pd.DataFrame:
        """For the report-type volume view.

        - include_oos=False: in-scope only, server-side published_at filter.
        - include_oos=True: OOS rows have published_at=NULL. Skip the
          server-side period filter and drop rows whose effective_date
          (published_at, else sent_at as a KST date) < period_start here.
        """
        if include_oos:
            chain = _final_status(self._select())
        else:
            chain = _in_scope(self._select()).gte('published_at', period_start_iso)
        rows = _paged_fetch(chain)
        df = _to_frame(rows)
        if include_oos:
            # Client-side period filter using effective_date semantics:
            # published_at OR (sent_at as KST date).
            df = _on_or_after(df, period_start_iso)
        return df

    def fetch_stock_rows(self, code: str, period_start_iso: str) -> pd.DataFrame:
        """All in-scope rows where stock_codes contains the given code.

        Uses supabase-py's .contains() with a Python list, which it joins
        into a Postgres text-array literal without quotes ({001440}: fine
        for codes of letters and digits). The earlier
        .cs('stock_codes', f'{{{code}}}') passed a string, and cs() joins
        a string's characters with commas ({{,0,0,1,4,4,0,}}), so PostgREST
        got another array than the code meant.
        """
        chain = (_in_scope(self._select())
                 .contains('stock_codes', [code])
                 .gte('published_at', period_start_iso))
        rows = _paged_fetch(chain)
        return _to_frame(rows)

    def fetch_rows_for_stocks(self, codes: Iterable[str], period_start_iso: str) -> pd.DataFrame:
        """In-scope rows whose stock_codes share at least one code with ``codes`` (as given),
        kept when their effective date is on or after the start day.

        ``.ov()`` gets a real list, like ``.contains()`` above: supabase-py writes the array
        literal. The server filters on the in-scope rule and the overlap only; the period filter
        runs here. Codes go ``CODES_PER_QUERY`` at a time; a row found by two batches comes once.
        """
        wanted = _codes_to_ask(codes)
        rows: list[dict] = []
        seen: set = set()
        for start in range(0, len(wanted), CODES_PER_QUERY):
            chain = (_in_scope(self._select())
                     .ov('stock_codes', wanted[start:start + CODES_PER_QUERY])
                     .order('id'))   # a fixed order, so the pages do not shift between reads
            for row in _paged_fetch(chain):
                if row['id'] not in seen:
                    seen.add(row['id'])
                    rows.append(row)
        return _on_or_after(_to_frame(rows), period_start_iso)

    def fetch_report_row(self, rid: int) -> Optional[dict]:
        """The in-scope row with this id (the 16 columns), or None."""
        result = _in_scope(self._select().eq('id', rid)).execute()
        return result.data[0] if result.data else None

    def fetch_latest_row(self) -> Optional[dict]:
        """The in-scope row with the latest sent_at (the 16 columns), or None. NULLs sort last."""
        result = (_in_scope(self._select())
                  .order('sent_at', desc=True, nullsfirst=False)
                  .limit(1)
                  .execute())
        return result.data[0] if result.data else None

    def count_processing_since(self, locked_since_iso: str) -> int:
        """How many rows a tagger is working on under a lock taken at or after
        ``locked_since_iso``: an exact count from the server; no row is fetched."""
        result = (self._sb.table('reports')
                  .select('id', count='exact', head=True)
                  .eq('tagging_status', PROCESSING)
                  .gte('tagging_locked_at', locked_since_iso)
                  .execute())
        return int(result.count or 0)
