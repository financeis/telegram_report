"""Tests for extract_pdf node."""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import fitz  # PyMuPDF
import pytest

from research_desk.core import pdf as core_pdf
from research_desk.tagger.nodes import extract_pdf as node
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


# ── no text on pages 1–3 → page 1 drawn as a PNG (image-only PDFs) ───────────

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _make_picture_pdf(path: Path, pages: int = 1, text_on: dict[int, str] | None = None) -> Path:
    """Pages that carry only a picture and no text layer, like a scanned report.

    ``text_on`` puts Korean text (the 'korea' font, so it is extractable) on the
    given 1-based pages.
    """
    doc = fitz.open()
    for number in range(1, pages + 1):
        page = doc.new_page()
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 20), False)
        pix.clear_with(180)
        page.insert_image(fitz.Rect(72, 72, 272, 172), pixmap=pix)
        if text_on and number in text_on:
            page.insert_text((72, 300), text_on[number], fontname="korea", fontsize=11)
    doc.save(path)
    doc.close()
    return path


def _make_encrypted_pdf(path: Path) -> Path:
    """A PDF that needs a password to open: its text and its pages cannot be read."""
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "키움증권 리서치센터", fontname="korea", fontsize=11)
    doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="user", owner_pw="owner")
    doc.close()
    return path


def _no_drawing(path, page, dpi=None):
    raise AssertionError("a page must not be drawn when pages 1-3 have text")


def test_page_image_defaults():
    """One page (page 1) at 120 dpi; both are module constants so they can be tuned."""
    assert node.PAGE_IMAGE_PAGES == 1
    assert node.PAGE_IMAGE_DPI == 120


@pytest.mark.asyncio
async def test_picture_only_pdf_gives_a_png_of_page_1(tmp_path):
    path = _make_picture_pdf(tmp_path / "scan.pdf", pages=4)

    out = await extract_pdf({"file_path": str(path)})

    # The text walk is unchanged: still no text after 3 pages.
    assert out["pdf_text"] == ""
    assert out["pages_used"] == [1, 2, 3]
    assert out["pdf_unreadable"] is True
    (png,) = out["page_images"]
    assert png.startswith(PNG_SIGNATURE)
    assert png == core_pdf.render_page_png(path, 1, dpi=120)


@pytest.mark.asyncio
async def test_text_only_beyond_page_3_still_gives_a_picture(tmp_path):
    """Only pages 1–3 are read for text, so text on page 4 does not count."""
    path = _make_picture_pdf(tmp_path / "scan.pdf", pages=4, text_on={4: "키움증권 리서치센터"})

    out = await extract_pdf({"file_path": str(path)})

    assert out["pdf_unreadable"] is True
    assert len(out["page_images"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("text_on", [{1: "키움증권 리서치센터"}, {2: "둘째 쪽 글자"}, {3: "셋째 쪽 글자"}])
async def test_text_on_any_of_pages_1_to_3_means_no_picture(monkeypatch, tmp_path, text_on):
    path = _make_picture_pdf(tmp_path / "mixed.pdf", pages=3, text_on=text_on)
    monkeypatch.setattr(core_pdf, "render_page_png", _no_drawing)

    out = await extract_pdf({"file_path": str(path)})

    assert out["pdf_unreadable"] is False
    assert out["pdf_text"] == next(iter(text_on.values()))
    assert "page_images" not in out


@pytest.mark.asyncio
@pytest.mark.parametrize("pages", [
    ["키움증권 분석가", "둘", "셋"],      # cover info on page 1
    ["하나", "둘", "셋"],                # a little text, no cover info, 3 pages read
    ["", "", "x"],                       # one character on page 3
])
async def test_the_text_walk_never_draws_when_there_is_text(monkeypatch, pages):
    monkeypatch.setattr(core_pdf, "page_texts", lambda path, max_pages=None: list(pages))
    monkeypatch.setattr(core_pdf, "render_page_png", _no_drawing)

    out = await extract_pdf({"file_path": "/reports/any.pdf"})

    assert out["pdf_unreadable"] is False
    assert "page_images" not in out


@pytest.mark.asyncio
async def test_only_page_1_is_drawn_at_120_dpi(monkeypatch, tmp_path):
    calls = []

    def fake_render(path, page, dpi=None):
        calls.append((path, page, dpi))
        return b"PNG-%d" % page

    monkeypatch.setattr(core_pdf, "page_texts", lambda path, max_pages=None: ["", " ", "\n"])
    monkeypatch.setattr(core_pdf, "render_page_png", fake_render)

    out = await extract_pdf({"file_path": str(tmp_path / "a.pdf")})

    assert calls == [(tmp_path / "a.pdf", 1, 120)]
    assert out["page_images"] == [b"PNG-1"]
    assert out["pdf_unreadable"] is True


@pytest.mark.asyncio
async def test_page_count_and_dpi_are_read_when_drawing(monkeypatch, tmp_path):
    """Tuning the module constants changes what is drawn (they are not frozen at import)."""
    calls = []

    def fake_render(path, page, dpi=None):
        calls.append((page, dpi))
        if page > 2:
            raise core_pdf.PageNotFound(f"page {page} is not in the document (2 pages)")
        return b"PNG-%d" % page

    monkeypatch.setattr(core_pdf, "page_texts", lambda path, max_pages=None: ["", ""])
    monkeypatch.setattr(core_pdf, "render_page_png", fake_render)
    monkeypatch.setattr(node, "PAGE_IMAGE_PAGES", 3)
    monkeypatch.setattr(node, "PAGE_IMAGE_DPI", 72)

    out = await extract_pdf({"file_path": str(tmp_path / "a.pdf")})

    # A document shorter than the page count keeps the pages it has.
    assert calls == [(1, 72), (2, 72), (3, 72)]
    assert out["page_images"] == [b"PNG-1", b"PNG-2"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["missing", "broken", "encrypted"])
async def test_no_picture_when_page_1_cannot_be_drawn(tmp_path, kind):
    path = tmp_path / "x.pdf"
    if kind == "broken":
        path.write_bytes(b"%PDF-1.7 not really a pdf")
    elif kind == "encrypted":
        _make_encrypted_pdf(path)

    out = await extract_pdf({"file_path": str(path)})

    assert out["pdf_unreadable"] is True
    assert out["pdf_text"] == ""
    assert out["page_images"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    core_pdf.PageNotFound("page 1 is not in the document (0 pages)"),   # 0 pages
    RuntimeError("drawing failed"),
    ValueError("document closed or encrypted"),
    MemoryError(),
])
async def test_drawing_errors_mean_no_picture(monkeypatch, error):
    def failing_render(path, page, dpi=None):
        raise error

    monkeypatch.setattr(core_pdf, "page_texts", lambda path, max_pages=None: [])
    monkeypatch.setattr(core_pdf, "render_page_png", failing_render)

    out = await extract_pdf({"file_path": "/reports/any.pdf"})

    assert out == {"pdf_text": "", "pages_used": [], "pdf_unreadable": True, "page_images": []}


@pytest.mark.asyncio
async def test_drawing_runs_off_the_event_loop(monkeypatch):
    loop_thread = threading.get_ident()
    drawn_on = []

    def fake_render(path, page, dpi=None):
        drawn_on.append(threading.get_ident())
        return b"png"

    monkeypatch.setattr(core_pdf, "page_texts", lambda path, max_pages=None: [""])
    monkeypatch.setattr(core_pdf, "render_page_png", fake_render)

    await extract_pdf({"file_path": "/reports/any.pdf"})

    assert drawn_on and drawn_on[0] != loop_thread
