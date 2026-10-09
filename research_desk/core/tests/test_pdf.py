"""core.pdf: storage confinement, page text, page count, page images, one PyMuPDF user at a time.

Real reads use temp PDFs only; the concurrency checks swap ``pymupdf.open`` for a fake, so no
real document is ever touched from two threads.
"""
from __future__ import annotations

import struct
import threading
import time
from contextlib import contextmanager
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


# ── one PyMuPDF user at a time ───────────────────────────────────────────────
# PyMuPDF must not be used from several threads at once. The tagger reads two rows' PDFs in
# worker threads and the web app renders pages and reads text from FastAPI's thread pool, so
# every core.pdf function holds one process-wide lock from opening a document to closing it.

class _Tracker:
    """Counts documents open at once and calls in progress at once, keeping the maxima."""

    def __init__(self, pause: float = 0.002):
        self._guard = threading.Lock()
        self._pause = pause
        self.open_docs = self.max_open_docs = 0
        self.inside = self.max_inside = 0

    @contextmanager
    def call(self):
        with self._guard:
            self.inside += 1
            self.max_inside = max(self.max_inside, self.inside)
        try:
            time.sleep(self._pause)  # give another thread the chance to step in
            yield
        finally:
            with self._guard:
                self.inside -= 1

    def opened(self):
        with self._guard:
            self.open_docs += 1
            self.max_open_docs = max(self.max_open_docs, self.open_docs)

    def closed(self):
        with self._guard:
            self.open_docs -= 1


class _FakePixmap:
    def __init__(self, tracker: _Tracker, number: int):
        self._tracker, self._number = tracker, number

    def tobytes(self, fmt: str) -> bytes:
        with self._tracker.call():
            return f"{fmt}-{self._number}".encode()


class _FakePage:
    def __init__(self, tracker: _Tracker, number: int):
        self._tracker, self.number = tracker, number

    def get_text(self, kind: str) -> str:
        with self._tracker.call():
            return f"{kind}-{self.number}"

    def get_pixmap(self, matrix, alpha: bool) -> _FakePixmap:
        with self._tracker.call():
            return _FakePixmap(self._tracker, self.number)


class _FakeDoc:
    PAGES = 3

    def __init__(self, tracker: _Tracker):
        self._tracker = tracker
        self._closed = False
        tracker.opened()

    @property
    def page_count(self) -> int:
        with self._tracker.call():
            return self.PAGES

    def __getitem__(self, index: int) -> _FakePage:
        with self._tracker.call():
            if not 0 <= index < self.PAGES:
                raise IndexError(index)
            return _FakePage(self._tracker, index)

    def close(self) -> None:
        with self._tracker.call():
            if not self._closed:
                self._closed = True
                self._tracker.closed()

    def __enter__(self) -> "_FakeDoc":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


@pytest.fixture
def fake_pymupdf(monkeypatch):
    """``pymupdf.open`` as core.pdf sees it, replaced by a fake that records overlaps.

    A path whose name starts with ``broken`` fails to open, like a damaged file.
    """
    tracker = _Tracker()

    def fake_open(path):
        with tracker.call():
            if Path(path).name.startswith("broken"):
                raise RuntimeError("cannot open broken document")
            return _FakeDoc(tracker)

    monkeypatch.setattr(pdf.pymupdf, "open", fake_open)
    return tracker


def _run_together(calls, timeout: float = 30) -> list:
    """Start every call in its own thread at the same moment; their results (or errors) in order."""
    barrier = threading.Barrier(len(calls))
    results: list = [None] * len(calls)

    def worker(i, call):
        barrier.wait()
        try:
            results[i] = call()
        except Exception as exc:  # compared by the test
            results[i] = exc

    threads = [threading.Thread(target=worker, args=(i, call), daemon=True)
               for i, call in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout)
    assert not any(thread.is_alive() for thread in threads), "a PDF call never finished"
    return results


def test_pdf_calls_from_many_threads_never_overlap_inside_pymupdf(fake_pymupdf):
    calls = []
    for _ in range(3):
        calls += [
            lambda: pdf.page_texts("report.pdf"),
            lambda: pdf.page_texts("report.pdf", max_pages=2),
            lambda: pdf.render_page_png("report.pdf", 2),
            lambda: pdf.page_count("report.pdf"),
        ]

    results = _run_together(calls)

    assert results == [
        ["text-0", "text-1", "text-2"], ["text-0", "text-1"], b"png-1", 3,
    ] * 3
    assert fake_pymupdf.max_open_docs == 1  # the lock spans open → read → close
    assert fake_pymupdf.max_inside == 1
    assert fake_pymupdf.open_docs == 0


def test_failing_pdf_calls_from_many_threads_keep_their_errors_and_never_overlap(fake_pymupdf):
    calls = [
        lambda: pdf.page_texts("broken.pdf"),
        lambda: pdf.page_count("broken.pdf"),
        lambda: pdf.render_page_png("broken.pdf", 1),
        lambda: pdf.render_page_png("report.pdf", 4),
        lambda: pdf.render_page_png("report.pdf", 0),
        lambda: pdf.page_texts("report.pdf"),
    ] * 2

    results = _run_together(calls)

    for first in (0, 6):
        unopenable, count_error, render_error, past_end, before_start, texts = results[first:first + 6]
        assert unopenable == []
        assert isinstance(count_error, RuntimeError)
        assert isinstance(render_error, RuntimeError)
        assert isinstance(past_end, PageNotFound)
        assert isinstance(before_start, PageNotFound)
        assert texts == ["text-0", "text-1", "text-2"]
    assert fake_pymupdf.max_open_docs == 1
    assert fake_pymupdf.max_inside == 1
    assert fake_pymupdf.open_docs == 0


def test_a_failed_pdf_call_does_not_block_the_next_one(tmp_path):
    good = _make_pdf(tmp_path / "two.pdf", ["first", "second"])
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf at all")

    with pytest.raises(Exception):
        pdf.page_count(broken)
    with pytest.raises(PageNotFound):
        pdf.render_page_png(good, 3)
    assert pdf.page_texts(broken) == []

    # From another thread, one call at a time: a lock left held by the failures above would
    # hang here (and fail on the timeout instead of hanging the test run).
    [count] = _run_together([lambda: pdf.page_count(good)], timeout=10)
    [texts] = _run_together([lambda: pdf.page_texts(good)], timeout=10)
    assert count == 2
    assert [t.strip() for t in texts] == ["first", "second"]
