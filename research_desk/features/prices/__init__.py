"""Prices (주가): the daily KIS price snapshot of every stock in the stock list (spec §8).

This feature owns ``stock_price_snapshot`` (one row per stock, the latest snapshot only) and
``price_update_runs`` (one row per update run); other features read prices only through the
names below. No web routes. Units: returns and excess returns are percent floats (12.3 means
+12.3 %), in the DB and here alike; money is whole KRW.

- ``snapshots(codes)`` → ``{code: price}`` for the codes that have a stored snapshot (others are
  left out; each code once, in the order asked; codes that are not plain letters and digits match
  nothing; one plain string instead of a collection is a TypeError). ``price`` is spec §12.1's
  ``price`` plus ``market``::

      {"market": "KOSPI" | "KOSDAQ" | None, "as_of": "YYYY-MM-DD", "close": int,
       "market_cap": int, "avg_value_20d": int, "traded": bool,
       "returns": {"1w": float, "1m": float, "3m": float},
       "excess": {"1w": float, "1m": float, "3m": float},
       "flags": [...]}   # no_data / short_history / halted / admin_issue

  A missing value is None. ``excess`` = the return minus the median return of the same market in
  the run that stored it. ``no_data``: the last run could not get the stock, so the values are
  from an earlier run (see ``as_of``).
- ``latest_run()`` → ``{"last_run_at": datetime (aware, UTC; the latest run's start),
  "last_run_status": "running" | "ok" | "partial" | "failed", "as_of": date | None (the latest
  ok / partial run's data date)}``, or None when there has been no run.

Both raise ``NotReady("주가", "DB 접속 설정(SUPABASE_URL, SUPABASE_SERVICE_KEY)이 없습니다")``
without DB settings (tried again on the next call). Importing this window loads no FastAPI, HTTP
library, KIS client or MongoDB package.
"""
from .service import latest_run, snapshots
