"""Synthesize the committed golden boundary-case PDFs (spec §6.5 rule 4 + §6.6).

These PDFs are committed (binary) so future integration / parity tests can
exercise extract_pdf against documented golden inputs. Run this module once
locally to regenerate them; idempotent (overwrites existing).

Usage:
    python -m research_desk.tagger.tests.golden._synthesize
"""
from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF


CASES: dict[str, str] = {
    "ipo_unlisted.pdf": (
        "신영증권 IPO 분석\n공모예정 ABC테크\n공모가 밴드 5,000~6,000원"
    ),
    "domestic_with_foreign_peer.pdf": (
        "키움증권 삼성전자 1Q26 Preview\n분석가 홍길동\nNVDA H100 수요 ↑"
    ),
    "ir_company_self.pdf": (
        "휴온스 Investor Relations\nIR Material 2026.04\n비상장 자회사 현황"
    ),
    "unknown_publisher_in_scope.pdf": (
        "NewBoutique Research\n분석가 김신규\n삼성전자 [005930] 매수\n목표주가 100,000원"
    ),
    "unknown_product_in_scope.pdf": (
        "키움증권 분석\n삼성전자 [005930] 매수\n주요제품: 완전이상한제품"
    ),
    "private_unlisted.pdf": (
        "비상장사 ABC 분석\n[000000]\n장외 시장 동향"
    ),
}


def synth(path: Path, text: str) -> None:
    """Create a one-page PDF with the given text using the built-in 'korea' font.

    The 'korea' face is a CJK-capable PyMuPDF font that round-trips Hangul
    through text extraction (the default Helvetica face cannot encode Hangul).
    """
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontsize=11, fontname="korea")
    doc.save(str(path))
    doc.close()


def main() -> None:
    here = Path(__file__).parent
    for name, text in CASES.items():
        out = here / name
        synth(out, text)
        print(f"  wrote {out.name} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
