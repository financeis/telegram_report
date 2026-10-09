"""PDF files: confinement to the storage folder, page text, page count, page images.

Errors are plain exceptions; the features turn them into HTTP responses.

PyMuPDF must not be used from several threads at once, and this module is called from several:
the tagger reads two rows' PDFs in worker threads, and the web app renders review pages and reads
report text in its thread pool. So every function here that opens a document holds one
process-wide lock (``_PYMUPDF_LOCK``) from opening the document to closing it: one call at a time
is inside PyMuPDF, the others wait their turn. Only the PDF work waits; the AI calls around it
are not serialized.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Optional, Union

import pymupdf

logger = logging.getLogger(__name__)

# MuPDF prints some document errors (e.g. "MuPDF error: format error: ...") straight to the
# process's stdout, which would corrupt a command's one-JSON-object stdout. Turn its own
# printing off; the errors still surface as exceptions or empty results.
pymupdf.TOOLS.mupdf_display_errors(False)
pymupdf.TOOLS.mupdf_display_warnings(False)

StrPath = Union[str, os.PathLike]

NOT_IN_STORAGE = "PDF를 찾을 수 없습니다."
FILE_MISSING = "로컬 PDF 파일이 없습니다. 수집 상태를 확인해 주세요."

# One lock for the whole process, held across open → read → close by every PyMuPDF user below.
# Reentrant so a future helper that calls another function here from inside it cannot deadlock.
_PYMUPDF_LOCK = threading.RLock()


class PDFNotFound(Exception):
    """The path leaves the storage folder, is not a ``.pdf``, or the file is missing.

    ``message`` (also ``str()``) is the user-facing Korean text.
    """

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class PageNotFound(LookupError):
    """The page number is not in the document."""


def resolve_in_storage(base: StrPath, relative: StrPath) -> Path:
    """The resolved path of ``relative`` if it is a ``.pdf`` file inside ``base``.

    Raises PDFNotFound(NOT_IN_STORAGE) for a path outside ``base`` (``../``, an
    absolute path elsewhere) or a non-PDF name, and PDFNotFound(FILE_MISSING)
    when the file does not exist.
    """
    root = Path(base).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path.suffix.lower() != ".pdf":
        raise PDFNotFound(NOT_IN_STORAGE)
    if not path.is_file():
        raise PDFNotFound(FILE_MISSING)
    return path


def page_texts(path: StrPath, max_pages: Optional[int] = None) -> list[str]:
    """Text of each page in order, at most ``max_pages`` pages.

    A file that cannot be opened (missing, broken, empty) gives ``[]``; a page
    whose text cannot be read gives ``""`` for that page.
    """
    with _PYMUPDF_LOCK:
        try:
            doc = pymupdf.open(path)
        except Exception as exc:
            logger.debug("PDF open failed: %s (%s)", path, exc)
            return []
        try:
            count = doc.page_count if max_pages is None else min(max_pages, doc.page_count)
            texts: list[str] = []
            for i in range(count):
                try:
                    texts.append(doc[i].get_text("text") or "")
                except Exception:
                    texts.append("")
            return texts
        finally:
            doc.close()


def page_count(path: StrPath) -> int:
    """Number of pages; an error opening the file propagates."""
    with _PYMUPDF_LOCK, pymupdf.open(path) as doc:
        return doc.page_count


def render_page_png(path: StrPath, page: int, dpi: float = 120) -> bytes:
    """PNG of page ``page`` (1-based) at ``dpi``; PageNotFound if out of range."""
    with _PYMUPDF_LOCK, pymupdf.open(path) as doc:
        if page < 1 or page > doc.page_count:
            raise PageNotFound(f"page {page} is not in the document ({doc.page_count} pages)")
        zoom = dpi / 72
        pixmap = doc[page - 1].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        png = pixmap.tobytes("png")
        del pixmap   # free MuPDF's pixmap while the lock is still held
        return png
