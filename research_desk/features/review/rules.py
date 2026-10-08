"""What each manual review action writes, and the undo snapshot (spec §9.8).

Moved from langgraph_tagger/review_viewer/actions.py with the same values, so a review decision
leaves the row in the shape the tagger would have produced:

- verify (승인) → only ``tagging_status = 'verified'``; the classification stays;
- oos (제외) → ``tagging_status = 'verified'`` plus the shared out-of-scope row shape of
  ``domain.reports.oos_row_shape`` with the reason (spec §4, §9.2): published_at and the
  stock / company / sector / product arrays emptied, the classification and the raw audit
  columns kept from the row;
- retag (재분류) → the old reset values: every classification and tagging-meta column emptied
  and ``tagging_status = 'pending'``, so the next tagger run classifies the row afresh.

``SNAPSHOT_COLUMNS`` are the 22 columns the actions may write. The undo keeps the row's values in
these columns before and after an action and restores only them; collector columns (message,
file and channel data) never appear here.
"""
from __future__ import annotations

from typing import Any, Mapping

from research_desk.domain.reports import oos_row_shape

SNAPSHOT_COLUMNS: tuple[str, ...] = (
    # classification
    'published_at',
    'report_type',
    'publisher',
    'publisher_type',
    'analysts',
    'title',
    'stock_codes',
    'company_names',
    'stock_codes_raw',
    'company_names_raw',
    'sectors_major',
    'sectors_minor',
    'products',
    'out_of_scope_reason',
    # tagging meta
    'tagging_status',
    'tagging_confidence',
    'tagging_notes',
    'tagged_at',
    'tagger_version',
    'taxonomy_version',
    'tagging_locked_at',
    'tagging_worker_id',
)


def capture_snapshot(row: Mapping[str, Any]) -> dict[str, Any]:
    """The row's values in ``SNAPSHOT_COLUMNS`` (None for a column the row lacks)."""
    return {col: row.get(col) for col in SNAPSHOT_COLUMNS}


def build_verified_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    """Verify: keep the classification, only mark the row verified."""
    return {'tagging_status': 'verified'}


def build_oos_payload(row: Mapping[str, Any], reason: str) -> dict[str, Any]:
    """Out of scope: verified, with the shared out-of-scope row shape for ``reason``.

    Raises ValueError when ``reason`` is not one of ``domain.reports.OOS_REASONS``.
    """
    return {'tagging_status': 'verified', **oos_row_shape(row, reason)}


def build_pending_reset_payload() -> dict[str, Any]:
    """Retag: empty every classification and tagging-meta column and set the row pending."""
    return {
        # tagging meta
        'tagging_status': 'pending',
        'tagging_locked_at': None,
        'tagging_worker_id': None,
        'tagged_at': None,
        'tagging_notes': None,
        'tagging_confidence': None,
        'tagger_version': None,
        'taxonomy_version': None,
        # classification
        'published_at': None,
        'report_type': None,
        'publisher': None,
        'publisher_type': None,
        'analysts': [],
        'title': None,
        'stock_codes': [],
        'company_names': [],
        'stock_codes_raw': [],
        'company_names_raw': [],
        'sectors_major': [],
        'sectors_minor': [],
        'products': [],
        'out_of_scope_reason': None,
    }
