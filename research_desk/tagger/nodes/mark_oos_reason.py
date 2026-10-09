"""mark_oos_reason node: sets is_oos + oos_reason from LLM signals + IR자료 (v2).

oos_gate (routing function) ensures we only enter this node when:
  - report_type == 'IR자료' (자동 OOS ir_self), or
  - one of foreign/fund/digital signals is true, or
  - private_company_likely + KRX-unmatched (no IPO exception in v2).
"""
from __future__ import annotations

from research_desk.tagger.state import RowState


def mark_oos_reason(state: RowState) -> dict:
    raw = state["llm_raw"]
    # IR자료 우선 — 사용자 의도 (분석 타겟 외)
    if raw.report_type == "IR자료":
        return {"is_oos": True, "oos_reason": "ir_self"}
    sig = raw.oos_signals
    if sig.foreign_primary_coverage:
        return {"is_oos": True, "oos_reason": "foreign"}
    if sig.etf_or_fund:
        return {"is_oos": True, "oos_reason": "fund"}
    if sig.digital_asset:
        return {"is_oos": True, "oos_reason": "digital"}
    return {"is_oos": True, "oos_reason": "private"}
