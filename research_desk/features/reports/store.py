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
- one row by id: in-scope only, None when there is none.

All group-by / unnest / bucketing happens in the callers.
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from research_desk.domain.reports import IN_SCOPE_STATUSES

PAGE = 1000

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
            pub = pd.to_datetime(df['published_at'], errors='coerce')
            sent = pd.to_datetime(df['sent_at'], errors='coerce', utc=True)
            sent_kst = (sent.dt.tz_convert('Asia/Seoul')
                            .dt.tz_localize(None)
                            .dt.normalize())
            eff = pub.fillna(sent_kst)
            cutoff = pd.Timestamp(period_start_iso)
            df = df[eff >= cutoff].reset_index(drop=True)
        return df

    def fetch_stock_rows(self, code: str, period_start_iso: str) -> pd.DataFrame:
        """All in-scope rows where stock_codes contains the given code.

        Uses supabase-py's .contains() which builds a properly-quoted
        Postgres text-array literal from a Python list. The earlier
        .cs('stock_codes', f'{{{code}}}') produced an unquoted array
        like {001440} which PostgREST parsed character-by-character.
        """
        chain = (_in_scope(self._select())
                 .contains('stock_codes', [code])
                 .gte('published_at', period_start_iso))
        rows = _paged_fetch(chain)
        return _to_frame(rows)

    def fetch_report_row(self, rid: int) -> Optional[dict]:
        """The in-scope row with this id (the 16 columns), or None."""
        result = _in_scope(self._select().eq('id', rid)).execute()
        return result.data[0] if result.data else None
