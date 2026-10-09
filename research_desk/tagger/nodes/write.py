"""write node (v2): build UPDATE payload and persist via SupabaseSQL.

v2 변경 (rev-7):
- topics 제거, stock_codes_raw/company_names_raw 추가 → 19-arg payload
- OOS row도 LLM의 report_type/publisher/title/analysts 보존 ('기타' 강제 안 함)

The OOS columns come from the shared out-of-scope row shape
(research_desk.domain.reports.oos_row_shape), the same rule manual review uses.
"""
from __future__ import annotations

from typing import Any, Optional

from research_desk.domain.reports import oos_row_shape
from research_desk.tagger.llm_schemas import LLMExtraction
from research_desk.tagger.sql import UPDATE_SQL
from research_desk.tagger.state import RowState


def _llm_columns(raw: Optional[LLMExtraction]) -> Optional[dict[str, Any]]:
    """The LLM's classification under the DB column names (publisher_canon → publisher)."""
    if raw is None:
        return None
    return {
        "report_type": raw.report_type,
        "publisher": raw.publisher_canon,
        "publisher_type": raw.publisher_type,
        "analysts": raw.analysts,
        "title": raw.title,
        "stock_codes_raw": raw.stock_codes_raw,
        "company_names_raw": raw.company_names_raw,
    }


def _build_payload(state: RowState, taxonomy_version: str) -> tuple:
    """Return UPDATE_SQL bind-arg tuple matching $1..$19 in tagger.sql.UPDATE_SQL."""
    raw = state.get("llm_raw")
    is_oos = bool(state.get("is_oos"))

    # OOS 케이스 — 분류 본체(stock_codes/company_names/sectors/products)는 비우되
    # report_type/publisher/title/analysts/raw audit는 LLM 출력 그대로 보존.
    if is_oos:
        shape = oos_row_shape(_llm_columns(raw), state["oos_reason"])
        return (
            state["id"],                      # $1
            shape["published_at"],            # $2 published_at (OOS는 null)
            shape["report_type"],             # $3 report_type — LLM 분류 그대로
            shape["publisher"],               # $4 publisher
            shape["publisher_type"],          # $5 publisher_type
            shape["analysts"],                # $6 analysts
            shape["title"],                   # $7 title
            shape["stock_codes"],             # $8 stock_codes (빈 배열)
            shape["company_names"],           # $9 company_names (빈 배열)
            shape["stock_codes_raw"],         # $10 stock_codes_raw (audit)
            shape["company_names_raw"],       # $11 company_names_raw (audit)
            shape["sectors_major"],           # $12 sectors_major (빈 배열)
            shape["sectors_minor"],           # $13 sectors_minor (빈 배열)
            shape["products"],                # $14 products (빈 배열)
            shape["out_of_scope_reason"],     # $15 out_of_scope_reason
            state["tagging_status"],          # $16
            state["tagging_confidence"],      # $17
            state.get("tagging_notes"),       # $18
            taxonomy_version,                 # $19
        )

    # 가독 실패 (raw 없음)
    if raw is None:
        return (
            state["id"], None, None, None, None, [], None,
            [], [], [], [], [], [], [],
            None, state["tagging_status"], state["tagging_confidence"],
            state.get("tagging_notes"), taxonomy_version,
        )

    # in-scope
    return (
        state["id"],
        state["published_at_final"],
        raw.report_type,
        raw.publisher_canon,
        raw.publisher_type,
        list(raw.analysts),
        raw.title,
        list(state.get("stock_codes_final", [])),
        list(state.get("company_names_final", [])),
        list(raw.stock_codes_raw),                  # audit 항상 보존
        list(raw.company_names_raw),                # audit 항상 보존
        list(state.get("sectors_major_final", [])),
        list(state.get("sectors_minor_final", [])),
        list(state.get("products_final", [])),
        None,                                        # out_of_scope_reason NULL
        state["tagging_status"],
        state["tagging_confidence"],
        state.get("tagging_notes"),
        taxonomy_version,
    )


async def write(state: RowState, *, sb, dry_run: bool, taxonomy_version: str) -> dict:
    if dry_run:
        return {}
    args = _build_payload(state, taxonomy_version)
    await sb.execute(UPDATE_SQL, args)
    return {}
