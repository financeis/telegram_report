"""features.review.rules: what each review action writes, and the undo snapshot.

Ported from langgraph_tagger/review_viewer/tests/test_actions.py (every test, unchanged in
meaning; the import path is the new one).

New (spec §4, §9.2, §9.8):
- the 22 snapshot columns, in their old order;
- the out-of-scope payload is ``tagging_status = 'verified'`` plus ``domain.reports.oos_row_shape``
  and is exactly what the old build_oos_payload wrote, for full rows and for rows with missing or
  null columns; the ValueError text for an unknown reason is the old one;
- the retag payload is exactly the old reset and covers exactly the snapshot columns;
- every action writes only snapshot columns (so the undo can put each of them back), and each call
  gives fresh lists.
"""
import pytest

from research_desk.domain.reports import OOS_REASONS, oos_row_shape
from research_desk.features.review.rules import (
    SNAPSHOT_COLUMNS,
    capture_snapshot,
    build_verified_payload,
    build_oos_payload,
    build_pending_reset_payload,
)


def make_row(**overrides):
    """Factory for a representative review_needed row dict (supabase-py response shape)."""
    base = {
        # original meta — must NEVER appear in snapshot or payload
        'id': 1234,
        'message_id': 124784,
        'chat_username': 'example_channel',
        'file_path': '124784_some.pdf',
        'file_name': 'some.pdf',
        'file_size_bytes': 12345,
        'file_hash_sha256': 'a' * 64,
        'caption': 'Sample',
        'downloaded_at': '2026-05-07T08:04:14+00:00',
        'sent_at': '2026-05-07T08:04:14+00:00',
        # analysis body
        'published_at': '2026-05-07',
        'report_type': '단일종목',
        'publisher': '삼성증권',
        'publisher_type': 'broker',
        'analysts': ['홍길동'],
        'title': '삼성전자 Q1',
        'stock_codes': ['005930'],
        'company_names': ['삼성전자'],
        'stock_codes_raw': ['005935'],
        'company_names_raw': ['삼성전자우'],
        'sectors_major': ['반도체'],
        'sectors_minor': ['메모리'],
        'products': ['DRAM'],
        'out_of_scope_reason': None,
        # tagging meta
        'tagging_status': 'review_needed',
        'tagging_confidence': 'medium',
        'tagging_notes': 'krx_unmatched_in_scope',
        'tagged_at': '2026-05-07T08:30:00+00:00',
        'tagger_version': 'langgraph-tagger@2.0',
        'taxonomy_version': 'KRX@2026-05-08',
        'tagging_locked_at': None,
        'tagging_worker_id': None,
    }
    base.update(overrides)
    return base


ORIGINAL_META = {
    'id', 'message_id', 'chat_username', 'file_path', 'file_name',
    'file_size_bytes', 'file_hash_sha256', 'caption',
    'downloaded_at', 'sent_at',
}


# ── ported: SNAPSHOT_COLUMNS ─────────────────────────────────────────────────

def test_snapshot_columns_excludes_original_meta():
    """Original / message / file meta must never be in the allowlist —
    undo restores tagging state only, not the row's identity."""
    forbidden = {
        'id', 'message_id', 'chat_username', 'file_path', 'file_name',
        'file_size_bytes', 'file_hash_sha256', 'caption',
        'downloaded_at', 'sent_at',
    }
    assert forbidden.isdisjoint(set(SNAPSHOT_COLUMNS))


def test_snapshot_columns_covers_all_action_writes():
    """All columns that any action writes must be in the allowlist —
    otherwise undo can't restore them."""
    expected = {
        # analysis
        'published_at', 'report_type', 'publisher', 'publisher_type',
        'analysts', 'title', 'stock_codes', 'company_names',
        'stock_codes_raw', 'company_names_raw',
        'sectors_major', 'sectors_minor', 'products',
        'out_of_scope_reason',
        # tagging meta
        'tagging_status', 'tagging_confidence', 'tagging_notes',
        'tagged_at', 'tagger_version', 'taxonomy_version',
        'tagging_locked_at', 'tagging_worker_id',
    }
    assert set(SNAPSHOT_COLUMNS) == expected


# ── ported: capture_snapshot ─────────────────────────────────────────────────

def test_capture_snapshot_picks_only_allowlist():
    row = make_row()
    snap = capture_snapshot(row)
    assert set(snap.keys()) == set(SNAPSHOT_COLUMNS)
    assert snap['tagging_status'] == 'review_needed'
    assert snap['tagging_notes'] == 'krx_unmatched_in_scope'
    # original meta must not leak
    assert 'message_id' not in snap
    assert 'file_path' not in snap


# ── ported: build_verified_payload ───────────────────────────────────────────

def test_build_verified_payload_only_changes_status():
    row = make_row()
    payload = build_verified_payload(row)
    assert payload == {'tagging_status': 'verified'}


