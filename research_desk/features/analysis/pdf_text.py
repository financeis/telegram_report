"""Page-marked report text for the extraction prompt, built on ``core.pdf.page_texts``.

- Each page becomes ``--- Page N ---\\n<text>\\n``. ``ground_metrics`` splits pages
  on these markers, so the format must not change.
- Tokens are estimated as characters ÷ 3 per page chunk. From the first page
  that would pass the cap, no more pages go in; only ``input_truncated`` records
  it (no marker is added to the text).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from research_desk.core import pdf

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PDFTextResult:
    text: str                     # page-numbered concat
    pages_used: int               # 실제 추출에 포함된 페이지 수
    total_pages: int              # 원본 PDF의 총 페이지 수
    input_truncated: bool         # max_tokens cap에 걸려 잘렸나
    estimated_input_tokens: int   # 추출된 text의 token 추정


EMPTY = PDFTextResult('', 0, 0, False, 0)


def _estimate_tokens(text: str) -> int:
    """대략 1 token ≈ 3 chars (한국어 mixed text 가정). overcount는 안전 방향."""
    return max(1, len(text) // 3)


def build_pages_text(pages: list[str], max_tokens: int, name: str = '') -> PDFTextResult:
    """Page texts (in order) → page-marked text, stopping at the token cap."""
    total = len(pages)
    parts: list[str] = []
    cumulative = 0
    truncated = False

    for i, body in enumerate(pages):
        chunk = f"--- Page {i + 1} ---\n{body or ''}\n"
        chunk_tokens = _estimate_tokens(chunk)

        if cumulative + chunk_tokens > max_tokens:
            truncated = True
            logger.warning(
                "PDF truncated at page %d/%d (cap=%d tokens, used=%d): %s",
                i, total, max_tokens, cumulative, name,
            )
            break

        parts.append(chunk)
        cumulative += chunk_tokens

    return PDFTextResult(
        text=''.join(parts),
        pages_used=len(parts),
        total_pages=total,
        input_truncated=truncated,
        estimated_input_tokens=cumulative,
    )


def extract_all_pages(path: Path, max_tokens: int) -> PDFTextResult:
    """PDF를 페이지별로 추출 + page-numbered concat. cap 넘으면 truncate.

    파일 부재·읽기 실패는 빈 결과 반환 (analysis가 텍스트 없음으로 처리).
    """
    path = Path(path)
    if not path.exists():
        logger.warning("PDF not found: %s", path)
        return EMPTY
    pages = pdf.page_texts(path)
    if not pages:
        logger.warning("PDF open failed or has no pages: %s", path)
        return EMPTY
    return build_pages_text(pages, max_tokens, path.name)
