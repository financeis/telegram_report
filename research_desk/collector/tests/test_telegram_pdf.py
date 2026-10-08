"""Tests for has_pdf and _get_original_filename — both pure given a Telethon-shaped object.

We use simple namespace objects to mimic Telethon's Message structure without importing it.
Ported from the root tests/test_telegram_pdf.py.
"""
from __future__ import annotations

from types import SimpleNamespace

from research_desk.collector.telegram import _get_original_filename, has_pdf


def _msg_with_doc(mime_type: str | None, file_name: str | None) -> SimpleNamespace:
    """Build a minimal Telethon-Message-like object for testing."""
    attrs = []
    if file_name is not None:
        attrs.append(SimpleNamespace(file_name=file_name))
    doc = SimpleNamespace(mime_type=mime_type, attributes=attrs)
    return SimpleNamespace(document=doc)


def _msg_text_only() -> SimpleNamespace:
    return SimpleNamespace(document=None)


# === has_pdf ===

def test_has_pdf_text_only_message_returns_false():
    assert has_pdf(_msg_text_only()) is False


def test_has_pdf_pdf_mime_returns_true():
    msg = _msg_with_doc(mime_type='application/pdf', file_name='report.pdf')
    assert has_pdf(msg) is True


def test_has_pdf_falls_back_to_extension_when_mime_missing():
    msg = _msg_with_doc(mime_type='application/octet-stream', file_name='report.PDF')
    assert has_pdf(msg) is True


def test_has_pdf_no_mime_no_pdf_extension_returns_false():
    msg = _msg_with_doc(mime_type='application/zip', file_name='archive.zip')
    assert has_pdf(msg) is False


def test_has_pdf_no_filename_attr_falls_back_to_mime():
    msg = _msg_with_doc(mime_type='application/pdf', file_name=None)
    assert has_pdf(msg) is True


def test_has_pdf_no_filename_no_mime_returns_false():
    msg = _msg_with_doc(mime_type=None, file_name=None)
    assert has_pdf(msg) is False


# === _get_original_filename ===

def test_get_filename_returns_attr_value():
    msg = _msg_with_doc(mime_type='application/pdf', file_name='hello.pdf')
    assert _get_original_filename(msg) == 'hello.pdf'


def test_get_filename_returns_none_when_no_document():
    assert _get_original_filename(_msg_text_only()) is None


def test_get_filename_returns_none_when_no_filename_attribute():
    msg = _msg_with_doc(mime_type='application/pdf', file_name=None)
    assert _get_original_filename(msg) is None
