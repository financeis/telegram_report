"""Pydantic schema for the structured-output LLM call (v2).

v2 변경 (rev-7):
- report_type 14종 → 6종
- sectors_major/minor, products, topics, company_names, publisher_raw 제거
- stock_codes_raw, company_names_raw 추가 (raw audit)
- publisher_canon, publisher_type 직접 출력 (LLM이 publishers.yaml 보고 매핑)

The Literal value sets must equal research_desk/domain/vocabulary.yaml (tested).
Class docstrings and Field descriptions are part of the JSON schema the LLM
receives, so editing them changes the request.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field
from typing_extensions import Literal

REPORT_TYPES = Literal[
    "단일종목", "산업", "섹터", "IR자료", "전략·시황", "기타",
]

PUBLISHER_TYPES = Literal[
    "broker", "data_provider", "ir_agency", "other",
]


class OOSSignals(BaseModel):
    """LLM-observed primary-coverage signals."""
    foreign_primary_coverage: bool = Field(
        description="**리포트의 primary coverage가 해외 상장사**일 때만 true. "
                    "국내 종목/산업 리포트가 외국 티커를 peer/벨류체인/수요처로 "
                    "단순 언급하는 경우는 false."
    )
    etf_or_fund: bool = Field(
        description="ETF 라인업 / 펀드평가 / 펀드비교 (primary coverage가 펀드/ETF)"
    )
    digital_asset: bool = Field(
        description="가상자산·디지털자산·BTC·ETH·코인 (primary coverage가 디지털자산)"
    )
    private_company_likely: bool = Field(
        description="명백한 비상장/장외 컨텍스트 (000000 코드, '비상장 분석' 표기 등). "
                    "IR자료/IPO/KRX-매칭 케이스는 false (별도 분기 처리)."
    )
    # ir_self는 LLM signal로 안 둠 — report_type='IR자료'에서 자동 결정


class LLMExtraction(BaseModel):
    """All fields the LLM populates in one structured-output call (v2)."""
    report_type: REPORT_TYPES
    title: Optional[str] = Field(default=None, max_length=120)
    published_at: Optional[str] = Field(
        default=None,
        description="YYYY-MM-DD or null if not present on first page"
    )

    stock_codes_raw: list[str] = Field(
        default_factory=list,
        description="첫 페이지 헤더의 KRX 6자리 코드 (영문 포함). 본문 등장 종목은 추출 안 함."
    )
    company_names_raw: list[str] = Field(
        default_factory=list,
        description="회사명 후보 raw. 시스템이 KRX로 정규화 또는 audit으로 보존."
    )

    publisher_canon: Optional[str] = Field(
        default=None,
        description="publishers.yaml의 canonical 형태로 정규화한 발행 주체. 매칭 안 되면 null."
    )
    publisher_type: Optional[PUBLISHER_TYPES] = Field(
        default=None,
        description="publisher_canon이 set되면 함께 결정. 매칭 안 되면 null."
    )

    analysts: list[str] = Field(default_factory=list)

    oos_signals: OOSSignals
    self_confidence: Literal["high", "medium", "low"] = Field(
        description="LLM이 자체 판단한 추출 신뢰도"
    )
    notes: Optional[str] = Field(
        default=None, max_length=200,
        description="모호함·특이사항 메모 (한 줄)"
    )
