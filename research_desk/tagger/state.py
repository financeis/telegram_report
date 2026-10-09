"""LangGraph row-graph state (v2).

TypedDict with all keys total=False — each node sets only the keys it owns.
v2 변경 (rev-7):
- canonicalize/validate 단계 키 제거 (해당 노드 삭제)
- enrich 출력 → resolve_krx로 통합 + krx_lookup_skipped/krx_entries/krx_name_code_mismatch 추가
- oos_reason Literal에 ir_self 추가
- llm_extract가 발행처 확인 결과(publisher_final / publisher_type_final / publisher_suspect)를
  AI 결과가 있는 모든 행(분석 대상 외 포함)에 남긴다. 못 읽음·거부 행에는 없다.
- 1~3쪽에 글자가 없으면 extract_pdf가 1쪽 그림(page_images)을 만들고, llm_extract가 그 그림으로
  AI를 부르면 page_image, 모델이 그림을 못 받으면 page_image_unsupported를 남긴다.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from typing_extensions import Literal, TypedDict

from research_desk.domain.stocks import StockEntry
from research_desk.tagger.llm_schemas import PUBLISHER_TYPES, LLMExtraction

# Why a row's publisher is suspect (see nodes/llm_extract.publisher_checks).
PublisherSuspect = Literal["filename_mismatch", "unknown"]


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
    pdf_unreadable: bool           # no text on pages 1–3 (llm_extract clears it for a row read from a picture)
    # extract_pdf output, only when pages 1–3 have no text:
    page_images: list[bytes]       # PNG of page 1 (PAGE_IMAGE_PAGES at PAGE_IMAGE_DPI); [] if it cannot be drawn.
                                   # llm_extract empties it after use so later nodes do not carry the picture.

    # llm_extract output
    llm_raw: Optional[LLMExtraction]
    llm_refusal: Optional[str]
    # llm_extract output, only for rows whose page 1 was drawn:
    page_image: bool               # True: the AI was asked with the page picture (pdf_unreadable set back to False)
    page_image_unsupported: bool   # True: the model cannot take pictures, so no AI call; the row stays pdf_unreadable
    # llm_extract output, only for rows with an AI result (OOS included):
    publisher_final: Optional[str]                    # the AI's answer if exactly a dictionary canonical name, else None
    publisher_type_final: Optional[PUBLISHER_TYPES]   # the dictionary section of publisher_final (never the AI's)
    publisher_suspect: Optional[PublisherSuspect]     # what the file name's tag says about it; None = not suspect

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