# ── ported: build_oos_payload ────────────────────────────────────────────────

def test_build_oos_payload_matches_write_node_semantics():
    """OOS payload must mirror the tagger's OOS write (the shared out-of-scope row shape):
    - tagging_status='verified' (manual review concluded)
    - out_of_scope_reason=<reason>
    - analysis-body fields cleared (stock_codes/names/sectors/products, published_at)
    - LLM classification preserved (report_type, publisher, title, analysts, *_raw)
    """
    row = make_row()
    payload = build_oos_payload(row, reason='foreign')

    assert payload['tagging_status'] == 'verified'
    assert payload['out_of_scope_reason'] == 'foreign'
    # cleared:
    assert payload['published_at'] is None
    assert payload['stock_codes'] == []
    assert payload['company_names'] == []
    assert payload['sectors_major'] == []
    assert payload['sectors_minor'] == []
    assert payload['products'] == []
    # preserved (from row):
    assert payload['report_type'] == '단일종목'
    assert payload['publisher'] == '삼성증권'
    assert payload['publisher_type'] == 'broker'
    assert payload['title'] == '삼성전자 Q1'
    assert payload['analysts'] == ['홍길동']
    assert payload['stock_codes_raw'] == ['005935']
    assert payload['company_names_raw'] == ['삼성전자우']


def test_build_oos_payload_rejects_unknown_reason():
    row = make_row()
    with pytest.raises(ValueError):
        build_oos_payload(row, reason='not_a_real_reason')


def test_build_oos_payload_accepts_all_v2_reasons():
    row = make_row()
    for reason in ('foreign', 'fund', 'digital', 'private', 'ir_self'):
        p = build_oos_payload(row, reason=reason)
        assert p['out_of_scope_reason'] == reason


# ── ported: build_pending_reset_payload ──────────────────────────────────────

def test_build_pending_reset_clears_analysis_and_meta():
    """Re-tag must mirror migration 003's reset: every analysis column +
    every tagging-meta column gets emptied/nulled."""
    payload = build_pending_reset_payload()
    # status / meta
    assert payload['tagging_status'] == 'pending'
    assert payload['tagging_locked_at'] is None
    assert payload['tagging_worker_id'] is None
    assert payload['tagged_at'] is None
    assert payload['tagging_notes'] is None
    assert payload['tagging_confidence'] is None
    assert payload['tagger_version'] is None
    assert payload['taxonomy_version'] is None
    # analysis
    assert payload['published_at'] is None
    assert payload['report_type'] is None
    assert payload['publisher'] is None
    assert payload['publisher_type'] is None
    assert payload['analysts'] == []
    assert payload['title'] is None
    assert payload['stock_codes'] == []
    assert payload['company_names'] == []
    assert payload['stock_codes_raw'] == []
    assert payload['company_names_raw'] == []
    assert payload['sectors_major'] == []
    assert payload['sectors_minor'] == []
    assert payload['products'] == []
    assert payload['out_of_scope_reason'] is None


def test_build_pending_reset_does_not_touch_original_meta():
    payload = build_pending_reset_payload()
    forbidden = {
        'id', 'message_id', 'chat_username', 'file_path', 'file_name',
        'file_size_bytes', 'file_hash_sha256', 'caption',
        'downloaded_at', 'sent_at',
    }
    assert forbidden.isdisjoint(set(payload.keys()))


# ── ported: snapshot round-trip ──────────────────────────────────────────────

def test_snapshot_restore_round_trip():
    """The snapshot returned by capture_snapshot must be applicable as an
    UPDATE payload that restores the row state."""
    row = make_row()
    snap = capture_snapshot(row)
    # Sanity: applying snap as payload would set every allowlist field back
    # to its original. Field-by-field equality.
    for col in SNAPSHOT_COLUMNS:
        assert snap[col] == row[col], f"round-trip failed for {col}"


# ── new: the snapshot columns ────────────────────────────────────────────────

def test_the_snapshot_columns_are_the_old_22_in_order():
    assert SNAPSHOT_COLUMNS == (
        'published_at', 'report_type', 'publisher', 'publisher_type', 'analysts', 'title',
        'stock_codes', 'company_names', 'stock_codes_raw', 'company_names_raw',
        'sectors_major', 'sectors_minor', 'products', 'out_of_scope_reason',
        'tagging_status', 'tagging_confidence', 'tagging_notes', 'tagged_at',
        'tagger_version', 'taxonomy_version', 'tagging_locked_at', 'tagging_worker_id',
    )
    assert len(SNAPSHOT_COLUMNS) == len(set(SNAPSHOT_COLUMNS)) == 22


