"""A small peers world for the web tests: a stock list file, the peers tables with vectors whose
cosines are chosen, a public build with simple percentile tables, report counts and price
snapshots, and an app with this feature's router and the web app's NotReady → 503 answer.

Vectors: the seed's company vector is the axis 0, its segment 1 (메모리, 98.1 %) the axis 1 and its
segment 0 (기타, 1.9 %) the axis 2; every other vector mixes those axes with weights = the wanted
cosines and fills the rest of its length on an axis of its own. The percentile tables put their
points on exact binary fractions.

Expected list for the seed (segment 1, the default): 111110 (segment 100), 032580 (99.75),
000660 (98.3), 777770 (97.4), 888880 (96.92), 444440 (96.2). Left out: the seed, 222220 (not in
the stock list), 555550 (failed profile), 666660 (a 2024 profile only), 333330 (below related).
"""
from __future__ import annotations

import csv
import math
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from research_desk.core.settings import NotReady
from research_desk.domain.stocks import HEADER
from research_desk.features import peers

from .fakes import DIMS, FakeSupabase

URL = 'https://peers-test.supabase.co'
KEY = 'service-key-peers-test'
FY, PV, MODEL = 2025, 'peer-profile@1.0', 'text-embedding-3-large'
SEED = '080220'

COMPANY_TABLE = [[95.0, 0.5], [98.0, 0.625], [99.5, 0.75], [100.0, 0.875]]
SEGMENT_TABLE = [[95.0, 0.55], [98.0, 0.65], [99.5, 0.8], [100.0, 0.9]]
TERM_TABLE = {
    'legacydram': {'display': 'Legacy DRAM', 'companies': 2},
    'mcp': {'display': 'MCP', 'companies': 3},
    'lpddr': {'display': 'LPDDR', 'companies': 4},
    'nor': {'display': 'NOR Flash', 'companies': 5},
    'dram': {'display': 'DRAM', 'companies': 9},
    '팹리스': {'display': '팹리스', 'companies': 30},
    'hbm': {'display': 'HBM', 'companies': 2},
    '모듈': {'display': '모듈', 'companies': 12},
    '기타': {'display': '기타', 'companies': 40},
}

# code, name, market, 산업명(대), 산업명(중), 주요제품
STOCKS = [
    (SEED, '제주반도체', 'KOSDAQ', '반도체', '비메모리_팹리스', 'DRAM, MCP'),
    ('032580', '피델릭스', 'KOSDAQ', '반도체', '비메모리_팹리스', 'DRAM, NOR'),
    ('000660', 'SK하이닉스', 'KOSPI', '반도체', '메모리', 'DRAM, HBM'),
    ('111110', '부문회사', 'KOSDAQ', '전자부품', '', '모듈'),
    ('333330', '약한회사', 'KOSDAQ', '반도체', '메모리', '기타'),
    ('444440', '지주회사', 'KOSPI', '지주', '지주회사', '지주'),
    ('555550', '실패회사', 'KOSDAQ', '반도체', '메모리', '기타'),
    ('666660', '작년회사', 'KOSDAQ', '반도체', '메모리', '기타'),
    ('777770', '정지회사', 'KOSDAQ', '반도체', '메모리', '기타'),
    ('888880', '주가없음', 'KOSDAQ GLOBAL', '반도체', '메모리', '기타'),
    ('999990', '부문없음', 'KOSDAQ', '반도체', '메모리', '기타'),
    ('123450', '임베딩없음', 'KOSDAQ', '반도체', '메모리', '기타'),
]


