"""status_oos node: OOS rows get auto status + reason-derived confidence (v2).

Then the rules applied last (decide_status.apply_final_rules): a row read from the
page picture or with a suspect publisher is at most medium and carries those notes.
"""
from research_desk.tagger.nodes.decide_status import apply_final_rules
from research_desk.tagger.state import RowState


def status_oos(state: RowState) -> dict:
    reason = state["oos_reason"]
    confidence = "high" if reason in ("foreign", "fund", "digital", "ir_self") else "medium"
    return {
        "is_oos": True,
        **apply_final_rules(state, status="auto", confidence=confidence, note=None),
    }
