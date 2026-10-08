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
