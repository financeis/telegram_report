"""The AI reply shape for one company's business profile (프로필, AI 사업 요약 카드).

Count and length limits are not in the schema: the reply is validated as it is, then grounded
and cut in code (``logic.cap_profile``), so an over-long list never fails a company. Missing
values are empty lists and ``-1`` (a share), never null.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ROLES: tuple[str, ...] = (
    "소재", "부품", "장비", "설계(팹리스)", "완제품 제조", "위탁생산(OEM/ODM/CDMO)",
    "패키징·테스트", "유통", "서비스·플랫폼", "건설·EPC", "금융", "지주·투자", "기타",
)
Role = Literal[
    "소재", "부품", "장비", "설계(팹리스)", "완제품 제조", "위탁생산(OEM/ODM/CDMO)",
    "패키징·테스트", "유통", "서비스·플랫폼", "건설·EPC", "금융", "지주·투자", "기타",
]
InfoQuality = Literal["충분", "부족"]


class Segment(BaseModel):
    name: str = Field(description="사업부문명(원문 표기)")
    products: list[str] = Field(description="그 부문의 제품, 최대 6개")
    keywords: list[str] = Field(description="그 부문의 틈새 용어, 최대 6개")
    revenue_share_pct: float = Field(
        description="원문 표에 적힌 매출 비중(%). 원문에 없으면 -1. 계산·추정 금지")


class CompanyProfile(BaseModel):
    niche_industry: str = Field(description="틈새 업종, 40자 이내")
    summary: str = Field(description="무엇을 누구에게 파는가, 160자 이내")
    roles: list[Role] = Field(description="가치사슬에서의 역할 1~2개")
    segments: list[Segment] = Field(description="사업부문, 매출 비중 큰 순, 최대 6개")
    products: list[str] = Field(description="자사 제품, 최대 12개")
    keywords: list[str] = Field(description="회사 전체의 틈새 용어, 최대 15개")
    applications: list[str] = Field(description="전방산업·적용처, 최대 8개")
    customers: list[str] = Field(description="원문에 이름이 나온 고객사만, 최대 8개")
    competitors: list[str] = Field(description="원문에 이름이 나온 경쟁사만, 최대 8개")
    is_holding: bool = Field(description="지주회사이면 true")
    is_financial: bool = Field(description="은행·증권·보험·여신·VC 등 금융업이면 true")
    info_quality: InfoQuality = Field(description="정보가 충분하면 충분, 부족하면 부족")
