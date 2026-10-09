"""status_oos node: OOS rows get auto status + reason-derived confidence (v2)."""
from research_desk.tagger.state import RowState


def status_oos(state: RowState) -> dict:
    reason = state["oos_reason"]
    confidence = "high" if reason in ("foreign", "fund", "digital", "ir_self") else "medium"
    return {
        "is_oos": True,
        "tagging_status": "auto",
        "tagging_confidence": confidence,
        "tagging_notes": None,
    }
