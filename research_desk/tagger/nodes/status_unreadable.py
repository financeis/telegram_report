"""status_unreadable node: pdf_unreadable or llm_refusal → review_needed/low.

Then the rules applied last (decide_status.apply_final_rules): a refused (or empty)
answer to the page picture keeps its llm_refusal note and gets page_image after it.
A picture the model could not take (page_image_unsupported) adds no note.
"""
from research_desk.tagger.nodes.decide_status import apply_final_rules
from research_desk.tagger.state import RowState


def status_unreadable(state: RowState) -> dict:
    if state.get("pdf_unreadable"):
        notes = "first_page_unreadable"
    else:
        reason = state.get("llm_refusal") or ""
        notes = f"llm_refusal:{reason}"
    return apply_final_rules(state, status="review_needed", confidence="low", note=notes)
