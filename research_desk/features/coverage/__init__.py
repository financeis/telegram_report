"""Coverage (리서치 커버리지): counts of the collected reports by period, sector, product, report
type and company.

Public names:

- ``router``: ``GET /api/market`` and ``GET /api/stocks/{code}/activity`` (spec §6).
- ``invalidate()``: drop the cached period rows, so the next ``/api/market`` request reads them
  again. Review calls it after an action or an undo succeeds (spec §9.7).

Rows come from the reports window (``reports.period_rows`` / ``reports.stock_rows``); ranking
names come from the stock list (``domain.stocks``). An unreadable stock list makes the market
address ``NotReady("커버리지", …)``; the reports window's ``NotReady("리포트", …)`` passes through
unchanged (spec §9.9).
"""
from .router import router
from .service import invalidate
