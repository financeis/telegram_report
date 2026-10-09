"""extract_pdf node: read page 1, falling back up to page 3 if metadata is sparse.

When pages 1-3 give no text at all (a scanned or picture-only PDF), page 1 is also
drawn as a PNG (``page_images``) for llm_extract to show the AI instead of text.
``pdf_unreadable`` still means "no text on pages 1-3" here; llm_extract clears it
only when it actually asks the AI with the picture.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from research_desk.core import pdf, settings
from research_desk.tagger.state import RowState

# Heuristic keywords that indicate a research PDF's first page has the metadata
# we need (analyst, publisher, investment opinion, target price, report type words).
_META_KEYWORDS = [
    "분석가", "애널리스트", "투자의견", "목표주가", "Research", "리서치",
    "증권", "FnGuide", "KIRS", "Investor Relations", "IR Material",
    "단일종목", "산업분석", "시황", "매크로", "퀀트", "전략",
]

MAX_PAGES = 3

# A PDF with no text on pages 1-3 is drawn for the AI: this many pages from page 1,
# at this resolution. Read when drawing (not bound at import), so they can be tuned.
PAGE_IMAGE_PAGES = 1
PAGE_IMAGE_DPI = 120


def _has_meta_signals(text: str) -> bool:
    return any(kw in text for kw in _META_KEYWORDS)


def _resolve(file_path: str) -> Path:
    """Resolve relative paths against STORAGE_BASE_DIR (default ./reports), read
    at call time so pytest monkeypatch.setenv after module import still applies."""
    p = Path(file_path)
    if p.is_absolute():
        return p
    return settings.storage_base_dir() / p


def _sync_extract(path: Path, max_pages: int = MAX_PAGES) -> dict:
    """Walk pages 1..max_pages, stopping once the text read so far has meta signals.

    No text at all (missing or broken file, blank pages) → ``pdf_unreadable``.
    """
    parts: list[str] = []
    pages_used: list[int] = []
    for number, text in enumerate(pdf.page_texts(path, max_pages=max_pages), start=1):
        parts.append(text)
        pages_used.append(number)
        # Stop early if we have meta signals on the accumulated text
        if _has_meta_signals("\n".join(parts)):
            break
    pdf_text = "\n".join(parts).strip()
    return {
        "pdf_text": pdf_text,
        "pages_used": pages_used,
        "pdf_unreadable": not pdf_text,
    }


def _render_page_images(path: Path) -> list[bytes]:
    """PNGs of pages 1..PAGE_IMAGE_PAGES at PAGE_IMAGE_DPI.

    Stops at the first page that cannot be drawn and keeps the pages before it, so a
    PDF whose page 1 cannot be drawn (missing, broken, password-locked, 0 pages, a
    drawing error) gives ``[]``: never an exception, like the text walk.
    """
    images: list[bytes] = []
    for number in range(1, PAGE_IMAGE_PAGES + 1):
        try:
            images.append(pdf.render_page_png(path, number, dpi=PAGE_IMAGE_DPI))
        except Exception:
            break
    return images


def _sync_read(path: Path) -> dict:
    """The text walk, plus the page pictures (``page_images``) only when it found no text."""
    out = _sync_extract(path)
    if out["pdf_unreadable"]:
        out["page_images"] = _render_page_images(path)
    return out


async def extract_pdf(state: RowState) -> dict:
    """Read PDF first page; fall back up to 3 pages if metadata is sparse; with no text,
    draw page 1. Runs in a worker thread so the event loop is not blocked."""
    path = _resolve(state["file_path"])
    return await asyncio.to_thread(_sync_read, path)
