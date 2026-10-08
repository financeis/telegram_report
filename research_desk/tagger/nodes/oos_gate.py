"""oos_gate: 3-way LangGraph routing function (v2).

v2 변경 (rev-7):
- IR자료 분기 추가 → 자동 OOS ir_self
- "canonicalize" 라벨 → "resolve_krx"
- IPO 예외 제거 (IPO enum 자체가 사라짐 — 6종에 IPO 없음)

LangGraph 1.0 contract: routing functions for ``add_conditional_edges`` MUST
return a string label only and MUST NOT mutate state. State mutation for OOS
classification lives in ``mark_oos_reason``.
"""
from __future__ import annotations

from typing import Literal

from research_desk.domain.stocks import StockList
from research_desk.tagger.state import RowState


def oos_gate(state: RowState, *, krx: StockList) -> Literal[
    "mark_oos_reason", "status_unreadable", "resolve_krx"
]:
    if state.get("pdf_unreadable") or state.get("llm_refusal"):
        return "status_unreadable"

    raw = state.get("llm_raw")
    if raw is None:
        return "status_unreadable"

    # v2: IR자료 = 자동 OOS ir_self (mark_oos_reason에서 reason 결정)
    if raw.report_type == "IR자료":
        return "mark_oos_reason"

    sig = raw.oos_signals
    if sig.foreign_primary_coverage or sig.etf_or_fund or sig.digital_asset:
        return "mark_oos_reason"

    if sig.private_company_likely:
        # 명백한 비상장 컨텍스트: KRX 매칭 시 in-scope (예: 0008Z0 SPAC), 미매칭 시 OOS private.
        # IPO 예외는 v2에서 제거됨 (IPO enum 자체가 사라짐).
        if any(krx.validate_code(c) for c in raw.stock_codes_raw):
            return "resolve_krx"
        return "mark_oos_reason"

    return "resolve_krx"
