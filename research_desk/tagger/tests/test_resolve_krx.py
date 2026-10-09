"""Test resolve_krx — v2 type-aware KRX lookup."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from research_desk.tagger.llm_schemas import OOSSignals
from research_desk.tagger.nodes.resolve_krx import resolve_krx
from research_desk.tagger.tests.conftest import make_llm_extraction


def _state(raw, sent_at_iso="2026-05-01T00:00:00+00:00"):
    return {"llm_raw": raw, "sent_at": datetime.fromisoformat(sent_at_iso)}


def test_단일종목_stock_code_match(krx):
    raw = make_llm_extraction(
        report_type="단일종목",
        stock_codes_raw=["005930"],
        company_names_raw=["삼성전자"],
    )
    out = resolve_krx(_state(raw), krx=krx)
    assert out["krx_lookup_skipped"] is False
    assert out["krx_matched"] is True
    assert out["stock_codes_final"] == ["005930"]
    assert out["company_names_final"] == ["삼성전자"]
    assert out["sectors_major_final"]   # KRX entry has it
    assert out["krx_name_code_mismatch"] is False


def test_단일종목_name_fallback_when_code_missing(krx):
    raw = make_llm_extraction(
        report_type="단일종목",
        stock_codes_raw=[],
        company_names_raw=["삼성전자"],
    )
    out = resolve_krx(_state(raw), krx=krx)
    assert out["krx_matched"] is True
    assert out["stock_codes_final"] == ["005930"]
    # name_code_mismatch는 stock_code 매칭이 성공한 케이스에만 검사 — 여기는 False
    assert out["krx_name_code_mismatch"] is False


def test_단일종목_unmatched_returns_raw_fallback(krx):
    raw = make_llm_extraction(
        report_type="단일종목",
        stock_codes_raw=["999999"],     # invalid
        company_names_raw=["미상장IPO후보"],
    )
    out = resolve_krx(_state(raw), krx=krx)
    assert out["krx_matched"] is False
    assert out["stock_codes_final"] == []
    assert out["company_names_final"] == ["미상장IPO후보"]   # raw fallback
    assert out["sectors_major_final"] == []


def test_단일종목_name_code_mismatch_detected(krx):
    """stock_code → 삼성전자, 그러나 raw 회사명에 다른 회사가 있으면 mismatch."""
    raw = make_llm_extraction(
        report_type="단일종목",
        stock_codes_raw=["005930"],         # 삼성전자
        company_names_raw=["SK하이닉스"],     # mismatch
    )
    out = resolve_krx(_state(raw), krx=krx)
    assert out["krx_matched"] is True
    assert out["company_names_final"] == ["삼성전자"]   # KRX 정식
    assert out["krx_name_code_mismatch"] is True


def test_산업_skips_lookup(krx):
    raw = make_llm_extraction(
        report_type="산업",
        stock_codes_raw=[],
        company_names_raw=[],
    )
    out = resolve_krx(_state(raw), krx=krx)
    assert out["krx_lookup_skipped"] is True
    assert out["krx_matched"] is False
    assert out["krx_entries"] == []
    assert out["sectors_major_final"] == []


def test_전략시황_skips_lookup(krx):
    raw = make_llm_extraction(
        report_type="전략·시황",
        stock_codes_raw=[],
        company_names_raw=[],
    )
    out = resolve_krx(_state(raw), krx=krx)
    assert out["krx_lookup_skipped"] is True
    assert out["krx_matched"] is False


def test_섹터_aggregates_n_entries(krx):
    raw = make_llm_extraction(
        report_type="섹터",
        stock_codes_raw=["005930", "000660"],   # 삼성전자, SK하이닉스
        company_names_raw=[],
    )
    out = resolve_krx(_state(raw), krx=krx)
    assert out["krx_lookup_skipped"] is False
    assert out["krx_matched"] is True
    assert out["stock_codes_final"] == ["005930", "000660"]
    # 두 종목의 sectors_major union (둘 다 반도체일 수 있음 → 1개 또는 2개)
    assert len(out["sectors_major_final"]) >= 1


def test_섹터_zero_match_is_auto_ok(krx):
    """섹터에 stock_code/company_name이 없거나 모두 미매칭이어도 정상 (decide_status가 auto로 처리)."""
    raw = make_llm_extraction(
        report_type="섹터",
        stock_codes_raw=[],
        company_names_raw=[],
    )
    out = resolve_krx(_state(raw), krx=krx)
    assert out["krx_lookup_skipped"] is False
    assert out["krx_matched"] is False
    assert out["stock_codes_final"] == []


def test_published_at_uses_llm_value_when_present(krx):
    raw = make_llm_extraction(
        report_type="단일종목",
        stock_codes_raw=["005930"],
        published_at="2026-04-30",
    )
    out = resolve_krx(_state(raw), krx=krx)
    assert out["published_at_final"].isoformat() == "2026-04-30"
    assert out["used_sent_at_fallback"] is False


def test_published_at_falls_back_to_sent_at_kst(krx):
    raw = make_llm_extraction(
        report_type="단일종목",
        stock_codes_raw=["005930"],
        published_at=None,
    )
    # sent_at = 2026-05-01 23:00 UTC → 2026-05-02 KST
    out = resolve_krx(_state(raw, "2026-05-01T23:00:00+00:00"), krx=krx)
    assert out["published_at_final"].isoformat() == "2026-05-02"
    assert out["used_sent_at_fallback"] is True
