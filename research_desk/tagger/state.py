"""LangGraph row-graph state (v2).

TypedDict with all keys total=False — each node sets only the keys it owns.
v2 변경 (rev-7):
- canonicalize/validate 단계 키 제거 (해당 노드 삭제)
- enrich 출력 → resolve_krx로 통합 + krx_lookup_skipped/krx_entries/krx_name_code_mismatch 추가
- oos_reason Literal에 ir_self 추가
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from typing_extensions import Literal, TypedDict

from research_desk.domain.stocks import StockEntry
from research_desk.tagger.llm_schemas import LLMExtraction


class RowState(TypedDict, total=False):
    # Input (populated at claim time)
    id: int
    file_path: str
    file_name: str
    sent_at: datetime
    caption: Optional[str]
    chat_username: str
    worker_id: str
    model: str

    # extract_pdf output
    pdf_text: str
    pages_used: list[int]
    pdf_unreadable: bool

    # llm_extract output
    llm_raw: Optional[LLMExtraction]
    llm_refusal: Optional[str]

    # oos_gate / mark_oos_reason output
    is_oos: bool
    oos_reason: Optional[Literal["foreign", "fund", "digital", "private", "ir_self"]]

    # resolve_krx output (v1 canonicalize+validate+enrich 통합)
    krx_lookup_skipped: bool       # 산업/전략·시황은 True
    krx_matched: bool              # 매칭 entry가 1개 이상 존재
    krx_entries: list[StockEntry]  # 단일종목=0~1, 섹터=0~N, 산업/전략·시황=[]
    krx_name_code_mismatch: bool   # 단일종목 + stock_code 매칭이지만 entry.name이 raw에 없음
    stock_codes_final: list[str]
    company_names_final: list[str]
    sectors_major_final: list[str]
    sectors_minor_final: list[str]
    products_final: list[str]
    published_at_final: Optional[date]
    used_sent_at_fallback: bool

    # decide_status / status_oos / status_unreadable output
    tagging_status: Literal["auto", "review_needed"]
    tagging_confidence: Literal["high", "medium", "low"]
    tagging_notes: Optional[str]
