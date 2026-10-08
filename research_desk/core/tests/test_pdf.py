"""core.pdf: storage confinement, page text, page count, page images (temp PDFs only)."""
from __future__ import annotations

import struct
from pathlib import Path

import pymupdf
import pytest

from research_desk.core import pdf
from research_desk.core.pdf import PageNotFound, PDFNotFound, resolve_in_storage

NOT_FOUND = "PDF를 찾을 수 없습니다."
MISSING = "로컬 PDF 파일이 없습니다. 수집 상태를 확인해 주세요."


def _make_pdf(path: Path, pages: list[str], size: float = 300) -> Path:
    """A PDF of square pages, one per text; the built-in Korean font keeps Hangul extractable."""
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page(width=size, height=size)
        if text:
            page.insert_text((4, size / 2), text, fontname="korea", fontsize=8)
    doc.save(str(path))
    doc.close()
    return path


def _png_size(png: bytes) -> tuple[int, int]:
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    return struct.unpack(">II", png[16:24])


# ── storage confinement ──────────────────────────────────────────────────────

def _reason(base, relative) -> str:
    with pytest.raises(PDFNotFound) as exc:
        resolve_in_storage(base, relative)
    return str(exc.value)


def test_pdf_is_confined_to_storage(tmp_path):
    root = tmp_path / "reports"
    root.mkdir()
    (root / "good.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "outside.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / ".env").write_text("SECRET=1\n", encoding="utf-8")
    assert resolve_in_storage(root, "good.pdf") == root / "good.pdf"

    assert _reason(root, "../outside.pdf") == NOT_FOUND
    assert _reason(root, str(tmp_path / "outside.pdf")) == NOT_FOUND  # absolute, outside
    assert _reason(root, "../.env") == NOT_FOUND
    assert _reason(root, "missing.pdf") == MISSING


def test_storage_rejections_inside_the_folder(tmp_path):
    root = tmp_path / "reports"
    (root / "folder.pdf").mkdir(parents=True)
    (root / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (root / "notes.txt").write_text("x", encoding="utf-8")

    assert _reason(root, ".env") == NOT_FOUND
    assert _reason(root, "notes.txt") == NOT_FOUND
    assert _reason(root, "sub/../../reports/../outside.pdf") == NOT_FOUND
    assert _reason(root, "folder.pdf") == MISSING  # not a file


def test_storage_accepts_pdfs_inside_however_they_are_written(tmp_path):
    root = tmp_path / "reports"
    (root / "2026").mkdir(parents=True)
    (root / "2026" / "123_report.PDF").write_bytes(b"%PDF-1.4")
    expected = (root / "2026" / "123_report.PDF").resolve()

    assert resolve_in_storage(root, "2026/123_report.PDF") == expected
    assert resolve_in_storage(root, "2026/../2026/123_report.PDF") == expected
    assert resolve_in_storage(root, str(expected)) == expected  # absolute, inside
    assert resolve_in_storage(str(root), "2026/123_report.PDF") == expected


def test_pdf_not_found_is_not_a_web_framework_error(tmp_path):
    with pytest.raises(PDFNotFound) as exc:
        resolve_in_storage(tmp_path, "missing.pdf")
    assert exc.value.message == MISSING
    modules = {cls.__module__.split(".")[0] for cls in type(exc.value).__mro__}
    assert not modules & {"fastapi", "starlette"}


# ── page text ────────────────────────────────────────────────────────────────

def test_page_texts_reads_every_page_in_order(tmp_path):
    path = _make_pdf(tmp_path / "three.pdf", ["키움증권 리서치", "second page", "third page"])

    texts = pdf.page_texts(path)

    assert len(texts) == 3
    assert "키움증권" in texts[0]
    assert "second page" in texts[1]
    assert "third page" in texts[2]
    assert pdf.page_texts(str(path)) == texts


def test_page_texts_can_stop_after_some_pages(tmp_path):
    path = _make_pdf(tmp_path / "five.pdf", [f"page-{i}" for i in range(1, 6)])
    assert [t.strip() for t in pdf.page_texts(path, max_pages=3)] == ["page-1", "page-2", "page-3"]
    assert len(pdf.page_texts(path, max_pages=10)) == 5


def test_page_texts_blank_page_is_empty_text(tmp_path):
    path = _make_pdf(tmp_path / "blank-first.pdf", ["", "meta"])
    texts = pdf.page_texts(path)
    assert texts[0].strip() == ""
    assert "meta" in texts[1]


def test_page_texts_of_an_unopenable_file_is_an_empty_list(tmp_path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf at all")
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    for path in (broken, empty, tmp_path / "missing.pdf"):
        assert pdf.page_texts(path) == []


def test_page_texts_failed_page_is_empty_text(tmp_path, monkeypatch):
    path = _make_pdf(tmp_path / "three.pdf", ["one", "two", "three"])
    real_get_text = pymupdf.Page.get_text

    def flaky_get_text(page, *args, **kwargs):
        if page.number == 1:
            raise RuntimeError("damaged page")
        return real_get_text(page, *args, **kwargs)

    monkeypatch.setattr(pymupdf.Page, "get_text", flaky_get_text)
    texts = pdf.page_texts(path)
    assert len(texts) == 3
    assert "one" in texts[0]
    assert texts[1] == ""
    assert "three" in texts[2]


# ── page count and images ────────────────────────────────────────────────────

def test_page_count(tmp_path):
    assert pdf.page_count(_make_pdf(tmp_path / "three.pdf", ["a", "b", "c"])) == 3


def test_page_count_of_a_broken_file_raises(tmp_path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf at all")
    with pytest.raises(Exception):
        pdf.page_count(broken)


def test_render_page_png_default_is_120_dpi(tmp_path):
    path = _make_pdf(tmp_path / "two.pdf", ["first", "second"], size=72)  # one inch square

    assert _png_size(pdf.render_page_png(path, 1)) == (120, 120)
    assert _png_size(pdf.render_page_png(path, 2, dpi=72)) == (72, 72)
    assert _png_size(pdf.render_page_png(str(path), 2, dpi=144)) == (144, 144)


@pytest.mark.parametrize("page", [0, -1, 3])
def test_render_page_png_rejects_pages_outside_the_document(tmp_path, page):
    path = _make_pdf(tmp_path / "two.pdf", ["first", "second"])
    with pytest.raises(PageNotFound):
        pdf.render_page_png(path, page)
