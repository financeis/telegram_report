"""Test decide_status — v2 simplified policy."""
import pytest

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


# ── Rules applied last: page_image / publisher_suspect (docs/business-rules.md) ───────────
# A row read from the page picture or with a suspect publisher is at most medium
# (low stays low) and gets "page_image" then "publisher_suspect:<why>" after its
# own note, joined by ";". The status never changes.

_AUTO_HIGH = {"krx_matched": True, "krx_lookup_skipped": False, "pages_used": [1],
              "used_sent_at_fallback": False, "krx_name_code_mismatch": False}

# (name, state, base status, base confidence, base note) — every row of the table.
_TABLE_ROWS = [
    ("pdf_unreadable", {"pdf_unreadable": True},
     "review_needed", "low", "first_page_unreadable"),
    ("llm_refusal", {"llm_refusal": "policy"},
     "review_needed", "low", "llm_refusal:policy"),
    ("krx_unmatched", {"report_type": "단일종목", "krx_matched": False, "krx_lookup_skipped": False},
     "review_needed", "low", "krx_unmatched_in_scope:ipo_pending_or_unknown"),
    ("type_indeterminate", {"report_type": "기타", "self_confidence": "low", "krx_matched": False,
                            "krx_lookup_skipped": False},
     "review_needed", "low", "type_indeterminate"),
    ("auto_high", {"report_type": "단일종목", **_AUTO_HIGH},
     "auto", "high", None),
    ("auto_fallback", {"report_type": "단일종목", **_AUTO_HIGH, "used_sent_at_fallback": True},
     "auto", "medium", None),
    ("auto_two_pages", {"report_type": "산업", **_AUTO_HIGH, "krx_lookup_skipped": True,
                        "pages_used": [1, 2]},
     "auto", "medium", None),
    ("auto_name_mismatch", {"report_type": "단일종목", **_AUTO_HIGH, "krx_name_code_mismatch": True},
     "auto", "medium", "krx_name_code_mismatch"),
]


def _table_state(spec: dict) -> dict:
    spec = dict(spec)
    llm_fields = {k: spec.pop(k) for k in ("report_type", "self_confidence") if k in spec}
    if llm_fields:
        spec["llm_raw"] = make_llm_extraction(**llm_fields)
    return spec


@pytest.mark.parametrize("suspect", [None, "unknown", "filename_mismatch"])
@pytest.mark.parametrize("page_image", [False, True])
@pytest.mark.parametrize("name,spec,status,confidence,note", _TABLE_ROWS,
                         ids=[r[0] for r in _TABLE_ROWS])
def test_final_rules_on_every_table_row(name, spec, status, confidence, note, page_image, suspect):
    state = _table_state(spec)
    if page_image:
        state["page_image"] = True
    if suspect:
        state["publisher_suspect"] = suspect

    out = decide_status(state)

    marked = page_image or suspect is not None
    expected_conf = "medium" if (marked and confidence == "high") else confidence
    expected_notes = ";".join(n for n in (
        note,
        "page_image" if page_image else None,
        f"publisher_suspect:{suspect}" if suspect else None,
    ) if n) or None
    assert out == {"tagging_status": status, "tagging_confidence": expected_conf,
                   "tagging_notes": expected_notes}


def test_unmarked_rows_are_unchanged_letter_for_letter():
    """page_image False and publisher_suspect None (as llm_extract leaves them) change nothing."""
    state = _table_state({"report_type": "단일종목", **_AUTO_HIGH})
    state.update(page_image=False, publisher_suspect=None, publisher_final="키움증권")
    assert decide_status(state) == {"tagging_status": "auto", "tagging_confidence": "high",
                                    "tagging_notes": None}


def test_page_image_alone_caps_an_auto_row_to_medium():
    state = _table_state({"report_type": "단일종목", **_AUTO_HIGH})
    state["page_image"] = True
    assert decide_status(state) == {"tagging_status": "auto", "tagging_confidence": "medium",
                                    "tagging_notes": "page_image"}


def test_publisher_suspect_alone_caps_an_auto_row_to_medium():
    state = _table_state({"report_type": "섹터", **_AUTO_HIGH})
    state["publisher_suspect"] = "filename_mismatch"
    assert decide_status(state) == {"tagging_status": "auto", "tagging_confidence": "medium",
                                    "tagging_notes": "publisher_suspect:filename_mismatch"}


def test_notes_order_base_then_page_image_then_suspect():
    state = _table_state({"report_type": "단일종목", **_AUTO_HIGH, "krx_name_code_mismatch": True})
    state.update(page_image=True, publisher_suspect="unknown")
    out = decide_status(state)
    assert out["tagging_notes"] == "krx_name_code_mismatch;page_image;publisher_suspect:unknown"
    assert out["tagging_confidence"] == "medium"


def test_a_review_row_keeps_its_status_and_low_with_the_marks_appended():
    """The spec's example: a mark never changes the status, and review rows stay low."""
    state = _table_state({"report_type": "단일종목", "krx_matched": False, "krx_lookup_skipped": False})
    state.update(page_image=True, publisher_suspect="unknown")
    assert decide_status(state) == {
        "tagging_status": "review_needed",
        "tagging_confidence": "low",
        "tagging_notes": "krx_unmatched_in_scope:ipo_pending_or_unknown;page_image;publisher_suspect:unknown",
    }


def test_a_refused_picture_row_keeps_the_refusal_first():
    out = decide_status({"llm_refusal": "cannot read", "page_image": True})
    assert out == {"tagging_status": "review_needed", "tagging_confidence": "low",
                   "tagging_notes": "llm_refusal:cannot read;page_image"}


def test_publisher_suspect_is_not_a_review_reason():
    """A suspect mark on an otherwise-auto row keeps it auto."""
    state = _table_state({"report_type": "단일종목", **_AUTO_HIGH})
    state["publisher_suspect"] = "unknown"
    assert decide_status(state)["tagging_status"] == "auto"
