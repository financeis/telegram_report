"""Tests for extract_pdf node."""
from __future__ import annotations

import asyncio
from pathlib import Path

import fitz  # PyMuPDF
import pytest

from research_desk.core import pdf as core_pdf
from research_desk.tagger.nodes.extract_pdf import (
    _META_KEYWORDS, _has_meta_signals, _resolve, _sync_extract, extract_pdf,
)


GOLDEN = Path(__file__).parent / "golden"


def _make_pdf(path: Path, pages_text: list[str]) -> None:
    """Synthesize a tiny PDF with given text on each page.

    Uses the built-in 'korea' CJK font so Hangul roundtrips through
    PyMuPDF's text extraction (the default Helvetica face cannot encode
    Hangul codepoints and would emit placeholder glyphs).
    """
    doc = fitz.open()
    for txt in pages_text:
        page = doc.new_page()
        if txt:
            page.insert_text((72, 72), txt, fontname="korea", fontsize=12)
    doc.save(path)
    doc.close()


@pytest.fixture(autouse=True)
def _ensure_golden(tmp_path_factory):
    """Create synthetic PDFs once per session."""
    GOLDEN.mkdir(exist_ok=True)
    if not (GOLDEN / "single_page_with_meta.pdf").exists():
        _make_pdf(
            GOLDEN / "single_page_with_meta.pdf",
            ["키움증권 리서치센터\n분석가: 홍길동\n투자의견: 매수\n목표주가: 100,000원"],
        )
    if not (GOLDEN / "page1_blank_meta_on_p2.pdf").exists():
        _make_pdf(
            GOLDEN / "page1_blank_meta_on_p2.pdf",
            ["", "키움증권 리서치센터\n분석가: 김철수\n투자의견: 매수"],
        )
    if not (GOLDEN / "no_meta_anywhere.pdf").exists():
        _make_pdf(GOLDEN / "no_meta_anywhere.pdf", ["하나", "둘", "셋", "넷", "다섯", "여섯"])


@pytest.mark.asyncio
async def test_first_page_with_meta_stops_at_p1():
    state = {"file_path": str(GOLDEN / "single_page_with_meta.pdf")}
    out = await extract_pdf(state)
    assert out["pages_used"] == [1]
    assert "키움증권" in out["pdf_text"]
    assert out["pdf_unreadable"] is False


@pytest.mark.asyncio
async def test_blank_first_page_falls_back_to_p2():
    state = {"file_path": str(GOLDEN / "page1_blank_meta_on_p2.pdf")}
    out = await extract_pdf(state)
    assert out["pages_used"] == [1, 2]
    assert "김철수" in out["pdf_text"]
    assert out["pdf_unreadable"] is False


@pytest.mark.asyncio
async def test_no_meta_walks_all_3_then_returns_text():
    state = {"file_path": str(GOLDEN / "no_meta_anywhere.pdf")}
    out = await extract_pdf(state)
    # No meta signals found → walked up to 3 pages (v2)
    assert out["pages_used"] == [1, 2, 3]
    # Text is non-empty (so pdf_unreadable False) but extraction is incomplete
    assert out["pdf_text"]
    assert out["pdf_unreadable"] is False


@pytest.mark.asyncio
async def test_corrupt_path_marks_unreadable():
    state = {"file_path": "/nonexistent/garbage.pdf"}
    out = await extract_pdf(state)
    assert out["pdf_unreadable"] is True
    assert out["pdf_text"] == ""
    assert out["pages_used"] == []


def test_meta_signals_detector():
    assert _has_meta_signals("분석가 김민수 투자의견 매수 목표주가") is True
    assert _has_meta_signals("키움증권 Research") is True
    assert _has_meta_signals("그냥 평범한 텍스트입니다") is False


def test_extract_pdf_max_pages_is_3(monkeypatch, tmp_path):
    """v2: max_pages는 3 (기존 v1의 5에서 축소)."""
    pdf = tmp_path / "five_pages.pdf"
    doc = fitz.open()
    for i in range(5):
        # Insert non-meta text so _has_meta_signals() never triggers early stop
        doc.new_page().insert_text((72, 72), f"page-{i+1}-content", fontsize=11)
    doc.save(pdf)
    doc.close()

    out = _sync_extract(pdf)  # uses default max_pages
    # v2: should stop at 3
    assert out["pages_used"] == [1, 2, 3]


# ── where the PDF is read from (spec §7, §10 item 5) ─────────────────────────

def test_relative_paths_default_to_the_reports_folder(tagger_env):
    """STORAGE_BASE_DIR unset → ./reports (was the current folder)."""
    assert _resolve("124784_report.pdf") == Path("reports") / "124784_report.pdf"


def test_relative_paths_use_storage_base_dir(tagger_env, tmp_path):
    tagger_env.setenv("STORAGE_BASE_DIR", str(tmp_path))
    assert _resolve("124784_report.pdf") == tmp_path / "124784_report.pdf"


def test_absolute_paths_are_kept(tagger_env, tmp_path):
    tagger_env.setenv("STORAGE_BASE_DIR", str(tmp_path / "elsewhere"))
    path = tmp_path / "x.pdf"
    assert _resolve(str(path)) == path


@pytest.mark.asyncio
async def test_relative_path_is_read_from_storage_base_dir(tagger_env, tmp_path):
    _make_pdf(tmp_path / "1_x.pdf", ["키움증권 리서치센터\n분석가: 홍길동"])
    tagger_env.setenv("STORAGE_BASE_DIR", str(tmp_path))
    out = await extract_pdf({"file_path": "1_x.pdf"})
    assert out["pages_used"] == [1]
    assert "홍길동" in out["pdf_text"]


# ── page walk on top of core.pdf.page_texts (spec §9.2) ─────────────────────

@pytest.mark.parametrize("pages,used,text,unreadable", [
    (["키움증권 분석가", "둘", "셋"], [1], "키움증권 분석가", False),     # meta on p1 → stop
    (["", "투자의견 매수", "셋"], [1, 2], "투자의견 매수", False),          # blank p1 → p2
    (["하나", "둘", "셋"], [1, 2, 3], "하나\n둘\n셋", False),               # no meta → all 3
    (["하나"], [1], "하나", False),
    (["", "  ", "\n"], [1, 2, 3], "", True),                              # no text → unreadable
    ([], [], "", True),                                                   # cannot open → unreadable
])
def test_page_walk_rules(monkeypatch, pages, used, text, unreadable):
    calls = []

    def fake_page_texts(path, max_pages=None):
        calls.append((path, max_pages))
        return list(pages)

    monkeypatch.setattr(core_pdf, "page_texts", fake_page_texts)
    out = _sync_extract(Path("any.pdf"))
    assert calls == [(Path("any.pdf"), 3)]       # at most 3 pages are read
    assert out == {"pdf_text": text, "pages_used": used, "pdf_unreadable": unreadable}


def test_meta_keywords_are_unchanged():
    assert _META_KEYWORDS == [
        "분석가", "애널리스트", "투자의견", "목표주가", "Research", "리서치",
        "증권", "FnGuide", "KIRS", "Investor Relations", "IR Material",
        "단일종목", "산업분석", "시황", "매크로", "퀀트", "전략",
    ]
