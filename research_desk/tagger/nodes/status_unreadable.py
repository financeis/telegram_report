"""status_unreadable node: pdf_unreadable or llm_refusal → review_needed/low."""
from research_desk.tagger.state import RowState


def status_unreadable(state: RowState) -> dict:
    if state.get("pdf_unreadable"):
        notes = "first_page_unreadable"
    else:
        reason = state.get("llm_refusal") or ""
        notes = f"llm_refusal:{reason}"
    return {
        "tagging_status": "review_needed",
        "tagging_confidence": "low",
        "tagging_notes": notes,
    }
