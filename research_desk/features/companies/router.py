"""Web addresses of the companies feature (spec §6).

- ``GET /api/workspace`` → ``{"stocks": [{code, name, sector_major, sector_minor}…], "favorites": [code…]}``
- ``PUT /api/favorites/{code}`` with ``{"enabled": bool}`` → ``{"favorites": [...]}``;
  404 ``종목을 찾을 수 없습니다.`` when the zero-padded code is not in the stock list.

When the stock list cannot be read the service raises ``NotReady("기업 목록", …)``; the web app
turns it into a 503. The handler and body-model names (and no handler docstrings) match the old
app, so these entries of ``/openapi.json`` stay the same.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .service import get_service

router = APIRouter()


class FavoriteBody(BaseModel):
    enabled: bool


@router.get("/api/workspace")
def bootstrap(service=Depends(get_service)):
    return service.bootstrap()


@router.put("/api/favorites/{code}")
def favorite(code: str, body: FavoriteBody, service=Depends(get_service)):
    return service.set_favorite(code, body.enabled)
