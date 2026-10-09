"""Shared pieces of the web tests: the spec's texts, the route table, temp files, and stand-ins for
the feature services (for tests that need a feature's answer without its DB or AI).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from starlette.routing import Mount

try:
    # FastAPI keeps an included router as one entry of app.routes; this walks the routes it
    # serves, with their effective paths (what FastAPI's own /openapi.json generation uses).
    from fastapi.routing import iter_route_contexts
except ImportError:   # older FastAPI copied the included routes into app.routes
    def iter_route_contexts(routes):
        return iter(routes)

# ── texts (spec §6, §9.9) ────────────────────────────────────────────────────

FORBIDDEN = {'detail': '허용되지 않은 요청입니다.'}
UNEXPECTED = {'detail': '데이터를 불러오거나 분석하지 못했습니다. 연결 상태를 확인하고 다시 시도해 주세요.'}
BUILD_FIRST = {'detail': '프론트엔드를 먼저 빌드해 주세요: cd frontend && npm run build'}
DB_REASON = 'DB 접속 설정(SUPABASE_URL, SUPABASE_SERVICE_KEY)이 없습니다'
STOCKS_REASON = '종목표 파일을 읽을 수 없습니다'
KEY_REASON = 'ANTHROPIC_API_KEY가 설정되지 않았습니다. .env에 추가 후 분석 다시 시도하세요.'
UNDO_UNKNOWN = {'detail': '되돌릴 작업이 없거나 서버가 재시작되었습니다.'}
NOT_ANALYZED = {'detail': '선택한 두 보고서를 먼저 분석해 주세요.'}
NO_PUBLIC_BUILD_REASON = '아직 공개된 유사도 계산 결과가 없습니다(python -m research_desk peers build)'
OPENAI_KEY_REASON = 'OPENAI_API_KEY가 설정되지 않았습니다'


def not_ready(area: str, reason: str) -> dict:
    """The 503 body of a feature that cannot prepare."""
    return {'detail': f'{area} 기능을 지금 쓸 수 없습니다: {reason}'}


# ── the route table ──────────────────────────────────────────────────────────

# spec §6, row by row, as (method, path). The screen files' mount is ('GET', '/assets/*'). HEAD is
# left out: Starlette adds it by itself next to GET on /openapi.json (and the static files answer
# it); the API routes, like the old app's, have none.
SPEC_ROUTES = {
    ('GET', '/api/health'),
    ('GET', '/api/workspace'),
    ('PUT', '/api/favorites/{code}'),
    ('GET', '/api/stocks/{code}/reports'),
    ('GET', '/api/reports/{rid}/pdf'),
    ('POST', '/api/reports/{rid}/analyze'),
    ('GET', '/api/compare'),
    ('POST', '/api/compare/analyze'),
    ('GET', '/api/market'),
    ('GET', '/api/stocks/{code}/activity'),
    ('GET', '/api/review'),
    ('GET', '/api/review/{rid}/pdf'),
    ('GET', '/api/review/{rid}/preview'),
    ('GET', '/api/review/{rid}/pages/{page}'),
    ('POST', '/api/review/{rid}/action'),
    ('POST', '/api/review/undo/{token}'),
    ('GET', '/api/stocks/{code}/peers'),     # peers (its window's web_router())
    ('POST', '/api/peers/search'),
    ('GET', '/api/freshness'),               # freshness
    ('GET', '/'),
    ('GET', '/assets/*'),
    ('GET', '/openapi.json'),
}


def served_routes(app: FastAPI) -> list:
    """The routes the app serves, in matching order, included routers expanded. Each has
    ``path`` (the effective one) and ``methods`` (None for a mount); ``original_route`` (when
    present) is the route object itself."""
    return list(iter_route_contexts(app.routes))


def is_mount(route) -> bool:
    return isinstance(getattr(route, 'original_route', route), Mount)


def route_table(app: FastAPI) -> list[tuple[str, str]]:
    """Every (method, path) the app serves, in route order (a mount as ``('GET', '<path>/*')``,
    Starlette's automatic HEAD left out)."""
    table: list[tuple[str, str]] = []
    for route in served_routes(app):
        if is_mount(route):
            table.append(('GET', f'{route.path}/*'))
        else:
            table.extend((method, route.path) for method in sorted(set(route.methods) - {'HEAD'}))
    return table


# ── files ────────────────────────────────────────────────────────────────────

INDEX_HTML = b'<!doctype html><title>Research Desk</title><div id="root"></div>'
APP_JS = b'console.log("research desk");'
PDF_BYTES = b'%PDF-1.4 web test'
PNG = b'\x89PNG\r\n\x1a\nweb test'

# The first header cell has a line break inside quotes, like the real file.
HEADER_LINE = '"종목\n코드",종목명,시장,산업명(대),산업명(중),주요제품\n'
ROWS = ('005930,삼성전자,KOSPI,반도체,메모리반도체,DRAM/NAND\n'
        '016360,삼성증권,KOSPI,금융,증권,증권\n')
CATALOG = [
    {'code': '005930', 'name': '삼성전자', 'sector_major': '반도체', 'sector_minor': '메모리반도체'},
    {'code': '016360', 'name': '삼성증권', 'sector_major': '금융', 'sector_minor': '증권'},
]


def write_screen_files(folder: Path) -> Path:
    """A built frontend: ``index.html`` and ``assets/app.js``."""
    (folder / 'assets').mkdir(parents=True)
    (folder / 'index.html').write_bytes(INDEX_HTML)
    (folder / 'assets' / 'app.js').write_bytes(APP_JS)
    return folder


def write_stock_csv(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((HEADER_LINE + ROWS).encode('utf-8'))
    return path


# ── stand-ins for the feature services ───────────────────────────────────────

def report(rid: int, published: str, publisher: str = 'KB', code: str = '016360',
           summary: Optional[dict] = None) -> dict:
    """The old test_workspace.py helper: a report in its public shape."""
    return {'id': rid, 'published_at': published, 'publisher': publisher,
            'stock_codes': [code], 'summary': summary}


class FakeCompanies:
    """The old test_workspace.py stub service's companies part."""

    def bootstrap(self) -> dict:
        return {'stocks': [], 'favorites': []}

    def set_favorite(self, code: str, enabled: bool) -> dict:
        return {'favorites': [code] if enabled else []}


class FakeReports:
    """Answers like the reports service, behind its router and its window (compare reads the
    reports through ``reports.get_report``). The old stub's ``report`` and ``analyze``."""

    def __init__(self, pdf: Path) -> None:
        self.pdf = pdf

    def get_report(self, rid: int) -> dict:
        return report(rid, f'2026-0{rid}-01')

    def stock_reports(self, code: str) -> dict:
        return {'stock': {'code': code}, 'reports': [self.get_report(1)]}

    def pdf_path(self, rid: int) -> Path:
        return self.pdf

    async def analyze(self, rid: int) -> dict:
        return self.get_report(rid) | {'summary': {'one_line_summary': '검증된 응답'},
                                       'analysis_reused': False}


class FakeCoverage:
    """Echoes what the coverage router hands its service."""

    def market(self, since: str, level: str, items: list, unit: str, include_oos: bool) -> dict:
        return {'since': since, 'level': level, 'items': items, 'unit': unit,
                'include_oos': include_oos}

    def activity(self, code: str, since: str, unit: str) -> dict:
        return {'code': code, 'since': since, 'unit': unit}


class FakeReview:
    """Answers like the review service and echoes what the review router hands it."""

    def __init__(self, pdf: Path) -> None:
        self.pdf = pdf

    def queue(self, skipped) -> dict:
        return {'remaining': 0, 'report': None, 'skipped': list(skipped)}

    def pdf_path(self, rid: int) -> Path:
        return self.pdf

    def preview(self, rid: int) -> dict:
        return {'page_count': 1, 'preview_pages': 1}

    def page_png(self, rid: int, page: int) -> bytes:
        return PNG

    def act(self, rid: int, action: str, reason: Optional[str] = None) -> dict:
        return {'undo_token': f'{action}-{reason}', 'report_id': rid}

    def undo(self, token: str) -> dict:
        return {'report_id': 1, 'token': token}


class FakePeers:
    """Echoes what the peers router hands its service."""

    def peers(self, code: str, window: str, segment_no: Optional[int]) -> dict:
        return {'code': code, 'window': window, 'segment': segment_no}

    async def search(self, query: str, window: str, limit: int) -> dict:
        return {'query': query, 'window': window, 'limit': limit}


class FakeFreshness:
    """Answers like the freshness service."""

    ANSWER = {
        'prices': {'as_of': '2026-10-08', 'last_run_at': '2026-10-08T18:30:00+09:00',
                   'last_run_status': 'ok', 'stale': False, 'note': None},
        'reports': {'latest_at': '2026-10-08T09:12:00+09:00', 'stale': False},
        'checked_at': '2026-10-08T20:00:00+09:00',
    }

    def freshness(self) -> dict:
        return self.ANSWER
