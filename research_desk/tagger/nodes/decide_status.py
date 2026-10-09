"""decide_status node (v2 simplified).

review_needed 트리거 4종:
  - pdf_unreadable
  - llm_refusal
  - 단일종목 + KRX unmatched (IPO pending or unknown)
  - type_indeterminate (report_type='기타' + self_confidence='low')

auto/medium 신호 (high가 아닌 케이스):
  - used_fallback (sent_at fallback 또는 multi-page)
  - krx_name_code_mismatch (단일종목에서 stock_code 매칭이지만 raw 회사명 mismatch)
"""
from __future__ import annotations

from research_desk.tagger.state import RowState


def decide_status(state: RowState) -> dict:
    if state.get("pdf_unreadable"):
        return {"tagging_status": "review_needed", "tagging_confidence": "low",
                "tagging_notes": "first_page_unreadable"}
    if state.get("llm_refusal"):
        return {"tagging_status": "review_needed", "tagging_confidence": "low",
                "tagging_notes": f"llm_refusal:{state['llm_refusal']}"}

    raw = state.get("llm_raw")
    rt = raw.report_type if raw else None

    # 단일종목 + KRX 미매칭만 review_needed (IPO 예정/상장예정/오타 등)
    # 산업/전략·시황은 lookup_skipped=True로 매칭 의미 없음 → auto OK
    # 섹터는 0개 매칭이어도 정상 케이스 (peer reference 없는 산업·테마 리포트) → auto OK
    if rt == "단일종목" and not state.get("krx_matched"):
        return {"tagging_status": "review_needed", "tagging_confidence": "low",
                "tagging_notes": "krx_unmatched_in_scope:ipo_pending_or_unknown"}

    if rt == "기타" and raw is not None and raw.self_confidence == "low":
        return {"tagging_status": "review_needed", "tagging_confidence": "low",
                "tagging_notes": "type_indeterminate"}

    # in-scope auto. confidence는 폴백/mismatch 신호로 결정.
    used_fallback = (
        state.get("used_sent_at_fallback")
        or len(state.get("pages_used") or [1]) > 1
    )
    name_code_mismatch = bool(state.get("krx_name_code_mismatch"))
    confidence = "medium" if (used_fallback or name_code_mismatch) else "high"
    notes = "krx_name_code_mismatch" if name_code_mismatch else None
    return {
        "tagging_status": "auto",
        "tagging_confidence": confidence,
        "tagging_notes": notes,
    }
