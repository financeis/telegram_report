"""Freshness work: the ``GET /api/freshness`` answer from the prices and reports windows
(spec §12.3, §12.4).

Every request asks ``prices.latest_run()`` (the latest run and the latest successful run's as_of)
and ``reports.latest_report_sent_at()`` (the newest in-scope report's sent_at), then judges both
at the clock's now with ``logic.freshness_payload``. Both are light reads; nothing is cached, so
a finished price run or a newly tagged report shows on the next request. The window functions are
looked up on the window modules when called (``from research_desk.features import prices``,
then ``prices.latest_run()``), so tests can replace them.

Readiness (spec §12.4). This feature reads no setting, file or table of its own and has nothing
to prepare; the windows prepare their own DB connection. Their ``NotReady("주가", …)`` /
``NotReady("리포트", …)`` passes through unchanged, so the route answers 503 with that feature's
sentence (the screen then shows grey ``자료 기준일 확인 불가``), and nothing of the failure is
kept: the next request asks the windows again. Any other error (a DB outage) is not turned into
NotReady; the web app answers it with its general 503. ``AREA`` is this feature's name for a
readiness failure of its own; it has none.
"""
from __future__ import annotations

from datetime import datetime
from threading import Lock
from typing import Callable, Optional

from research_desk.features import prices, reports

from .logic import KST, freshness_payload

AREA = "자료 기준일"


def now_in_korea() -> datetime:
    """The current time, aware, in Korea."""
    return datetime.now(KST)


class FreshnessService:
    """Freshness for one web app process. Creating it reads nothing (no window, no clock).

    ``clock`` gives "now" as an aware datetime (default ``now_in_korea``); tests pass a fixed one.
    """

    def __init__(self, clock: Optional[Callable[[], datetime]] = None) -> None:
        self._clock = clock if clock is not None else now_in_korea

    def freshness(self) -> dict:
        """``GET /api/freshness``: both windows' values judged now. NotReady from a window passes
        through unchanged."""
        run = prices.latest_run()
        latest_at = reports.latest_report_sent_at()
        return freshness_payload(run, latest_at, self._clock())


# ── the process-wide service ─────────────────────────────────────────────────

_service: Optional[FreshnessService] = None
_service_lock = Lock()


def get_service() -> FreshnessService:
    """The process-wide service, created on first use (creating it reads nothing).

    The router depends on this. Tests override it (``app.dependency_overrides[get_service]``) or
    replace ``_service``.
    """
    global _service
    with _service_lock:
        if _service is None:
            _service = FreshnessService()
        return _service
