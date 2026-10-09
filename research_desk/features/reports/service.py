"""Reports work: a company's report list, a report's PDF, the analyze request, and the reads
other features use (spec §4, §6, §9.5, §9.9).

Readiness (spec §9.9). Two things are prepared on first use, each on its own, and kept for the
life of the process once prepared:

- the DB, needed by every report read: ``core.settings.load_env()``, then SUPABASE_URL and
  SUPABASE_SERVICE_KEY → ``core.db.supabase_client``. Without them:
  ``NotReady("리포트", "DB 접속 설정(SUPABASE_URL, SUPABASE_SERVICE_KEY)이 없습니다")``.
- the stock list, needed only for the company shown with the report list: ``load_env()``, then
  ``domain.stocks.StockList.load(KRX_CSV_PATH)``. A file that cannot be read:
  ``NotReady("리포트", "종목표 파일을 읽을 수 없습니다")``; the path and the cause go to the local
  log only. A content hash that differs from the version file, or a missing version file, only
  logs a warning (spec §8): the web app shows names and never stores the version.

A failed preparation is tried again on the next request, so filling in the file or adding the
missing values to ``.env`` recovers without a restart. The report list prepares the DB before
the stock list, like the old service, which read the DB settings first.

Codes from the web (spec §6): the company lookup pads a code shorter than 6 digits with leading
zeros; the DB is asked with the code as received.

Analysis (spec §9.5): the analyze request reads the in-scope row first (404 when there is none),
then hands the row to ``analysis.analyze_report``, which checks the request in flight (409),
단일종목 (422), reuse, the model key and the PDF. Summaries come from ``analysis.summaries_for``
(active version, 100 ids per query).

The public report shape never carries file_path, the storage folder or keys (spec §6).
``report_row`` returns the DB row itself (file_path included) for other features' work, never
for the browser.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from threading import Lock
from typing import Any, Iterable, Mapping, Optional

import pandas as pd
from fastapi import HTTPException

from research_desk.core import db, pdf, settings
from research_desk.domain.stocks import (
    VERSION_INVALID,
    VERSION_MISMATCH,
    VERSION_MISSING,
    StockList,
    StockListError,
)
from research_desk.features import analysis

from .store import ReportStore

logger = logging.getLogger(__name__)

AREA = "리포트"
DB_NOT_CONFIGURED = "DB 접속 설정(SUPABASE_URL, SUPABASE_SERVICE_KEY)이 없습니다"
STOCK_LIST_UNREADABLE = "종목표 파일을 읽을 수 없습니다"
REPORT_NOT_FOUND = "기업 보고서를 찾을 수 없습니다."
STOCK_NOT_FOUND = "종목을 찾을 수 없습니다."
CODE_WIDTH = 6
LIST_SINCE = "2000-01-01"

# Row columns the browser sees, in this order; then summary and pdf_url (spec §6).
PUBLIC_COLUMNS = (
    'id', 'title', 'file_name', 'published_at', 'publisher', 'report_type',
    'stock_codes', 'company_names', 'sectors_major', 'sectors_minor', 'products',
)

_VERSION_PROBLEMS = {
    VERSION_MISSING: "종목표 버전 정보 파일이 없습니다",
    VERSION_MISMATCH: "종목표 파일 내용이 버전 정보와 다릅니다",
    VERSION_INVALID: "종목표 버전 정보 파일을 읽을 수 없습니다",
}


def public_report(row: Mapping[str, Any], summary: Optional[dict]) -> dict:
    """The report as the browser sees it: the public columns, ``summary`` and ``pdf_url``."""
    return {k: row.get(k) for k in PUBLIC_COLUMNS} | {
        'summary': summary, 'pdf_url': f"/api/reports/{row['id']}/pdf"}


class ReportsService:
    """Report reads for one web app process. Creating it reads nothing; first use prepares."""

    def __init__(self) -> None:
        self._store: Optional[ReportStore] = None
        self._stocks: Optional[StockList] = None
        self._prepare_lock = Lock()

    # ── readiness ────────────────────────────────────────────────────────────

    def store(self) -> ReportStore:
        """The DB reads. Prepares them now if needed; NotReady without the DB settings."""
        store = self._store
        if store is None:
            with self._prepare_lock:
                store = self._store
                if store is None:
                    store = self._store = connect()
        return store

    def stock_list(self) -> StockList:
        """The stock list. Prepares it now if needed; NotReady when it cannot be read."""
        stocks = self._stocks
        if stocks is None:
            with self._prepare_lock:
                stocks = self._stocks
                if stocks is None:
                    stocks = self._stocks = load_stock_list()
        return stocks

    # ── reads for other features ─────────────────────────────────────────────

    def report_row(self, rid: int) -> dict:
        """The in-scope row ``rid``; 404 ``기업 보고서를 찾을 수 없습니다.`` when there is none."""
        row = self.store().fetch_report_row(rid)
        if row is None:
            raise HTTPException(404, REPORT_NOT_FOUND)
        return row

    def get_report(self, rid: int) -> dict:
        """The public shape of report ``rid`` with its saved summary (or None)."""
        return public_report(self.report_row(rid), analysis.summaries_for([rid]).get(rid))

    def period_rows(self, since: str, include_oos: bool) -> pd.DataFrame:
        return self.store().fetch_inscope_or_oos_rows(since, include_oos)

    def stock_rows(self, code: str, since: str) -> pd.DataFrame:
        return self.store().fetch_stock_rows(code, since)

    def rows_for_stocks(self, codes: Iterable[str], since: str) -> pd.DataFrame:
        return self.store().fetch_rows_for_stocks(codes, since)

    # ── addresses ────────────────────────────────────────────────────────────

    def stock_reports(self, code: str) -> dict:
        """``GET /api/stocks/{code}/reports``: the company and its in-scope reports since 2000,
        newest first (published_at, then id), each with its saved summary."""
        store = self.store()
        entry = self.stock_list().lookup(code.zfill(CODE_WIDTH))
        if entry is None:
            raise HTTPException(404, STOCK_NOT_FOUND)
        df = store.fetch_stock_rows(code, LIST_SINCE)
        rows = df.sort_values(['published_at', 'id'], ascending=False).to_dict('records')
        saved = analysis.summaries_for([r['id'] for r in rows])
        return {'stock': {'code': entry.code, 'name': entry.name,
                          'sector_major': entry.sector_major, 'sector_minor': entry.sector_minor},
                'reports': [public_report(r, saved.get(r['id'])) for r in rows]}

    def pdf_path(self, rid: int) -> Path:
        """``GET /api/reports/{rid}/pdf``: the in-scope report's PDF inside the storage folder."""
        row = self.report_row(rid)
        try:
            return pdf.resolve_in_storage(settings.storage_base_dir(), row['file_path'])
        except pdf.PDFNotFound as exc:
            raise HTTPException(404, exc.message) from None

    async def analyze(self, rid: int) -> dict:
        """``POST /api/reports/{rid}/analyze``: the row first (404), then analysis."""
        row = await asyncio.to_thread(self.report_row, rid)
        summary, reused = await analysis.analyze_report(row)
        return public_report(row, summary) | {'analysis_reused': reused}


