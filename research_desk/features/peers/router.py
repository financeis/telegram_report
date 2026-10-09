"""Web addresses of the peers feature (spec §12.1, §12.2). The window hands this router out through
``web_router()``, so importing the window loads no FastAPI.

- ``GET /api/stocks/{code}/peers?window=1w|1m|3m&segment=<n>`` → the seed, the basis (the latest
  public build), the window (default ``1m``), the chosen seed segment (default: the largest
  revenue share), ``price_as_of``, ``seed_excess_pct``, ``judgeable`` and the peers list.
  Checks, in this order: 422 (FastAPI's, before anything is prepared) for a code that is not six
  capital letters or digits, a window outside 1w / 1m / 3m or a segment that is not a whole
  number of 0 or more; the service's readiness 503; 404 ``종목을 찾을 수 없습니다.`` for a code not
  in the stock list; 404 ``이 종목은 유사 기업 자료가 없습니다.`` without its profile in the public
  build; 422 ``그 사업부문이 없습니다.`` for a segment number the seed does not have.
- ``POST /api/peers/search`` with ``PeerSearchBody`` ``{"q": 2–100 characters (outer spaces
  dropped), "window": 1w|1m|3m (default 1m), "limit": a whole number 1–50 (default 30)}`` →
  ``{"query", "basis", "window", "price_as_of", "results"}``; ``price_as_of`` is the latest price
  date among the results (null without any). Anything else in the body is 422 before anything is
  prepared. An AI-calling address, so POST (the web app's origin check covers it).

``segment_match.revenue_share_pct`` and the segments' ``revenue_share_pct`` keep -1 for an unknown
share. Readiness failures are ``NotReady("유사 기업", …)``; the web app turns them into a 503.
"""
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, Field, StringConstraints

from .service import get_service

CODE_PATTERN = r"^[0-9A-Z]{6}$"

router = APIRouter()


@router.get("/api/stocks/{code}/peers")
def stock_peers(code: Annotated[str, Path(pattern=CODE_PATTERN)],
                window: Literal["1w", "1m", "3m"] = "1m",
                segment: Annotated[Optional[int], Query(ge=0)] = None,
                service=Depends(get_service)):
    return service.peers(code, window, segment)


class PeerSearchBody(BaseModel):
    q: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=100)]
    window: Literal["1w", "1m", "3m"] = "1m"
    limit: Annotated[int, Field(strict=True, ge=1, le=50)] = 30


@router.post("/api/peers/search")
async def peers_search(body: PeerSearchBody, service=Depends(get_service)):
    return await service.search(body.q, body.window, body.limit)
