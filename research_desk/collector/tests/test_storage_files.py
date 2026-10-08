"""Tests for Storage.save_pdf_atomically — uses tmp_path; no Supabase needed.

Ported from the root tests/test_storage_files.py.
"""
from __future__ import annotations

import pytest

from research_desk.collector.storage import Storage


@pytest.fixture
def storage(tmp_path) -> Storage:
    """Storage with a None supabase client (filesystem-only tests)."""
    return Storage(supabase_client=None, base_dir=tmp_path)


def test_save_pdf_writes_file_to_base_dir(storage, tmp_path):
    target = storage.save_pdf_atomically(b'fake pdf content', '12345_report.pdf')
    assert target == tmp_path / '12345_report.pdf'
    assert target.read_bytes() == b'fake pdf content'


def test_save_pdf_creates_base_dir_if_missing(tmp_path):
    nested = tmp_path / 'nested' / 'dir'
    storage = Storage(supabase_client=None, base_dir=nested)
    target = storage.save_pdf_atomically(b'data', 'x.pdf')
    assert nested.exists()
    assert target.read_bytes() == b'data'


def test_save_pdf_is_atomic_no_partial_left_after_success(storage, tmp_path):
    storage.save_pdf_atomically(b'ok', '1_a.pdf')
    partials = list(tmp_path.glob('*.partial'))
    assert partials == []


def test_save_pdf_overwrites_existing_target(storage, tmp_path):
    target = tmp_path / 'dup.pdf'
    target.write_bytes(b'old')
    storage.save_pdf_atomically(b'new', 'dup.pdf')
    assert target.read_bytes() == b'new'


def test_save_pdf_overwrites_stale_partial(storage, tmp_path):
    # Simulate leftover from a previous interrupted run
    stale = tmp_path / 'fresh.pdf.partial'
    stale.write_bytes(b'leftover')
    storage.save_pdf_atomically(b'fresh', 'fresh.pdf')
    assert (tmp_path / 'fresh.pdf').read_bytes() == b'fresh'
    # Atomic rename should have consumed/replaced the .partial
    assert not stale.exists()
