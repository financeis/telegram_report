"""Test decide_status — v2 simplified policy."""
from research_desk.tagger.nodes.decide_status import decide_status
from research_desk.tagger.tests.conftest import make_llm_extraction


def test_pdf_unreadable_review_low():
    out = decide_status({"pdf_unreadable": True})
    assert out["tagging_status"] == "review_needed"
    assert out["tagging_confidence"] == "low"
    assert out["tagging_notes"] == "first_page_unreadable"


def test_llm_refusal_review_low():
    out = decide_status({"llm_refusal": "policy"})
    assert out["tagging_status"] == "review_needed"
    assert out["tagging_confidence"] == "low"
    assert out["tagging_notes"].startswith("llm_refusal:")


def test_단일종목_krx_unmatched_review_low():
    raw = make_llm_extraction(report_type="단일종목")
    out = decide_status({
        "llm_raw": raw,
        "krx_matched": False,
        "krx_lookup_skipped": False,
    })
    assert out["tagging_status"] == "review_needed"
    assert out["tagging_confidence"] == "low"
    assert out["tagging_notes"] == "krx_unmatched_in_scope:ipo_pending_or_unknown"


def test_산업_krx_skipped_auto_high():
    raw = make_llm_extraction(report_type="산업")
    out = decide_status({
        "llm_raw": raw,
        "krx_matched": False,
        "krx_lookup_skipped": True,
        "pages_used": [1],
        "used_sent_at_fallback": False,
    })
    assert out["tagging_status"] == "auto"
    assert out["tagging_confidence"] == "high"
    assert out["tagging_notes"] is None


def test_섹터_zero_krx_match_auto_high():
    raw = make_llm_extraction(report_type="섹터")
    out = decide_status({
        "llm_raw": raw,
        "krx_matched": False,
        "krx_lookup_skipped": False,
        "pages_used": [1],
        "used_sent_at_fallback": False,
    })
    # 섹터는 0 매칭이어도 review_needed로 보내지 않음
    assert out["tagging_status"] == "auto"


def test_단일종목_krx_matched_auto_high():
    raw = make_llm_extraction(report_type="단일종목")
    out = decide_status({
        "llm_raw": raw,
        "krx_matched": True,
        "krx_lookup_skipped": False,
        "pages_used": [1],
        "used_sent_at_fallback": False,
        "krx_name_code_mismatch": False,
    })
    assert out["tagging_status"] == "auto"
    assert out["tagging_confidence"] == "high"


def test_name_code_mismatch_downgrades_to_medium():
    raw = make_llm_extraction(report_type="단일종목")
    out = decide_status({
        "llm_raw": raw,
        "krx_matched": True,
        "krx_lookup_skipped": False,
        "pages_used": [1],
        "used_sent_at_fallback": False,
        "krx_name_code_mismatch": True,
    })
    assert out["tagging_status"] == "auto"
    assert out["tagging_confidence"] == "medium"
    assert out["tagging_notes"] == "krx_name_code_mismatch"


def test_used_fallback_downgrades_to_medium():
    raw = make_llm_extraction(report_type="단일종목")
    out = decide_status({
        "llm_raw": raw,
        "krx_matched": True,
        "krx_lookup_skipped": False,
        "pages_used": [1, 2],   # multi-page → fallback
        "used_sent_at_fallback": False,
        "krx_name_code_mismatch": False,
    })
    assert out["tagging_status"] == "auto"
    assert out["tagging_confidence"] == "medium"


def test_type_indeterminate_review_low():
    raw = make_llm_extraction(report_type="기타", self_confidence="low")
    out = decide_status({
        "llm_raw": raw,
        "krx_matched": False,
        "krx_lookup_skipped": False,
    })
    assert out["tagging_status"] == "review_needed"
    assert out["tagging_notes"] == "type_indeterminate"
