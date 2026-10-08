"""supabase-py REST reads for the review queue.

Sync, unlike the async asyncpg adapter in langgraph_tagger/supabase_io.py
used by the tagger pipeline. Review writes and undo live in
langgraph_tagger/workspace/review.py.
"""
from __future__ import annotations

from typing import Any, Iterable


class ReviewDB:
    """Thin facade over a supabase-py client.

    The client argument is intentionally typed `Any` so tests can pass
    MagicMock without dragging supabase-py imports into the test path.
    """

    def __init__(self, client: Any) -> None:
        self._sb = client

    def count_review_queue(self) -> int:
        result = (
            self._sb.table('reports')
            .select('id', count='exact')
            .eq('tagging_status', 'review_needed')
            .execute()
        )
        return int(result.count or 0)

    def fetch_next_review(self, skipped_ids: Iterable[int]) -> dict[str, Any] | None:
        skipped_list = list(skipped_ids)
        q = (
            self._sb.table('reports')
            .select('*')
            .eq('tagging_status', 'review_needed')
        )
        if skipped_list:
            q = q.not_.in_('id', skipped_list)
        q = q.order('tagged_at', desc=False).limit(1)
        result = q.execute()
        data = result.data or []
        return data[0] if data else None