def write_stock_list(path, rows=STOCKS):
    with open(path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(HEADER)
        writer.writerows(rows)
    return path


# ── vectors ──────────────────────────────────────────────────────────────────

def vec(fill: int, **weights: float) -> list[float]:
    """A unit vector with cosine ``w`` to the axis named ``a<i>`` for each ``a<i>=w``; the rest
    of its length lies on the axis ``fill`` (10 or more, one per vector)."""
    assert fill >= 10
    vector = [0.0] * DIMS
    for name, weight in weights.items():
        vector[int(name[1:])] = weight
    rest = 1.0 - sum(w * w for w in weights.values())
    vector[fill] = math.sqrt(max(rest, 0.0))
    return vector


def axis(i: int) -> list[float]:
    vector = [0.0] * DIMS
    vector[i] = 1.0
    return vector


# ── the tables ───────────────────────────────────────────────────────────────

def build_row(build_id: int = 1, *, status: str = 'done', finished_at: str = '2026-10-01T09:00:00+00:00',
              company_quantiles=COMPANY_TABLE, segment_quantiles=SEGMENT_TABLE, term_table=TERM_TABLE,
              fiscal_year: int = FY, **extra) -> dict:
    return {'build_id': build_id, 'fiscal_year': fiscal_year, 'profile_version': PV, 'embed_model': MODEL,
            'embed_dims': DIMS, 'synonyms_version': 'sha', 'stock_list_version': 'KRX@2026-05-08',
            'status': status, 'eligible': 2400, 'profiled': 2350, 'failed': 50,
            'company_quantiles': company_quantiles, 'segment_quantiles': segment_quantiles,
            'term_table': term_table, 'started_at': '2026-10-01T00:00:00+00:00',
            'heartbeat_at': finished_at or '2026-10-01T00:00:00+00:00', 'finished_at': finished_at, **extra}


def profile(code: str, *, status: str = 'ok', fiscal_year: int = FY, terms=(), is_holding: bool = False,
            one_line: Optional[str] = None, niche: str = '', summary: str = '', keywords=()) -> dict:
    return {'fiscal_year': fiscal_year, 'profile_version': PV, 'stock_code': code, 'status': status,
            'corp_name': code, 'one_line': one_line if one_line is not None else f'{code} 틈새 업종',
            'is_holding': is_holding, 'is_financial': False, 'info_quality': '충분', 'terms': list(terms),
            'profile': {'niche_industry': niche or f'{code} 틈새 업종', 'summary': summary or f'{code} 요약',
                        'keywords': list(keywords), 'products': [], 'segments': []}}


def segment(code: str, seg_no: int, name: str, share: float, products=(), fiscal_year: int = FY) -> dict:
    return {'fiscal_year': fiscal_year, 'profile_version': PV, 'stock_code': code, 'seg_no': seg_no,
            'name': name, 'products': list(products), 'keywords': [], 'revenue_share_pct': share}


def company_embedding(code: str, vector, fiscal_year: int = FY, model: str = MODEL) -> dict:
    return {'fiscal_year': fiscal_year, 'profile_version': PV, 'stock_code': code, 'embed_model': model,
            'embedding': vector}


def segment_embedding(code: str, seg_no: int, vector, fiscal_year: int = FY) -> dict:
    return {'fiscal_year': fiscal_year, 'profile_version': PV, 'stock_code': code, 'seg_no': seg_no,
            'embed_model': MODEL, 'embedding': vector}


SEED_TERMS = ['legacydram', 'mcp', 'lpddr', 'dram', '팹리스', 'nor', '기타']


def make_db(builds: Optional[list[dict]] = None) -> FakeSupabase:
    profiles = [
        profile(SEED, terms=SEED_TERMS, niche='레거시 DRAM 팹리스', summary='레거시 DRAM과 MCP를 설계해 판다',
                keywords=['Legacy DRAM', 'MCP', 'LPDDR']),
        profile('032580', terms=['legacydram', 'mcp', 'lpddr', 'dram', '팹리스', 'nor']),
        profile('000660', terms=['dram', 'hbm']),
        profile('111110', terms=['모듈']),
        profile('333330', terms=['기타']),
        profile('444440', terms=['기타'], is_holding=True),
        profile('555550', status='failed'),
        profile('666660', fiscal_year=2024, terms=['dram']),
        profile('777770', terms=[]),
        profile('888880', terms=['dram']),
        profile('999990', terms=['dram']),
        profile('123450', terms=['dram']),
        profile('222220', terms=['legacydram']),       # not in the stock list
    ]
    segments = [
        segment(SEED, 0, '기타', 1.9, ['기타']),
        segment(SEED, 1, '메모리', 98.1, ['Legacy DRAM', 'MCP']),
        segment('032580', 0, '메모리', 60.0), segment('032580', 1, '기타', -1.0), segment('032580', 2, '모듈', 80.0),
        segment('000660', 0, 'DRAM', 70.0),
        segment('111110', 0, '기타 부품', 30.0), segment('111110', 1, '메모리 모듈', 70.0),
        segment('333330', 0, '기타', 100.0),
        segment('123450', 0, '메모리', 100.0),
    ]
    company_embeddings = [
        company_embedding(SEED, axis(0)),
        company_embedding('032580', vec(10, a0=0.8)),
        company_embedding('000660', vec(11, a0=0.65)),
        company_embedding('111110', vec(12, a0=0.3)),
        company_embedding('333330', vec(13, a0=0.45)),
        company_embedding('444440', vec(14, a0=0.55)),
        company_embedding('555550', vec(15, a0=0.98)),
        company_embedding('666660', vec(16, a0=0.97), fiscal_year=2024),
        company_embedding('777770', vec(17, a0=0.6, a3=0.7)),
        company_embedding('888880', vec(18, a0=0.58)),
        company_embedding('999990', axis(3)),          # a seed without segments: 777770 is its peer
        company_embedding('222220', vec(19, a0=0.99)),
    ]
    segment_embeddings = [
        segment_embedding(SEED, 0, axis(2)),
        segment_embedding(SEED, 1, axis(1)),
        segment_embedding('032580', 0, vec(20, a1=0.85)),
        segment_embedding('032580', 1, vec(21, a1=0.2, a2=0.6)),
        segment_embedding('032580', 2, vec(22, a1=0.7)),
        segment_embedding('000660', 0, vec(23, a1=0.3)),
        segment_embedding('111110', 0, vec(24, a1=0.5, a2=0.85)),
        segment_embedding('111110', 1, vec(25, a1=0.95)),
        segment_embedding('333330', 0, vec(26, a1=0.5)),
    ]
    return FakeSupabase(company_profiles=profiles, company_segments=segments,
                        company_embeddings=company_embeddings, segment_embeddings=segment_embeddings,
                        peer_builds=builds if builds is not None else [build_row()])


# ── report counts and prices ─────────────────────────────────────────────────

def counts_record(label: str = 'none', stock_reports: int = 0) -> dict:
    return {'stock_reports': stock_reports, 'sector_mentions': 1, 'other_research': 0,
            'last_stock_report_date': '2026-09-30' if stock_reports else None,
            'brokers': 1 if stock_reports else 0, 'label': label}


LABELS = {SEED: 'covered', '000660': 'covered'}


class Counts:
    """Stand-in for ``coverage.report_counts``: every code, label from ``LABELS`` (else none)."""

    def __init__(self, labels=None) -> None:
        self.labels = dict(LABELS if labels is None else labels)
        self.calls: list[tuple[list[str], int]] = []

    def __call__(self, codes, days=365):
        codes = list(codes)
        self.calls.append((codes, days))
        return {code: counts_record(self.labels.get(code, 'none'),
                                    3 if self.labels.get(code, 'none') != 'none' else 0) for code in codes}


def snapshot(*, excess_1m=None, excess_1w=None, excess_3m=None, as_of='2026-10-08', traded=True, flags=(),
             avg=1_000_000_000, market='KOSDAQ') -> dict:
    return {'market': market, 'as_of': as_of, 'close': 12_340, 'market_cap': 123_400_000_000,
            'avg_value_20d': avg, 'traded': traded,
            'returns': {'1w': 1.5, '1m': 25.0, '3m': 50.0},
            'excess': {'1w': excess_1w, '1m': excess_1m, '3m': excess_3m}, 'flags': list(flags)}


SNAPSHOTS = {
    SEED: snapshot(excess_1w=5.0, excess_1m=20.0, excess_3m=40.0),
    '032580': snapshot(excess_1m=4.0, excess_3m=30.0),
    '000660': snapshot(excess_1m=12.0, market='KOSPI'),
    '111110': snapshot(excess_1m=6.0, avg=400_000_000),
    '444440': snapshot(excess_1m=1.0, as_of='2026-10-07', market='KOSPI'),
    '777770': snapshot(excess_1m=0.0, traded=False, flags=['halted']),
    '999990': snapshot(excess_1m=0.0),
    '123450': snapshot(excess_1m=0.0),
}


class Snapshots:
    """Stand-in for ``prices.snapshots``: the stored codes among those asked, in the order asked."""

    def __init__(self, table=None) -> None:
        self.table = dict(SNAPSHOTS if table is None else table)
        self.calls: list[list[str]] = []

    def __call__(self, codes):
        codes = list(codes)
        self.calls.append(codes)
        return {code: dict(self.table[code]) for code in codes if code in self.table}


# ── the app ──────────────────────────────────────────────────────────────────

def build_app(service: Any = None) -> FastAPI:
    """This feature's router (through the window's ``web_router()``), a health address, and the
    NotReady → 503 answer the web app adds. Without ``service`` the process-wide one is used."""
    app = FastAPI()
    app.include_router(peers.web_router())

    @app.get('/api/health')
    def health():
        return {'status': 'ok'}

    if service is not None:
        from research_desk.features.peers.service import get_service

        app.dependency_overrides[get_service] = lambda: service

    @app.exception_handler(NotReady)
    async def not_ready(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=503)

    return app
