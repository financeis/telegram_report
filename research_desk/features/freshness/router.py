"""Web address of the freshness feature (spec §12.3).

- ``GET /api/freshness`` → ``{"prices": {"as_of", "last_run_at", "last_run_status", "stale",
  "note"}, "reports": {"latest_at", "stale"}, "checked_at"}``. No request values.

A plain ``def`` handler: the windows' reads are synchronous, so FastAPI runs it in a worker
thread. ``NotReady`` from the prices or reports window passes through; the web app turns it into
a 503 with that feature's sentence.
"""
from fastapi import APIRouter, Depends

from .service import get_service

router = APIRouter()


@router.get("/api/freshness")
def freshness(service=Depends(get_service)):
    return service.freshness()
