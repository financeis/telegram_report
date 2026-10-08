"""Page-marked PDF text with the token cap.

Ported from langgraph_tagger/analytics/llm_summary/tests/test_pdf_text.py, plus
exact-format and cap tests for the builder on top of core.pdf.page_texts.
"""
from pathlib import Path

import pymupdf
import pytest

from research_desk.features.analysis import pdf_text
from research_desk.features.analysis.pdf_text import (
    PDFTextResult, build_pages_text, extract_all_pages,
)


@pytest.fixture
def make_pdf(tmp_path):
    """Helper to write a PDF with N pages of given text.

    PyMuPDF's default font (Helvetica) cannot embed CJK glyphs via insert_text,
    so we use ASCII bodies for round-trippability. Real Korean PDFs embed their
    own CJK fonts.
    """
    def _make(num_pages: int, text_per_page: str = 'body text') -> Path:
        path = tmp_path / f'test_{num_pages}p.pdf'
        doc = pymupdf.open()
        for _ in range(num_pages):
            page = doc.new_page()
            page.insert_text((72, 72), text_per_page)
        doc.save(str(path))
        doc.close()
        return path
    return _make


def test_extract_simple_no_truncate(make_pdf):
    pdf = make_pdf(3, 'simple body')
    r = extract_all_pages(pdf, max_tokens=100000)
    assert isinstance(r, PDFTextResult)
    assert r.total_pages == 3
    assert r.pages_used == 3
    assert r.input_truncated is False
    assert '--- Page 1 ---' in r.text
    assert '--- Page 2 ---' in r.text
    assert '--- Page 3 ---' in r.text
    assert 'simple body' in r.text


def test_extract_truncate_when_cap_low(make_pdf):
    # 페이지마다 ~50 chars body + ~16 chars header ≈ ~22 tokens/page.
    # max_tokens=50 → page 1, 2는 들어가고 page 3부터 truncate.
    body = 'x' * 50  # 50 chars body → ~22 tokens per chunk including header
    pdf = make_pdf(10, body)
    r = extract_all_pages(pdf, max_tokens=50)
    assert r.input_truncated is True
    assert r.pages_used < r.total_pages
    assert r.pages_used > 0  # 최소 1페이지는 들어감
    assert r.total_pages == 10
    assert r.estimated_input_tokens > 0


def test_extract_missing_file_returns_empty(tmp_path):
    fake = tmp_path / 'nope.pdf'
    r = extract_all_pages(fake, max_tokens=10000)
    assert r.text == ''
    assert r.pages_used == 0
    assert r.total_pages == 0
    assert r.input_truncated is False


def test_extract_total_pages_correct(make_pdf):
    pdf = make_pdf(7)
    r = extract_all_pages(pdf, max_tokens=100000)
    assert r.total_pages == 7
    assert r.pages_used == 7


def test_unreadable_file_returns_empty(tmp_path):
    broken = tmp_path / 'broken.pdf'
    broken.write_bytes(b'not a pdf at all')
    assert extract_all_pages(broken, max_tokens=10000) == PDFTextResult('', 0, 0, False, 0)


def test_extract_uses_core_page_texts(monkeypatch, tmp_path):
    # The text comes from core.pdf.page_texts (all pages, in order).
    path = tmp_path / 'r.pdf'
    path.write_bytes(b'%PDF-1.4')
    seen = []

    def fake_page_texts(p, max_pages=None):
        seen.append((Path(p), max_pages))
        return ['first', 'second']

    monkeypatch.setattr(pdf_text.pdf, 'page_texts', fake_page_texts)
    r = extract_all_pages(path, max_tokens=100000)
    assert seen == [(path, None)]
    assert r.text == '--- Page 1 ---\nfirst\n--- Page 2 ---\nsecond\n'


def test_page_marker_format_is_exact():
    r = build_pages_text(['alpha', '', 'gamma\nline'], max_tokens=100000)
    assert r.text == ('--- Page 1 ---\nalpha\n'
                      '--- Page 2 ---\n\n'
                      '--- Page 3 ---\ngamma\nline\n')
    assert (r.pages_used, r.total_pages, r.input_truncated) == (3, 3, False)


def test_tokens_are_characters_divided_by_three_per_page():
    chunks = ['--- Page 1 ---\n' + 'a' * 30 + '\n', '--- Page 2 ---\n' + 'b' * 7 + '\n']
    r = build_pages_text(['a' * 30, 'b' * 7], max_tokens=100000)
    assert r.estimated_input_tokens == sum(len(c) // 3 for c in chunks)
    # every chunk counts at least one token
    assert pdf_text._estimate_tokens('') == 1


def test_pages_from_the_first_one_over_the_cap_are_left_out():
    first = '--- Page 1 ---\n' + 'a' * 45 + '\n'      # 61 chars → 20 tokens
    second_body = 'b' * 300                           # far over the remaining room
    r = build_pages_text(['a' * 45, second_body, 'c'], max_tokens=len(first) // 3 + 5)
    # page 3 alone would fit, but nothing after the first page over the cap goes in
    assert r.text == first
    assert (r.pages_used, r.total_pages) == (1, 3)
    assert r.input_truncated is True
    assert r.estimated_input_tokens == len(first) // 3
    # no truncation marker is added to the text
    assert 'truncat' not in r.text.lower()


def test_cap_exactly_reached_is_not_truncated():
    chunk = '--- Page 1 ---\n' + 'a' * 45 + '\n'
    r = build_pages_text(['a' * 45], max_tokens=len(chunk) // 3)
    assert r.input_truncated is False and r.pages_used == 1


def test_first_page_over_the_cap_gives_empty_text():
    r = build_pages_text(['a' * 300], max_tokens=10)
    assert r == PDFTextResult('', 0, 1, True, 0)
