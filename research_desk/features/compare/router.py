"""Web addresses of the compare feature (spec §6).

- ``GET /api/compare?left=&right=`` → the comparison: ``left`` (the earlier report) and ``right``
  (the later one) in the public report shape, ``same_publisher``, ``metrics``, ``narrative`` (only
  the one saved for exactly this pair) and ``target_price_change``. Never calls the AI.
- ``POST /api/compare/analyze`` ``{"left", "right"}`` → the same plus a ``narrative``; one already
  saved for this pair comes back without an AI call.

404 ``기업 보고서를 찾을 수 없습니다.`` when either report is not an in-scope report; 422
``금융 비교는 단일종목 보고서 두 개를 선택해 주세요.`` / ``서로 다른 보고서 두 개를 선택해 주세요.`` /
``같은 기업의 보고서를 선택해 주세요.``, and for the narrative ``선택한 두 보고서를 먼저 분석해 주세요.``.
``NotReady("리포트", …)`` (no DB settings) and ``NotReady("분석", …)`` (no model key or codex CLI,
for a new narrative only) reach the web app, which answers 503. The handler names, parameters and
body model (with no docstrings) match the old app, so these entries of ``/openapi.json`` stay the
same.
"""
from fastapi import APIRouter
from pydantic import BaseModel

from . import service

router = APIRouter()


@router.get('/api/compare')
def compare(left: int, right: int):
    return service.compare_reports(left, right)


class ComparisonBody(BaseModel):
    left: int
    right: int


@router.post('/api/compare/analyze')
async def comparison_analysis(body: ComparisonBody):
    return await service.analyze_comparison(body.left, body.right)
