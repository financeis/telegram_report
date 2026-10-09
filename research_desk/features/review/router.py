"""Web addresses of the review feature (spec §6).

- ``GET /api/review?skipped=…`` → ``{"remaining": <rows waiting>, "report": <next row or null>}``;
  the row has every column but ``file_path``, plus ``pdf_url`` (``/api/review/{id}/pdf``).
- ``GET /api/review/{rid}/pdf`` → the row's PDF (``application/pdf``); 404
  ``검토할 보고서가 없습니다.`` without that row, else the report PDF's 404 texts.
- ``GET /api/review/{rid}/preview`` → ``{"page_count", "preview_pages": min(3, page_count)}``.
- ``GET /api/review/{rid}/pages/{page}`` → page 1–3 as a 120 dpi PNG with
  ``Cache-Control: private, max-age=300``; 404 ``미리보기는 첫 3페이지까지 제공됩니다.`` outside 1–3,
  ``페이지가 없습니다.`` for a page the PDF does not have.
- ``POST /api/review/{rid}/action`` ``{"action": verify|oos|retag, "reason"}`` →
  ``{"undo_token", "report_id"}``; 422 ``분석 대상 제외 사유를 선택해 주세요.`` for oos without a
  reason, FastAPI's 422 for any other action or reason; the service's 404 / 409.
- ``POST /api/review/undo/{token}`` → ``{"report_id"}``; the service's 409 / 404.

Without DB settings the service raises ``NotReady("검토", …)``; the web app turns it into a 503.
The handler names, parameters and body model (and no docstrings on them) match the old app, so
these entries of ``/openapi.json`` stay the same; the reason values must equal
``domain.reports.OOS_REASONS`` (tested).
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from .service import get_service

OOS_REASON_REQUIRED = '분석 대상 제외 사유를 선택해 주세요.'
PAGE_CACHE_CONTROL = 'private, max-age=300'

router = APIRouter()


@router.get('/api/review')
def review(skipped: list[int] = Query(default=[]), service=Depends(get_service)):
    return service.queue(skipped)


@router.get('/api/review/{rid}/pdf')
def review_pdf(rid: int, service=Depends(get_service)):
    return FileResponse(service.pdf_path(rid), media_type='application/pdf')


@router.get('/api/review/{rid}/preview')
def review_preview(rid: int, service=Depends(get_service)):
    return service.preview(rid)


@router.get('/api/review/{rid}/pages/{page}')
def review_page(rid: int, page: int, service=Depends(get_service)):
    png = service.page_png(rid, page)
    return Response(png, media_type='image/png', headers={'Cache-Control': PAGE_CACHE_CONTROL})


class ReviewAction(BaseModel):
    action: Literal['verify', 'oos', 'retag']
    reason: Literal['foreign', 'fund', 'digital', 'private', 'ir_self'] | None = None


@router.post('/api/review/{rid}/action')
def review_action(rid: int, body: ReviewAction, service=Depends(get_service)):
    if body.action == 'oos' and not body.reason:
        raise HTTPException(422, OOS_REASON_REQUIRED)
    return service.act(rid, body.action, body.reason)


@router.post('/api/review/undo/{token}')
def review_undo(token: str, service=Depends(get_service)):
    return service.undo(token)
