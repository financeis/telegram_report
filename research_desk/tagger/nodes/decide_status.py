"""decide_status node (v2 simplified).

review_needed 트리거 4종:
  - pdf_unreadable
  - llm_refusal
  - 단일종목 + KRX unmatched (IPO pending or unknown)
  - type_indeterminate (report_type='기타' + self_confidence='low')

auto/medium 신호 (high가 아닌 케이스):
  - used_fallback (sent_at fallback 또는 multi-page)
  - krx_name_code_mismatch (단일종목에서 stock_code 매칭이지만 raw 회사명 mismatch)

Every status node (this one, status_oos, status_unreadable) ends with ``apply_final_rules``:
the page-picture and suspect-publisher marks, applied after the table above.
"""
from __future__ import annotations

from typing import Optional

from research_desk.tagger.state import RowState


def apply_final_rules(state: RowState, *, status: str, confidence: str,
                      note: Optional[str]) -> dict:
    """The row's status, confidence and notes once the rules applied last are in.

    A row read from the page picture (``page_image``) or with a suspect publisher
    (``publisher_suspect``) is at most ``medium``: ``high`` becomes ``medium``, ``low``
    stays ``low``. Its notes are the base ``note``, then ``page_image``, then
    ``publisher_suspect:<why>``, joined by ``;``. The status never changes (neither
    mark is a review reason), and a row with neither mark keeps its values exactly.
    """
    page_image = bool(state.get("page_image"))
    suspect = state.get("publisher_suspect")
    notes = [note] if note else []
    if page_image:
        notes.append("page_image")
    if suspect:
        notes.append(f"publisher_suspect:{suspect}")
    if (page_image or suspect) and confidence == "high":
        confidence = "medium"
    return {
        "tagging_status": status,
        "tagging_confidence": confidence,
        "tagging_notes": ";".join(notes) or None,
    }


def decide_status(state: RowState) -> dict:
    status, confidence, note = _base_status(state)
    return apply_final_rules(state, status=status, confidence=confidence, note=note)


def _base_status(state: RowState) -> tuple[str, str, Optional[str]]:
    """(status, confidence, note) from the first matching row of the table."""
    if state.get("pdf_unreadable"):
        return "review_needed", "low", "first_page_unreadable"
    if state.get("llm_refusal"):
        return "review_needed", "low", f"llm_refusal:{state['llm_refusal']}"

    raw = state.get("llm_raw")
    rt = raw.report_type if raw else None

    # 단일종목 + KRX 미매칭만 review_needed (IPO 예정/상장예정/오타 등)
    # 산업/전략·시황은 lookup_skipped=True로 매칭 의미 없음 → auto OK
    # 섹터는 0개 매칭이어도 정상 케이스 (peer reference 없는 산업·테마 리포트) → auto OK
    if rt == "단일종목" and not state.get("krx_matched"):
        return "review_needed", "low", "krx_unmatched_in_scope:ipo_pending_or_unknown"

    if rt == "기타" and raw is not None and raw.self_confidence == "low":
        return "review_needed", "low", "type_indeterminate"

    # in-scope auto. confidence는 폴백/mismatch 신호로 결정.
    used_fallback = (
        state.get("used_sent_at_fallback")
        or len(state.get("pages_used") or [1]) > 1
    )
    name_code_mismatch = bool(state.get("krx_name_code_mismatch"))
    confidence = "medium" if (used_fallback or name_code_mismatch) else "high"
    note = "krx_name_code_mismatch" if name_code_mismatch else None
    return "auto", confidence, note
