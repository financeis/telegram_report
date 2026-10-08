from datetime import date

import pytest

from research_desk.domain.reports import OOS_REASONS, oos_row_shape
from research_desk.tagger.nodes.write import _build_payload, write
from research_desk.tagger.sql import UPDATE_SQL
from research_desk.tagger.tests.conftest import make_llm_extraction


def _in_scope_state():
    """v2 in-scope state — uses *_final keys from resolve_krx, no topics/canon at state level."""
    return {
        "id": 100,
        "model": "gpt-5.4-mini",
        "is_oos": False,
        "llm_raw": make_llm_extraction(),
        "stock_codes_final": ["005930"],
        "company_names_final": ["삼성전자"],
        "sectors_major_final": ["반도체"],
        "sectors_minor_final": ["메모리반도체"],
        "products_final": ["DRAM"],
        "published_at_final": date(2026, 5, 1),
        "tagging_status": "auto",
        "tagging_confidence": "high",
        "tagging_notes": None,
    }


@pytest.mark.asyncio
async def test_in_scope_write_calls_update(mock_supabase):
    state = _in_scope_state()
    await write(state, sb=mock_supabase, dry_run=False, taxonomy_version="KRX@2026-05-08")
    assert len(mock_supabase.executed) == 1
    sql, args = mock_supabase.executed[0]
    assert sql == UPDATE_SQL
    assert len(args) == 19
    # id is first arg
    assert args[0] == 100
    # report_type from llm_raw (not 기타)
    assert args[2] == "단일종목"
    # publisher canonical (from raw, not state)
    assert args[3] == "키움증권"


@pytest.mark.asyncio
async def test_dry_run_does_not_write(mock_supabase):
    state = _in_scope_state()
    await write(state, sb=mock_supabase, dry_run=True, taxonomy_version="KRX@2026-05-08")
    assert mock_supabase.executed == []


@pytest.mark.asyncio
async def test_oos_write_preserves_llm_classification(mock_supabase):
    """v2: OOS는 LLM 분류 그대로 ('기타' 강제 안 함). 분류 본체만 비움."""
    state = _in_scope_state()
    state.update({
        "is_oos": True,
        "oos_reason": "foreign",
        "tagging_confidence": "high",
    })
    await write(state, sb=mock_supabase, dry_run=False, taxonomy_version="KRX@2026-05-08")
    sql, args = mock_supabase.executed[0]
    assert len(args) == 19
    assert args[2] == "단일종목"           # report_type preserved (NOT '기타')
    assert args[3] == "키움증권"            # publisher preserved
    assert args[4] == "broker"              # publisher_type preserved
    assert args[7] == []                    # stock_codes empty (classification body)
    assert args[8] == []                    # company_names empty
    assert args[9] == ["005930"]           # stock_codes_raw audit preserved
    assert args[10] == ["삼성전자"]         # company_names_raw audit preserved
    assert args[11] == []                   # sectors_major empty
    assert args[12] == []                   # sectors_minor empty
    assert args[13] == []                   # products empty
    assert args[14] == "foreign"            # out_of_scope_reason


@pytest.mark.asyncio
async def test_unreadable_write_has_null_report_type(mock_supabase):
    state = _in_scope_state()
    state.update({
        "is_oos": False,
        "tagging_status": "review_needed",
        "tagging_confidence": "low",
        "tagging_notes": "first_page_unreadable",
        "llm_raw": None,   # llm never returned anything usable
    })
    await write(state, sb=mock_supabase, dry_run=False, taxonomy_version="KRX@2026-05-08")
    sql, args = mock_supabase.executed[0]
    assert len(args) == 19
    assert args[2] is None              # report_type NULL
    assert args[15] == "review_needed"  # tagging_status shifted to $16 (idx 15)


def test_oos_payload_preserves_report_type():
    """v2: OOS는 LLM 분류 그대로 ('기타' 강제 안 함)."""
    raw = make_llm_extraction(
        report_type="단일종목",
        publisher_canon="키움증권",
        publisher_type="broker",
        stock_codes_raw=["TSLA01"],
        company_names_raw=["Tesla"],
    )
    state = {
        "id": 1, "is_oos": True, "oos_reason": "foreign",
        "llm_raw": raw,
        "tagging_status": "auto", "tagging_confidence": "high",
        "tagging_notes": None,
    }
    out = _build_payload(state, taxonomy_version="KRX@test")
    assert len(out) == 19
    # $3 report_type — LLM 분류 그대로
    assert out[2] == "단일종목"
    # $4 publisher — LLM 출력 그대로
    assert out[3] == "키움증권"
    # $10 stock_codes_raw (audit)
    assert out[9] == ["TSLA01"]
    # $11 company_names_raw (audit)
    assert out[10] == ["Tesla"]
    # $15 out_of_scope_reason
    assert out[14] == "foreign"


