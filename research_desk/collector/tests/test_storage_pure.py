"""sanitize_filename and compute_sha256 (ported from the root tests/test_storage_pure.py)."""
from __future__ import annotations

from research_desk.collector.storage import compute_sha256, sanitize_filename


# === sanitize_filename ===

def test_sanitize_normal_filename_unchanged():
    assert sanitize_filename('삼성전자_2026Q1.pdf') == '삼성전자_2026Q1.pdf'


def test_sanitize_strips_forbidden_chars():
    # Windows-forbidden: < > : " / \ | ? *
    raw = 'bad<name>:"with"/forbidden\\chars|?.pdf'
    out = sanitize_filename(raw)
    for ch in '<>:"/\\|?':
        assert ch not in out
    assert out.endswith('.pdf')


def test_sanitize_strips_control_chars():
    raw = 'file\x00with\x1fcontrol.pdf'
    out = sanitize_filename(raw)
    assert '\x00' not in out
    assert '\x1f' not in out


def test_sanitize_strips_leading_trailing_dots_spaces():
    assert sanitize_filename('  .file.pdf.  ').strip('. ') == sanitize_filename('  .file.pdf.  ')
    assert not sanitize_filename('  .file.pdf.  ').startswith('.')
    assert not sanitize_filename('  .file.pdf.  ').endswith(' ')


def test_sanitize_appends_pdf_when_missing():
    assert sanitize_filename('no_extension').endswith('.pdf')


def test_sanitize_keeps_pdf_when_present():
    out = sanitize_filename('already.pdf')
    assert out.lower().count('.pdf') == 1


def test_sanitize_truncates_long_names():
    long_name = 'x' * 200 + '.pdf'
    out = sanitize_filename(long_name, max_len=100)
    assert len(out) <= 100
    assert out.endswith('.pdf')


def test_sanitize_empty_input_becomes_unnamed():
    assert sanitize_filename('') == 'unnamed.pdf'


def test_sanitize_only_dots_and_spaces_becomes_unnamed():
    out = sanitize_filename('   ...   ')
    # After stripping dots/spaces, empty → fallback to 'unnamed' + '.pdf'
    assert out == 'unnamed.pdf'


# === compute_sha256 ===

def test_compute_sha256_known_value(tmp_path):
    f = tmp_path / 'test.bin'
    f.write_bytes(b'hello world')
    # Known sha256 of "hello world"
    expected = 'b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9'
    assert compute_sha256(f) == expected


def test_compute_sha256_empty_file(tmp_path):
    f = tmp_path / 'empty.bin'
    f.write_bytes(b'')
    expected = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
    assert compute_sha256(f) == expected


def test_compute_sha256_handles_large_file_in_chunks(tmp_path):
    # Verify we don't OOM by reading whole file at once
    f = tmp_path / 'big.bin'
    # 5 MB file
    f.write_bytes(b'A' * (5 * 1024 * 1024))
    result = compute_sha256(f)
    assert isinstance(result, str)
    assert len(result) == 64  # sha256 hex digest length
