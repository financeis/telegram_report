"""Pydantic schema for the analysis AI call's structured output.

``ExtractionResult``: structured fields + evidence for one report. (The
comparison narrative's schema belongs to the compare feature.)
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, conint, model_validator

from .financials import FinancialDetails


TargetPriceDir       = Literal['상향', '불변', '하향', '신규', 'N/A']
Recommendation       = Literal['매수', '중립', '매도', 'N/A']
RecommendationDir    = Literal['유지', '상향', '하향', '신규', 'N/A']
ExtractionConfidence = Literal['high', 'medium', 'low']

PageNum = conint(ge=1)  # 1-indexed; 0/음수 reject


class ExtractionResult(BaseModel):
    # Nullable keeps existing summaries/fixtures readable. New extraction fills this.
    financial_details: Optional[FinancialDetails] = None
    # 구조화 필드
    target_price_new:   Optional[int] = None
    target_price_old:   Optional[int] = None
    target_price_dir:   TargetPriceDir
    recommendation:     Recommendation
    recommendation_dir: RecommendationDir
    one_line_summary:   str       = Field(..., max_length=90)
    positive_points:    list[str] = Field(..., min_length=0, max_length=5)
    risk_points:        list[str] = Field(..., min_length=0, max_length=5)

    # evidence / 신뢰성
    target_price_raw:      Optional[str]  = Field(default=None, max_length=40)
    recommendation_raw:    Optional[str]  = Field(default=None, max_length=40)
    source_pages:          list[PageNum]  = Field(default_factory=list, max_length=10)
    extraction_confidence: ExtractionConfidence

    @model_validator(mode='after')
    def _dedupe_sort_pages(self):
        # source_pages — 중복 제거 + 오름차순 (immutable model에 setattr)
        object.__setattr__(self, 'source_pages', sorted(set(self.source_pages)))
        return self

    @model_validator(mode='after')
    def _evidence_invariant(self):
        """target_price_new가 있으면 raw 또는 source_pages 중 하나는 필수."""
        if self.target_price_new is not None:
            if not self.target_price_raw and not self.source_pages:
                raise ValueError(
                    "target_price_new가 set이면 target_price_raw 또는 "
                    "source_pages 중 하나 이상은 evidence로 채워야 함"
                )
        return self