def test_a_snapshot_shows_missing_columns_as_null():
    snap = capture_snapshot({'id': 1, 'tagging_status': 'review_needed', 'caption': 'x'})
    assert snap == dict.fromkeys(SNAPSHOT_COLUMNS) | {'tagging_status': 'review_needed'}


# ── new: the out-of-scope payload is the shared shape (spec §4, §9.2) ────────

# What the old build_oos_payload wrote for make_row() and 'foreign', key by key.
OLD_OOS_PAYLOAD = {
    'tagging_status': 'verified',
    'out_of_scope_reason': 'foreign',
    'published_at': None,
    'stock_codes': [],
    'company_names': [],
    'sectors_major': [],
    'sectors_minor': [],
    'products': [],
    'report_type': '단일종목',
    'publisher': '삼성증권',
    'publisher_type': 'broker',
    'analysts': ['홍길동'],
    'title': '삼성전자 Q1',
    'stock_codes_raw': ['005935'],
    'company_names_raw': ['삼성전자우'],
}

ROWS = {
    'full row': make_row(),
    'null arrays': make_row(analysts=None, stock_codes_raw=None, company_names_raw=None),
    'missing columns': {'id': 1, 'tagging_status': 'review_needed', 'report_type': '기타'},
    'out of scope already': make_row(out_of_scope_reason='fund', published_at=None, stock_codes=[]),
}


def test_the_oos_payload_is_exactly_what_the_old_one_wrote():
    payload = build_oos_payload(make_row(), 'foreign')
    assert payload == OLD_OOS_PAYLOAD
    assert list(payload) == list(OLD_OOS_PAYLOAD)


@pytest.mark.parametrize('reason', OOS_REASONS)
@pytest.mark.parametrize('row', list(ROWS.values()), ids=list(ROWS))
def test_the_oos_payload_is_verified_plus_the_shared_oos_row_shape(row, reason):
    assert build_oos_payload(row, reason) == {'tagging_status': 'verified', **oos_row_shape(row, reason)}


def test_null_or_missing_kept_arrays_become_empty_lists():
    for row in (ROWS['null arrays'], ROWS['missing columns']):
        payload = build_oos_payload(row, 'private')
        assert payload['analysts'] == [] and payload['stock_codes_raw'] == []
        assert payload['company_names_raw'] == []
    assert build_oos_payload(ROWS['missing columns'], 'private')['report_type'] == '기타'
    assert build_oos_payload(ROWS['missing columns'], 'private')['title'] is None


def test_the_oos_payload_copies_the_kept_arrays():
    row = make_row()
    payload = build_oos_payload(row, 'digital')
    payload['analysts'].append('누군가')
    payload['stock_codes_raw'].clear()
    assert row['analysts'] == ['홍길동'] and row['stock_codes_raw'] == ['005935']


@pytest.mark.parametrize('reason', ['not_a_real_reason', None, '', 'FOREIGN'])
def test_an_unknown_reason_is_the_old_value_error(reason):
    with pytest.raises(ValueError) as exc:
        build_oos_payload(make_row(), reason)
    assert str(exc.value) == (
        f"reason must be one of ('foreign', 'fund', 'digital', 'private', 'ir_self'), got: {reason!r}")


# ── new: the retag payload and what the actions write ────────────────────────

def test_the_retag_payload_is_exactly_the_old_reset():
    assert build_pending_reset_payload() == {
        'tagging_status': 'pending', 'tagging_locked_at': None, 'tagging_worker_id': None,
        'tagged_at': None, 'tagging_notes': None, 'tagging_confidence': None,
        'tagger_version': None, 'taxonomy_version': None,
        'published_at': None, 'report_type': None, 'publisher': None, 'publisher_type': None,
        'analysts': [], 'title': None, 'stock_codes': [], 'company_names': [],
        'stock_codes_raw': [], 'company_names_raw': [], 'sectors_major': [],
        'sectors_minor': [], 'products': [], 'out_of_scope_reason': None,
    }
    assert set(build_pending_reset_payload()) == set(SNAPSHOT_COLUMNS)


def test_every_action_writes_only_snapshot_columns():
    row = make_row()
    payloads = [build_verified_payload(row), build_pending_reset_payload(),
                *(build_oos_payload(row, reason) for reason in OOS_REASONS)]
    for payload in payloads:
        assert set(payload) <= set(SNAPSHOT_COLUMNS)
        assert ORIGINAL_META.isdisjoint(payload)


def test_each_call_gives_fresh_lists():
    first = build_pending_reset_payload()
    first['stock_codes'].append('005930')
    first['analysts'].append('홍길동')
    assert build_pending_reset_payload()['stock_codes'] == []
    assert build_pending_reset_payload()['analysts'] == []
    oos = build_oos_payload(make_row(), 'fund')
    oos['products'].append('DRAM')
    assert build_oos_payload(make_row(), 'fund')['products'] == []
