"""Freshness (자료 기준일): how current the price snapshot and the newest report are, for the status
line on top of every screen (spec §12.3, §12.5).

Public name:

- ``router``: ``GET /api/freshness`` (no request values) →

      {"prices": {"as_of": "YYYY-MM-DD" | null,
                  "last_run_at": "2026-10-08T18:30:00+09:00" | null,
                  "last_run_status": "running" | "ok" | "partial" | "failed" | null,
                  "stale": bool, "note": str | null},
       "reports": {"latest_at": "2026-10-08T09:12:00+09:00" | null, "stale": bool},
       "checked_at": "2026-10-08T20:00:00+09:00"}

  ``as_of`` is the latest successful (ok / partial) price run's data day, ``last_run_at`` the
  latest run's start; ``latest_at`` is the newest in-scope report's sent_at; times are in Korea,
  cut to the second, and null when unknown. Prices are stale, ``note`` saying why (the first that
  fits), when no run has ever succeeded (``주가가 아직 한 번도 갱신되지 않았습니다``), when the
  latest run failed (``마지막 주가 갱신이 실패했습니다``), or when ``as_of`` is before the expected
  trading day (``주가가 10월 7일 기준으로 밀려 있습니다. 휴장일이면 정상입니다``); ``note`` is null
  exactly when they are fresh. The expected trading day is today when it is a weekday at or after
  20:00 in Korea (after the 18:30 daily price run), else the nearest weekday before today. Reports are stale without any in-scope
  report or when the newest is more than 3 days old; reports have no note. The rules and their
  adjustable values are in ``logic.py``.

The values come from the prices window (``prices.latest_run()``) and the reports window
(``reports.latest_report_sent_at()``) on every request; nothing is cached. Their
``NotReady("주가", …)`` / ``NotReady("리포트", …)`` passes through unchanged (spec §12.4): the route
answers 503 with that feature's sentence, and the next request asks again. This feature's own
name is ``자료 기준일``; it has nothing to prepare.
"""
from .router import router
