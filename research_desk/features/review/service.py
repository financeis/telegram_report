"""Review work: the queue, the PDF preview, the review actions and their undo (spec §6, §9.7–§9.9).

Moved from langgraph_tagger/workspace/review.py (queue, row, act, undo) and the review parts of
langgraph_tagger/workspace/api.py (the PDF, the preview, the page images), unchanged in behaviour.

The queue. The ``review_needed`` row tagged longest ago, apart from the ids the browser skipped
(skipping lives only in the browser session), and the exact number of rows waiting. The row goes
to the browser with every column but ``file_path``, plus ``pdf_url`` (``/api/review/{id}/pdf``);
``file_hash_sha256`` and the other columns go as they are (spec §6). The old code also listed a
``file_hash`` column, which does not exist, so only file_path was ever left out.

The PDF, the preview and the pages. The row is read by id, whatever its status; its file must be a
``.pdf`` inside the storage folder (``core.pdf.resolve_in_storage`` under ``STORAGE_BASE_DIR``,
the same 404 texts as the report PDF). The preview counts the pages and offers at most the first
3; a page image is a 120 dpi PNG of page 1–3 (``core.pdf.render_page_png``). A page outside 1–3 is
refused before anything is read.

The actions (spec §9.8), one action or undo at a time (one lock for the process):

1. the row (404 ``검토할 보고서가 없습니다.``) must be ``review_needed``, else 409
   ``다른 작업에서 처리한 보고서입니다. 목록을 새로고침해 주세요.``;
2. the payload of ``rules`` for the action: verify → verified; oos → verified with the shared
   out-of-scope row shape and the reason; retag → the full reset to pending;
3. it is written only while the row is still ``review_needed``; when the row changed in between
   nothing is written: 409 ``보고서 상태가 바뀌었습니다. 새로고침해 주세요.``;
4. the row's 22 snapshot columns before and after the write are kept in server memory under a
   new token (at most 100 records; the oldest goes first; gone when the server restarts). The
   browser gets ``{"undo_token", "report_id"}`` and never sends report content back.

The undo: the token's record (409 ``되돌릴 작업이 없거나 서버가 재시작되었습니다.`` without one), then
the row (404 when it is gone), which must still equal the 'after' snapshot, else 409
``후속 작업이 처리한 보고서라 되돌릴 수 없습니다.``. The 'before' snapshot is written back only while
tagging_status, tagged_at, tagging_locked_at and tagging_worker_id still hold their 'after' values,
so a tagger that took the row in between is never overwritten: 409 ``후속 작업이 시작되어 되돌릴 수
없습니다.``. Only a successful undo uses up its token; a refused one keeps it.

The coverage cache (spec §9.7). Right after each successful action and undo, ``coverage.invalidate()``
(the coverage window) drops the cached report rows, so the coverage numbers show the decision at
once. Nothing refused or failed clears it.

Readiness (spec §9.9). The DB is prepared on first use and kept for the life of the process:
``core.settings.load_env()``, then SUPABASE_URL and SUPABASE_SERVICE_KEY → ``core.db.supabase_client``.
Without them: ``NotReady("검토", "DB 접속 설정(SUPABASE_URL, SUPABASE_SERVICE_KEY)이 없습니다")``,
and the next call prepares again, so adding the values to ``.env`` recovers without a restart.
The checks that need no DB come first, as in the old handlers: request validation, the preview
page range and the undo token.
"""
from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Any, Iterable, Optional
from uuid import uuid4

from fastapi import HTTPException

from research_desk.core import db, pdf, settings
from research_desk.features import coverage

from .rules import (
    build_oos_payload,
    build_pending_reset_payload,
    build_verified_payload,
    capture_snapshot,
)
from .store import REVIEW_NEEDED, ReviewStore

AREA = "검토"
DB_NOT_CONFIGURED = "DB 접속 설정(SUPABASE_URL, SUPABASE_SERVICE_KEY)이 없습니다"

ROW_NOT_FOUND = '검토할 보고서가 없습니다.'
NOT_IN_QUEUE = '다른 작업에서 처리한 보고서입니다. 목록을 새로고침해 주세요.'
CHANGED = '보고서 상태가 바뀌었습니다. 새로고침해 주세요.'
UNDO_UNKNOWN = '되돌릴 작업이 없거나 서버가 재시작되었습니다.'
UNDO_LATER_WORK = '후속 작업이 처리한 보고서라 되돌릴 수 없습니다.'
UNDO_STARTED = '후속 작업이 시작되어 되돌릴 수 없습니다.'
PAGE_LIMIT = '미리보기는 첫 3페이지까지 제공됩니다.'
PAGE_MISSING = '페이지가 없습니다.'

PREVIEW_PAGES = 3
PREVIEW_DPI = 120
# Undo records kept in server memory; the oldest goes first.
UNDO_LIMIT = 100
# The undo writes only while these still hold their values after the action (compare-and-set).
UNDO_GUARD_COLUMNS = ('tagging_status', 'tagged_at', 'tagging_locked_at', 'tagging_worker_id')
# The browser never gets the local path of the PDF (spec §6).
HIDDEN_COLUMN = 'file_path'


