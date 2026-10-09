"""Coverage (리서치 커버리지): counts of the collected reports by period, sector, product, report
type and company.

Public names:

- ``router``: ``GET /api/market`` and ``GET /api/stocks/{code}/activity`` (spec §6).
- ``invalidate()``: drop the cached period rows, so the next ``/api/market`` request reads them
  again. Review calls it after an action or an undo succeeds (spec §9.7).
- ``report_counts(codes, days=365)`` → ``{code: {stock_reports, sector_mentions, other_research,
  last_stock_report_date, brokers, label}}``: how much research covers each company over the last
  ``days`` days (spec §7). Every code is in the answer, a code without rows with zeros, None and
  ``none``. ``label`` is ``none`` / ``few`` / ``covered``.

Rows come from the reports window (``reports.period_rows`` / ``reports.stock_rows`` /
``reports.rows_for_stocks``); ranking names come from the stock list (``domain.stocks``). An
unreadable stock list makes the market address ``NotReady("커버리지", …)``; the reports window's
``NotReady("리포트", …)`` passes through unchanged (spec §9.9).
"""
from .router import router
from .service import invalidate, report_counts
