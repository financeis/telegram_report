"""Coverage work: the market counts and a company's activity (spec §6, §9.7, §9.9).

Rows. Coverage reads no table itself: a period's rows come from
``reports.period_rows(since, include_oos)`` and a company's rows from
``reports.stock_rows(code, since)``, with the code as received (spec §6). The reports window
prepares the DB; without DB settings it raises ``NotReady("리포트", …)``, which passes through
unchanged, so the screen names the feature that failed (spec §9.9).

Period cache (spec §9.7). The cache holds the rows read from the DB, never an answer: rows of one
``(start day, include_oos)`` at a time, for 180 seconds counted from when they arrived. A request
for another key reads its rows, and they replace the old ones. Every request counts again from
the rows (level, unit and items change the counts, not the rows), so a changed choice never
shows an old answer.

- One period read at a time, like the old service: a request waits for a read in flight, then
  uses its rows if the key is the same.
- ``invalidate()`` (review calls it after an action or an undo succeeds) empties the cache at
  once and never waits for a read in flight. Rows such a read returns go to the request that
  asked for them but are not kept, since they may be older than the review change; the next
  request reads again.

Readiness (spec §9.9, §8). The stock list gives the ranking its names (the code → name merge of
``logic.market_payload``). The market address prepares it on first use:
``core.settings.load_env()``, then ``domain.stocks.StockList.load(core.settings.krx_csv_path())``.
A file that cannot be read raises ``NotReady("커버리지", "종목표 파일을 읽을 수 없습니다")``; the
path and the cause go to the local log only. The next request prepares again, so filling in the
file or adding ``KRX_CSV_PATH`` to ``.env`` recovers without a restart. Once prepared, the list is
kept for the life of the process. A content hash that differs from the version file, or a missing
version file, only logs a warning: the web app shows names and never stores the version. The
stock list is prepared before any rows are read, so an unreadable list costs no DB read. The
activity address needs no stock list.

The period starts on today in Korea minus ``days`` (``logic.period_start``, used by the router).
"""
from __future__ import annotations

import logging
from threading import Lock
from time import monotonic
from typing import Callable, Optional

import pandas as pd

from research_desk.core import settings
from research_desk.domain.stocks import (
    VERSION_INVALID,
    VERSION_MISMATCH,
    VERSION_MISSING,
    StockList,
    StockListError,
)
from research_desk.features import reports

from .logic import market_payload, stock_activity_payload

logger = logging.getLogger(__name__)

AREA = "커버리지"
STOCK_LIST_UNREADABLE = "종목표 파일을 읽을 수 없습니다"
CACHE_SECONDS = 180
# The stock list as a table, for the ranking's code → name merge.
NAME_COLUMNS = ['code', 'name', 'sector_major', 'sector_minor']

_VERSION_PROBLEMS = {
    VERSION_MISSING: "종목표 버전 정보 파일이 없습니다",
    VERSION_MISMATCH: "종목표 파일 내용이 버전 정보와 다릅니다",
    VERSION_INVALID: "종목표 버전 정보 파일을 읽을 수 없습니다",
}


class CoverageService:
    """Coverage for one web app process. Creating it reads nothing; first use prepares.

    ``clock`` gives the seconds the cache ages by (``time.monotonic``); tests pass a fake one.
    """

    def __init__(self, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._read_lock = Lock()    # one period read at a time
        self._cache_lock = Lock()   # guards _cache and _generation; never held during a read
        self._cache: dict = {}      # {(since, include_oos): (arrived_at, rows)}: one entry at most
        self._generation = 0        # moved on by invalidate(); rows read across it are not kept
        self._names: Optional[pd.DataFrame] = None
        self._prepare_lock = Lock()

    # ── readiness ────────────────────────────────────────────────────────────

    def names(self) -> pd.DataFrame:
        """The stock list as a code / name table. Prepares it now if needed; NotReady when that
        fails."""
        names = self._names
        if names is None:
            with self._prepare_lock:
                names = self._names
                if names is None:
                    names = self._names = name_table(load_stock_list())
        return names

    # ── the period cache ─────────────────────────────────────────────────────

    def period_rows(self, since: str, include_oos: bool) -> pd.DataFrame:
        """The period's rows: the cached ones while fresh, else read through the reports window."""
        key = (since, include_oos)
        with self._read_lock:
            with self._cache_lock:
                entry = self._cache.get(key)
                if entry is not None and self._clock() - entry[0] < CACHE_SECONDS:
                    return entry[1]
                generation = self._generation
            rows = reports.period_rows(since, include_oos)
            with self._cache_lock:
                if generation == self._generation:
                    self._cache = {key: (self._clock(), rows)}
            return rows

    def invalidate(self) -> None:
        """Empty the cache now. A read in flight is not kept; this never waits for it."""
        with self._cache_lock:
            self._generation += 1
            self._cache = {}

    # ── addresses ────────────────────────────────────────────────────────────

    def market(self, since: str, level: str, items: list[str], unit: str, include_oos: bool) -> dict:
        """``GET /api/market``: the period's counts, ranking names from the stock list."""
        names = self.names()
        rows = self.period_rows(since, include_oos)
        return market_payload(rows, names, level, items, unit, include_oos)

    def activity(self, code: str, since: str, unit: str) -> dict:
        """``GET /api/stocks/{code}/activity``: the company's in-scope reports over time."""
        return stock_activity_payload(reports.stock_rows(code, since), code, unit)


def name_table(stocks: StockList) -> pd.DataFrame:
    """The stock list's catalog as a DataFrame (file order, "" for empty cells)."""
    return pd.DataFrame(stocks.catalog(), columns=NAME_COLUMNS)


def load_stock_list() -> StockList:
    """Re-read ``.env``, then load the stock list from ``KRX_CSV_PATH``.

    Raises NotReady when the file cannot be read; a version problem only logs a warning.
    """
    settings.load_env()
    path = settings.krx_csv_path()
    try:
        stocks = StockList.load(path)
    except StockListError as exc:
        # The response carries a fixed sentence; the path and the cause go to the local log only.
        logger.warning("커버리지 기능이 종목표를 읽지 못했습니다. 다음 요청 때 다시 시도합니다: %s", exc)
        raise settings.NotReady(AREA, STOCK_LIST_UNREADABLE) from exc
    check = stocks.verify()
    if not check.ok:
        logger.warning(
            "%s: %s. 커버리지 기능은 경고만 남기고 그대로 동작합니다. 종목표를 바꿨다면 "
            "python -m research_desk stocks set-version --as-of <자료 기준일, YYYY-MM-DD>를 실행하세요.",
            _VERSION_PROBLEMS.get(check.reason, check.reason), path,
        )
    return stocks


# ── the process-wide service and the window function ─────────────────────────

_service: Optional[CoverageService] = None
_service_lock = Lock()


def get_service() -> CoverageService:
    """The process-wide service, created on first use (creating it reads nothing).

    The router depends on this and ``invalidate()`` uses it, so both reach the same cache. Tests
    override it (``app.dependency_overrides[get_service]``) or replace ``_service``.
    """
    global _service
    with _service_lock:
        if _service is None:
            _service = CoverageService()
        return _service


def invalidate() -> None:
    """Drop the cached period rows now, so the next ``/api/market`` request reads them again.

    Review calls this after an action or an undo succeeds (spec §9.7). It never waits for a read
    in flight, and rows such a read returns are not kept.
    """
    get_service().invalidate()
