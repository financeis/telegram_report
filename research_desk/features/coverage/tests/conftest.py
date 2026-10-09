"""Shared fixtures for the coverage tests.

``inscope_df`` and ``with_oos_df`` are ported from langgraph_tagger/analytics/tests/conftest.py
(the fixtures test_aggregate.py used), unchanged.
"""
from __future__ import annotations

import pandas as pd
import pytest


@pytest.fixture
def inscope_df() -> pd.DataFrame:
    """Small DataFrame representing in-scope rows for aggregate tests.

    Every row has both published_at and sent_at. effective_date will be
    derived from these by logic._ensure_effective_date.
    """
    return pd.DataFrame([
        # 단일종목 + 단일 stock_code
        {'published_at': '2026-05-01', 'sent_at': '2026-05-01T08:00:00+00:00',
         'report_type': '단일종목', 'publisher': '메리츠',
         'stock_codes': ['005930'], 'sectors_major': ['반도체'],
         'sectors_minor': ['메모리반도체'], 'products': ['DRAM'],
         'out_of_scope_reason': None},
        # 단일종목 + 같은 종목, 다른 발행처
        {'published_at': '2026-05-02', 'sent_at': '2026-05-02T08:00:00+00:00',
         'report_type': '단일종목', 'publisher': '키움',
         'stock_codes': ['005930'], 'sectors_major': ['반도체'],
         'sectors_minor': ['메모리반도체'], 'products': ['DRAM', 'NAND'],
         'out_of_scope_reason': None},
        # 단일종목 — 다른 종목
        {'published_at': '2026-05-03', 'sent_at': '2026-05-03T08:00:00+00:00',
         'report_type': '단일종목', 'publisher': '키움',
         'stock_codes': ['000660'], 'sectors_major': ['반도체'],
         'sectors_minor': ['메모리반도체'], 'products': ['DRAM'],
         'out_of_scope_reason': None},
        # 섹터 — multi-stock
        {'published_at': '2026-05-04', 'sent_at': '2026-05-04T08:00:00+00:00',
         'report_type': '섹터', 'publisher': 'NH',
         'stock_codes': ['005930', '000660'], 'sectors_major': ['반도체'],
         'sectors_minor': ['메모리반도체'], 'products': ['DRAM'],
         'out_of_scope_reason': None},
        # 산업 — 빈 stock_codes/sectors
        {'published_at': '2026-05-05', 'sent_at': '2026-05-05T08:00:00+00:00',
         'report_type': '산업', 'publisher': '메리츠',
         'stock_codes': [], 'sectors_major': [],
         'sectors_minor': [], 'products': [],
         'out_of_scope_reason': None},
        # 2차전지 — 다른 sector
        {'published_at': '2026-05-06', 'sent_at': '2026-05-06T08:00:00+00:00',
         'report_type': '단일종목', 'publisher': 'NH',
         'stock_codes': ['373220'], 'sectors_major': ['2차전지'],
         'sectors_minor': ['셀'], 'products': ['리튬이온배터리'],
         'out_of_scope_reason': None},
    ])


@pytest.fixture
def with_oos_df(inscope_df: pd.DataFrame) -> pd.DataFrame:
    """inscope + 2 OOS rows. OOS rows mirror writer semantics: published_at
    is None (writer sets it NULL for OOS), so effective_date must fall back
    to sent_at. This is the test case that exercises the fallback path."""
    extra = pd.DataFrame([
        {'published_at': None, 'sent_at': '2026-05-07T08:00:00+00:00',
         'report_type': 'IR자료', 'publisher': '한국기업평가',
         'stock_codes': ['005930'], 'sectors_major': [],
         'sectors_minor': [], 'products': [],
         'out_of_scope_reason': 'ir_self'},
        {'published_at': None, 'sent_at': '2026-05-08T08:00:00+00:00',
         'report_type': '단일종목', 'publisher': 'Reuters',
         'stock_codes': ['005930'], 'sectors_major': [],
         'sectors_minor': [], 'products': [],
         'out_of_scope_reason': 'foreign'},
    ])
    return pd.concat([inscope_df, extra], ignore_index=True)
