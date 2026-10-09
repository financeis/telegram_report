"""Web addresses of the reports feature (spec §6).

- ``GET /api/stocks/{code}/reports`` → ``{"stock": {code, name, sector_major, sector_minor},
  "reports": [public report…]}``; 404 ``종목을 찾을 수 없습니다.`` for a code (zero-padded to 6
  digits) not in the stock list.
- ``GET /api/reports/{rid}/pdf`` → the in-scope report's PDF (``application/pdf``); 404
  ``기업 보고서를 찾을 수 없습니다.`` without that row, ``PDF를 찾을 수 없습니다.`` outside the
  storage folder or not a PDF, ``로컬 PDF 파일이 없습니다. 수집 상태를 확인해 주세요.`` when the
  file is missing.
- ``POST /api/reports/{rid}/analyze`` → public report + ``"analysis_reused": bool``; 404 without
  the row, then analysis' 409 / 422 / 404.

When the feature cannot prepare, the service raises ``NotReady("리포트", …)`` (analysis may raise
``NotReady("분석", …)``); the web app turns it into a 503. The handler names and parameters (and
no handler docstrings) match the old app, so these entries of ``/openapi.json`` stay the same.
"""
from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from .service import get_service

router = APIRouter()


@router.get("/api/stocks/{code}/reports")
def stock_reports(code: str, service=Depends(get_service)):
    return service.stock_reports(code)


@router.get("/api/reports/{rid}/pdf")
def pdf(rid: int, service=Depends(get_service)):
    return FileResponse(service.pdf_path(rid), media_type="application/pdf")


@router.post("/api/reports/{rid}/analyze")
async def analyze(rid: int, service=Depends(get_service)):
    return await service.analyze(rid)