def connect() -> ReportStore:
    """Re-read ``.env``, then open the Supabase REST client. NotReady without the DB settings."""
    settings.load_env()
    url, key = settings.supabase_url(), settings.supabase_service_key()
    if not (url and key):
        raise settings.NotReady(AREA, DB_NOT_CONFIGURED)
    return ReportStore(db.supabase_client(url, key))


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
        logger.warning("리포트 기능이 종목표를 읽지 못했습니다. 다음 요청 때 다시 시도합니다: %s", exc)
        raise settings.NotReady(AREA, STOCK_LIST_UNREADABLE) from exc
    check = stocks.verify()
    if not check.ok:
        logger.warning(
            "%s: %s. 리포트 기능은 경고만 남기고 그대로 동작합니다. 종목표를 바꿨다면 "
            "python -m research_desk stocks set-version --as-of <자료 기준일, YYYY-MM-DD>를 실행하세요.",
            _VERSION_PROBLEMS.get(check.reason, check.reason), path,
        )
    return stocks


# ── the process-wide service and the window functions ────────────────────────

_service: Optional[ReportsService] = None
_service_lock = Lock()


def get_service() -> ReportsService:
    """The process-wide service, created on first use (creating it reads nothing).

    The router depends on this; tests override it (``app.dependency_overrides[get_service]``).
    The window functions below use it too.
    """
    global _service
    with _service_lock:
        if _service is None:
            _service = ReportsService()
        return _service


def report_row(rid: int) -> dict:
    """The in-scope reports row ``rid`` with its 16 columns, file_path included: for other
    features' work, never for the browser. 404 ``기업 보고서를 찾을 수 없습니다.`` when there is
    none; NotReady("리포트", …) without the DB settings."""
    return get_service().report_row(rid)


def get_report(rid: int) -> dict:
    """``public_report`` of the in-scope report ``rid`` with its saved summary (404 / NotReady
    as ``report_row``)."""
    return get_service().get_report(rid)


def period_rows(since: str, include_oos: bool) -> pd.DataFrame:
    """Rows for a period starting on ``since`` (YYYY-MM-DD), the 16 columns.

    In-scope rows published since then; with ``include_oos``, every final row whose effective
    date (published_at, else the KST date of sent_at) is since then. NotReady without DB settings.
    """
    return get_service().period_rows(since, include_oos)


def stock_rows(code: str, since: str) -> pd.DataFrame:
    """In-scope rows whose stock_codes hold ``code`` (as given), published since ``since``, the
    16 columns. NotReady without DB settings."""
    return get_service().stock_rows(code, since)


def rows_for_stocks(codes: Iterable[str], since: str) -> pd.DataFrame:
    """In-scope rows whose stock_codes share at least one code with ``codes`` and whose effective
    date (published_at, else the KST date of sent_at) is on or after ``since`` (YYYY-MM-DD); the
    16 columns, empty included.

    Codes are used as given (no zero-padding); one that is not plain letters and digits matches
    nothing, and one plain string instead of a collection is a TypeError. Read 1000 rows at a time
    and 100 codes per query, a row only once. NotReady without DB settings, codes or not.
    """
    return get_service().rows_for_stocks(codes, since)