class ReviewService:
    """Review for one web app process. Creating it reads nothing; first use prepares the DB.

    ``undo_records`` (token → the report id and its 'snapshot' / 'after' columns) and ``lock``
    belong to the process, so the router uses one service for every request (``get_service``).
    """

    def __init__(self) -> None:
        self._store: Optional[ReviewStore] = None
        self._prepare_lock = Lock()
        self.lock = Lock()   # one action or undo at a time
        self.undo_records: dict[str, dict[str, Any]] = {}

    # ── readiness ────────────────────────────────────────────────────────────

    def store(self) -> ReviewStore:
        """The DB reads and writes. Prepares them now if needed; NotReady without DB settings."""
        store = self._store
        if store is None:
            with self._prepare_lock:
                store = self._store
                if store is None:
                    store = self._store = connect()
        return store

    # ── the queue and one row ────────────────────────────────────────────────

    def queue(self, skipped: Iterable[int]) -> dict[str, Any]:
        """``GET /api/review``: the next row to review (or None) and how many rows wait."""
        store = self.store()
        row = store.fetch_next_review(skipped)
        if row:
            row = {k: v for k, v in row.items() if k != HIDDEN_COLUMN}
            row['pdf_url'] = f"/api/review/{row['id']}/pdf"
        return {'remaining': store.count_review_queue(), 'report': row}

    def row(self, rid: int) -> dict[str, Any]:
        """Every column of row ``rid``; 404 ``검토할 보고서가 없습니다.`` when there is none."""
        row = self.store().fetch_row(rid)
        if row is None:
            raise HTTPException(404, ROW_NOT_FOUND)
        return row

    # ── the PDF ──────────────────────────────────────────────────────────────

    def pdf_path(self, rid: int) -> Path:
        """``GET /api/review/{rid}/pdf``: the row's PDF inside the storage folder."""
        row = self.row(rid)
        try:
            return pdf.resolve_in_storage(settings.storage_base_dir(), row['file_path'])
        except pdf.PDFNotFound as exc:
            raise HTTPException(404, exc.message) from None

    def preview(self, rid: int) -> dict[str, int]:
        """``GET /api/review/{rid}/preview``: the page count and how many pages to preview."""
        count = pdf.page_count(self.pdf_path(rid))
        return {'page_count': count, 'preview_pages': min(PREVIEW_PAGES, count)}

    def page_png(self, rid: int, page: int) -> bytes:
        """``GET /api/review/{rid}/pages/{page}``: page 1–3 of the row's PDF as a 120 dpi PNG."""
        if page < 1 or page > PREVIEW_PAGES:
            raise HTTPException(404, PAGE_LIMIT)
        path = self.pdf_path(rid)
        try:
            return pdf.render_page_png(path, page, dpi=PREVIEW_DPI)
        except pdf.PageNotFound:
            raise HTTPException(404, PAGE_MISSING) from None

    # ── the actions and the undo ─────────────────────────────────────────────

    def act(self, rid: int, action: str, reason: Optional[str] = None) -> dict[str, Any]:
        """``POST /api/review/{rid}/action``: verify, oos (with ``reason``) or retag a waiting row."""
        with self.lock:
            row = self.row(rid)
            if row['tagging_status'] != REVIEW_NEEDED:
                raise HTTPException(409, NOT_IN_QUEUE)
            if action == 'verify':
                payload = build_verified_payload(row)
            elif action == 'oos':
                payload = build_oos_payload(row, reason)
            else:
                payload = build_pending_reset_payload()
            written = self.store().update_if(rid, payload, {'tagging_status': REVIEW_NEEDED})
            if not written:
                raise HTTPException(409, CHANGED)
            token = uuid4().hex
            self.undo_records[token] = {'id': rid, 'snapshot': capture_snapshot(row),
                                        'after': capture_snapshot(written[0])}
            # A small session history; no report content comes from the browser on undo.
            if len(self.undo_records) > UNDO_LIMIT:
                self.undo_records.pop(next(iter(self.undo_records)))
            result = {'undo_token': token, 'report_id': rid}
        coverage.invalidate()
        return result

    def undo(self, token: str) -> dict[str, Any]:
        """``POST /api/review/undo/{token}``: put the row back as it was before that action."""
        with self.lock:
            saved = self.undo_records.get(token)
            if not saved:
                raise HTTPException(409, UNDO_UNKNOWN)
            current = self.row(saved['id'])
            if capture_snapshot(current) != saved['after']:
                raise HTTPException(409, UNDO_LATER_WORK)
            # Compare-and-set: never overwrite a tagger that took the row in the meantime.
            expected = {key: saved['after'].get(key) for key in UNDO_GUARD_COLUMNS}
            if not self.store().update_if(saved['id'], saved['snapshot'], expected):
                raise HTTPException(409, UNDO_STARTED)
            self.undo_records.pop(token)
            result = {'report_id': saved['id']}
        coverage.invalidate()
        return result


def connect() -> ReviewStore:
    """Re-read ``.env``, then open the Supabase REST client. NotReady without the DB settings."""
    settings.load_env()
    url, key = settings.supabase_url(), settings.supabase_service_key()
    if not (url and key):
        raise settings.NotReady(AREA, DB_NOT_CONFIGURED)
    return ReviewStore(db.supabase_client(url, key))


# ── the process-wide service ─────────────────────────────────────────────────

_service: Optional[ReviewService] = None
_service_lock = Lock()


def get_service() -> ReviewService:
    """The process-wide service, created on first use (creating it reads nothing).

    The router depends on this, so every request shares one set of undo records and one lock.
    Tests override it (``app.dependency_overrides[get_service]``) or replace ``_service``.
    """
    global _service
    with _service_lock:
        if _service is None:
            _service = ReviewService()
        return _service
