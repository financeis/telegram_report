from research_desk.tagger.nodes.status_unreadable import status_unreadable


def test_pdf_unreadable_first_page_unreadable():
    state = {"pdf_unreadable": True, "llm_refusal": None}
    out = status_unreadable(state)
    assert out["tagging_status"] == "review_needed"
    assert out["tagging_confidence"] == "low"
    assert out["tagging_notes"] == "first_page_unreadable"


def test_llm_refusal_recorded_with_reason():
    state = {"pdf_unreadable": False, "llm_refusal": "policy violation"}
    out = status_unreadable(state)
    assert out["tagging_status"] == "review_needed"
    assert out["tagging_confidence"] == "low"
    assert out["tagging_notes"] == "llm_refusal:policy violation"


def test_pdf_unreadable_takes_priority_over_refusal():
    # If both flags set, prefer first_page_unreadable label
    state = {"pdf_unreadable": True, "llm_refusal": "x"}
    out = status_unreadable(state)
    assert out["tagging_notes"] == "first_page_unreadable"


# ── Rules applied last: page_image (spec §3.2, §3.5) ─────────────────────────

def test_a_refused_picture_row_is_a_refusal_with_the_page_image_note():
    out = status_unreadable({"pdf_unreadable": False, "page_image": True,
                             "llm_refusal": "cannot read"})
    assert out == {
        "tagging_status": "review_needed",
        "tagging_confidence": "low",
        "tagging_notes": "llm_refusal:cannot read;page_image",
    }


def test_an_empty_answer_to_a_picture_is_an_empty_refusal_with_the_page_image_note():
    out = status_unreadable({"pdf_unreadable": False, "page_image": True, "llm_raw": None})
    assert out == {
        "tagging_status": "review_needed",
        "tagging_confidence": "low",
        "tagging_notes": "llm_refusal:;page_image",
    }


def test_a_picture_the_model_cannot_take_stays_first_page_unreadable():
    """page_image_unsupported is counted in the report but adds no note."""
    out = status_unreadable({"pdf_unreadable": True, "page_image_unsupported": True,
                             "llm_raw": None})
    assert out == {
        "tagging_status": "review_needed",
        "tagging_confidence": "low",
        "tagging_notes": "first_page_unreadable",
    }


def test_unmarked_unreadable_rows_are_unchanged():
    assert status_unreadable({"pdf_unreadable": False, "llm_refusal": "x",
                              "page_image": False, "publisher_suspect": None}) == {
        "tagging_status": "review_needed",
        "tagging_confidence": "low",
        "tagging_notes": "llm_refusal:x",
    }
