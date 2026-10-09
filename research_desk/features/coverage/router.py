"""Web addresses of the coverage feature (spec §6).

- ``GET /api/market`` → ``{"total", "inscope", "oos", "publishers", "latest", "earliest",
  "available_items", "coverage", "ranking", "types"}``. Query: ``days`` 1–36500 (default 36500),
  ``unit`` D|W|M (default W), ``level`` sectors_major|sectors_minor|products (default
  sectors_major), ``items`` (repeatable, default none), ``include_oos`` (default false).
- ``GET /api/stocks/{code}/activity`` → ``{"timeline", "publishers", "total"}``, industry
  reports included. Query: ``days`` 1–36500 (default 36500), ``unit`` D|W|M (default W).

Any other value is a 422 before anything is read. The period starts on today in Korea minus
``days``. When the feature cannot prepare, the service raises ``NotReady("커버리지", …)``, or the
reports window's ``NotReady("리포트", …)`` passes through; the web app turns it into a 503. The
handler names and parameters (and no handler docstrings) match the old app, so these entries of
``/openapi.json`` stay the same.
"""
from typing import Literal

from fastapi import APIRouter, Depends, Query

from .logic import period_start
from .service import get_service

router = APIRouter()


@router.get("/api/market")
def market(days: int = Query(36500, ge=1, le=36500),
           unit: Literal['D', 'W', 'M'] = 'W',
           level: Literal['sectors_major', 'sectors_minor', 'products'] = 'sectors_major',
           items: list[str] = Query(default=[]), include_oos: bool = False,
           service=Depends(get_service)):
    return service.market(period_start(days), level, items, unit, include_oos)


@router.get("/api/stocks/{code}/activity")
def activity(code: str, days: int = Query(36500, ge=1, le=36500),
             unit: Literal['D', 'W', 'M'] = 'W', service=Depends(get_service)):
    return service.activity(code, period_start(days), unit)
