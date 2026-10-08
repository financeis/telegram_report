"""extract_pdf node: read page 1, falling back up to page 3 if metadata is sparse."""
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


async def extract_pdf(state: RowState) -> dict:
    """Read PDF first page; fall back up to 3 pages if metadata is sparse."""
    path = _resolve(state["file_path"])
    return await asyncio.to_thread(_sync_extract, path)
