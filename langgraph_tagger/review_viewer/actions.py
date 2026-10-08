"""Payload builders for the 4 review actions, plus snapshot allowlist.

Semantics mirror langgraph_tagger/nodes/write.py and migrations/003_v2_redesign.sql
so manual review decisions land the row in exactly the same shape the tagger
would have produced.
"""
from __future__ import annotations

from typing import Any

V2_OOS_REASONS: tuple[str, ...] = ('foreign', 'fund', 'digital', 'private', 'ir_self')

# Columns review actions may write. capture_snapshot uses them for the undo
# snapshot that workspace/review.py restores. Original / message / file meta
# NEVER appear here.
SNAPSHOT_COLUMNS: tuple[str, ...] = (
    # analysis body
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


def capture_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    """Pick the allowlist columns out of a fetched row for later undo."""
    return {col: row.get(col) for col in SNAPSHOT_COLUMNS}


def build_verified_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Manual verified: keep LLM extraction, only flip status."""
    return {'tagging_status': 'verified'}


def build_oos_payload(row: dict[str, Any], reason: str) -> dict[str, Any]:
    """Manual OOS: mirror write.py's OOS branch — clear analysis body,
    preserve LLM classification + raw audit, set reason + verified.
    """
    if reason not in V2_OOS_REASONS:
        raise ValueError(
            f"reason must be one of {V2_OOS_REASONS}, got: {reason!r}"
        )
    return {
        'tagging_status': 'verified',
        'out_of_scope_reason': reason,
        'published_at': None,
        # analysis body cleared
        'stock_codes': [],
        'company_names': [],
        'sectors_major': [],
        'sectors_minor': [],
        'products': [],
        # LLM classification + raw audit preserved
        'report_type': row.get('report_type'),
        'publisher': row.get('publisher'),
        'publisher_type': row.get('publisher_type'),
        'analysts': list(row.get('analysts') or []),
        'title': row.get('title'),
        'stock_codes_raw': list(row.get('stock_codes_raw') or []),
        'company_names_raw': list(row.get('company_names_raw') or []),
    }


def build_pending_reset_payload() -> dict[str, Any]:
    """Re-tag: mirror migration 003 reset — clear every analysis + tagging-meta
    column so the next tagger batch produces a fresh result.
    """
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
        # analysis body
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
