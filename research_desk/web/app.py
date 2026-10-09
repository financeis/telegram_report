"""The web app: common devices, error answers, screen files and the feature list (spec §6, §9.9).

``create_app(dist=None)`` builds the app the ``web`` command serves. It only registers; the work
happens in the features. The common devices are the old app's (langgraph_tagger/workspace/api.py):

- ``FastAPI(title='Research Desk', docs_url=None, redoc_url=None)``: ``/openapi.json`` stays on,
  the docs pages are off.
- Hosts: only ``127.0.0.1``, ``localhost`` and ``testserver`` (anything else: 400).
- Origins: a request other than GET / HEAD / OPTIONS whose ``Origin`` is not ``127.0.0.1:8520``,
  ``localhost:8520``, ``127.0.0.1:5173`` or ``localhost:5173`` → 403 ``허용되지 않은 요청입니다.``.
  The host check is added first and the origin check second, as before, so the origin check
  still runs first.
- A feature that cannot prepare raises ``core.settings.NotReady`` → 503 ``{"detail": "<기능 이름>
  기능을 지금 쓸 수 없습니다: <이유>"}`` and one warning line in the local log (the reason holds
  no path, key or traceback), so only that feature's addresses stop.
- Any other unexpected error → logged with its traceback, 503 with a fixed sentence.

``GET /api/health`` → ``{"status": "ok"}``, whatever feature is broken. ``FEATURES`` is the one
place features are wired, in this order, each by ``feature_router(feature)`` (spec §13): the
window's ``web_router()`` when it has one, else its ``router``. A window that also binds a command
(``register_jobs``, imported by ``cli.py`` for every command) cannot bind its router, since that
loads FastAPI; it has ``web_router()``, which imports its router when called — so FastAPI and that
feature's web side load when the app is made, never when the window is imported. (Once called,
such a package also has an attribute ``router``: its router submodule, not a router. That is why
``web_router`` is looked at first.)

Screen files come from ``dist`` (default ``DEFAULT_DIST`` = ``<repository>/frontend/dist``). When
that folder exists as the app is made, ``/assets`` serves ``dist/assets``. ``GET /`` serves
``dist/index.html`` with ``Cache-Control: no-cache``, looked up on each request; without it, 503
``프론트엔드를 먼저 빌드해 주세요: cd frontend && npm run build``.

Making the app reads no setting and touches no DB or AI (each feature prepares on first use), and
importing this module makes no app.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from types import ModuleType
from typing import Optional, Union
from urllib.parse import urlparse

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from research_desk.core.settings import NotReady
from research_desk.features import companies, compare, coverage, freshness, peers, reports, review

logger = logging.getLogger(__name__)

# The feature registration list (spec §2, §13): a feature with web addresses is one more name here.
FEATURES = [companies, reports, compare, coverage, review, peers, freshness]

TITLE = 'Research Desk'
ALLOWED_HOSTS = ['127.0.0.1', 'localhost', 'testserver']
# This server's own pages and the frontend dev server's.
ALLOWED_ORIGINS = ('127.0.0.1:8520', 'localhost:8520', '127.0.0.1:5173', 'localhost:5173')
SAFE_METHODS = ('GET', 'HEAD', 'OPTIONS')

FORBIDDEN = '허용되지 않은 요청입니다.'
UNEXPECTED = '데이터를 불러오거나 분석하지 못했습니다. 연결 상태를 확인하고 다시 시도해 주세요.'
BUILD_FIRST = '프론트엔드를 먼저 빌드해 주세요: cd frontend && npm run build'

# web -> research_desk -> repository
DEFAULT_DIST = Path(__file__).resolve().parents[2] / 'frontend' / 'dist'

PathLike = Union[str, os.PathLike]


async def local_writes(request: Request, call_next):
    """Refuse a write from a page of another origin (403)."""
    if request.method not in SAFE_METHODS:
        origin = request.headers.get('origin')
        if origin and urlparse(origin).netloc not in ALLOWED_ORIGINS:
            return JSONResponse({'detail': FORBIDDEN}, status_code=403)
    return await call_next(request)


async def not_ready(request: Request, exc: NotReady) -> JSONResponse:
    """A feature could not prepare: 503 with its text, one warning line (no traceback)."""
    logger.warning('%s %s: %s', request.method, request.url.path, exc)
    return JSONResponse({'detail': str(exc)}, status_code=503)


async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Anything else: logged with its traceback, 503 with a fixed sentence."""
    logger.exception('Workspace request failed', exc_info=exc)
    return JSONResponse({'detail': UNEXPECTED}, status_code=503)


def health():
    return {'status': 'ok'}


def feature_router(feature: ModuleType) -> APIRouter:
    """The router of a feature window: ``feature.web_router()`` when the window has
    ``web_router``, else ``feature.router``."""
    web_router = getattr(feature, 'web_router', None)
    if web_router is not None:
        return web_router()
    return feature.router


def create_app(dist: Optional[PathLike] = None) -> FastAPI:
    """The web app: common devices, error answers, ``/api/health``, the features in ``FEATURES``
    order (each by ``feature_router``), then the screen files from ``dist`` (default
    ``DEFAULT_DIST``)."""
    dist = Path(dist) if dist is not None else DEFAULT_DIST
    app = FastAPI(title=TITLE, docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
    app.middleware('http')(local_writes)
    app.exception_handler(NotReady)(not_ready)
    app.exception_handler(Exception)(unexpected_error)
    app.get('/api/health')(health)
    for feature in FEATURES:
        app.include_router(feature_router(feature))
    _add_screen_files(app, dist)
    return app


def _add_screen_files(app: FastAPI, dist: Path) -> None:
    if dist.exists():
        app.mount('/assets', StaticFiles(directory=dist / 'assets'), name='assets')

    @app.get('/')
    def index():
        if not (dist / 'index.html').exists():
            raise HTTPException(503, BUILD_FIRST)
        return FileResponse(dist / 'index.html', headers={'Cache-Control': 'no-cache'})
