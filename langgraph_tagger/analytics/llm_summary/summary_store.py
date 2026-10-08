"""DB CRUD for report_summaries.

REST (supabase-py): fetch / upsert / update_diff.
"""
from __future__ import annotations

from typing import Any, Literal, Optional


def fetch_summaries(
    sb,                                  # supabase-py client
    report_ids: list[int],
    active_version: str,
) -> dict[int, dict[str, Any]]:
    """active version에 해당하는 summary들만 cache lookup. 다른 버전은 cache miss로 취급."""
    if not report_ids:
        return {}
    resp = (
        sb.table('report_summaries')
          .select('*')
          .in_('report_id', report_ids)
          .eq('summary_version', active_version)
          .execute()
    )
    return {row['report_id']: row for row in (resp.data or [])}


def upsert_summary(sb, payload: dict[str, Any]) -> None:
    """INSERT or full-replace UPDATE. diff 필드는 항상 NULL로 reset됨.

    Caller가 payload 빌드 시 prev_report_id/prev_match_type/diff_narrative를
    명시적으로 None으로 채워야 함 (이 함수가 강제하진 않지만 spec §10 약속).
    """
    assert payload.get('prev_report_id') is None, "upsert payload must NULL prev_report_id (diff reset)"
    assert payload.get('prev_match_type') is None, "upsert payload must NULL prev_match_type (diff reset)"
    assert payload.get('diff_narrative') is None, "upsert payload must NULL diff_narrative (diff reset)"
    sb.table('report_summaries').upsert(payload, on_conflict='report_id').execute()


def update_diff(
    sb,
    report_id: int,
    prev_report_id: Optional[int],
    match_type: Literal['same_publisher', 'cross_publisher', 'none'],
    narrative: Optional[str],
    comparison_details: Optional[dict] = None,
) -> None:
    """비교 결과 — diff 필드만 부분 update."""
    sb.table('report_summaries').update({
        'prev_report_id': prev_report_id,
        'prev_match_type': match_type,
        'diff_narrative': narrative,
        'comparison_details': comparison_details,
    }).eq('report_id', report_id).execute()