def test_in_scope_payload_19_args():
    """v2 in-scope payload는 19-arg, raw audit 항상 포함."""
    raw = make_llm_extraction(
        report_type="단일종목",
        publisher_canon="키움증권",
        publisher_type="broker",
        stock_codes_raw=["005930"],
        company_names_raw=["삼성전자"],
    )
    state = {
        "id": 7, "is_oos": False,
        "llm_raw": raw,
        "published_at_final": date(2026, 5, 1),
        "stock_codes_final": ["005930"],
        "company_names_final": ["삼성전자"],
        "sectors_major_final": ["전기전자"],
        "sectors_minor_final": ["반도체"],
        "products_final": ["DRAM", "NAND"],
        "tagging_status": "auto", "tagging_confidence": "high",
        "tagging_notes": None,
    }
    out = _build_payload(state, taxonomy_version="KRX@test")
    assert len(out) == 19
    assert out[2] == "단일종목"
    assert out[7] == ["005930"]
    assert out[8] == ["삼성전자"]
    assert out[9] == ["005930"]   # raw audit
    assert out[10] == ["삼성전자"]
    assert out[11] == ["전기전자"]
    assert out[14] is None        # OOS reason null for in-scope


def test_unreadable_payload_19_args():
    """v2 unreadable payload는 19-arg, 모두 None/빈 배열."""
    state = {
        "id": 42, "is_oos": False,
        "llm_raw": None,
        "tagging_status": "review_needed",
        "tagging_confidence": "low",
        "tagging_notes": "first_page_unreadable",
    }
    out = _build_payload(state, taxonomy_version="KRX@test")
    assert len(out) == 19
    assert out[0] == 42
    assert out[2] is None              # report_type NULL
    assert out[9] == []                # stock_codes_raw empty
    assert out[10] == []               # company_names_raw empty
    assert out[14] is None             # out_of_scope_reason NULL
    assert out[15] == "review_needed"
    assert out[18] == "KRX@test"


# ── the OOS branch is the shared out-of-scope row shape (spec §4, §9.2) ─────

PAYLOAD_COLUMNS = [
    "id", "published_at", "report_type", "publisher", "publisher_type", "analysts", "title",
    "stock_codes", "company_names", "stock_codes_raw", "company_names_raw",
    "sectors_major", "sectors_minor", "products", "out_of_scope_reason",
    "tagging_status", "tagging_confidence", "tagging_notes", "taxonomy_version",
]


@pytest.mark.parametrize("reason", OOS_REASONS)
@pytest.mark.parametrize("raw", [
    None,
    make_llm_extraction(),
    make_llm_extraction(report_type="IR자료", publisher_canon="해당기업", publisher_type="other",
                        title=None, analysts=[], stock_codes_raw=[], company_names_raw=["X사"]),
])
def test_oos_payload_is_the_shared_out_of_scope_row_shape(reason, raw):
    state = {
        "id": 3, "is_oos": True, "oos_reason": reason, "llm_raw": raw,
        # in-scope values the OOS row must not pick up
        "published_at_final": date(2026, 5, 1), "stock_codes_final": ["005930"],
        "company_names_final": ["삼성전자"], "sectors_major_final": ["반도체"],
        "sectors_minor_final": ["메모리반도체"], "products_final": ["DRAM"],
        "tagging_status": "auto", "tagging_confidence": "high", "tagging_notes": None,
    }
    payload = dict(zip(PAYLOAD_COLUMNS, _build_payload(state, taxonomy_version="KRX@test")))
    llm_fields = None if raw is None else {
        "report_type": raw.report_type, "publisher": raw.publisher_canon,
        "publisher_type": raw.publisher_type, "analysts": raw.analysts, "title": raw.title,
        "stock_codes_raw": raw.stock_codes_raw, "company_names_raw": raw.company_names_raw,
    }
    shape = oos_row_shape(llm_fields, reason)
    assert {column: payload[column] for column in shape} == shape
    assert (payload["id"], payload["tagging_status"], payload["tagging_confidence"],
            payload["tagging_notes"], payload["taxonomy_version"]) == (3, "auto", "high", None, "KRX@test")
