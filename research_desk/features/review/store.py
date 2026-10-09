"""DB reads and writes of the manual review (Supabase REST), on the reports table.

Review owns these reads and writes of the reports table (spec §4): the review queue, one row by
id, and the guarded writes of a review action and of its undo. The queries are the ones of
langgraph_tagger/review_viewer/db.py (the queue) and langgraph_tagger/workspace/review.py (the
row, the writes):

- the queue count: ``tagging_status = 'review_needed'``, counted exactly by the server;
- the next row in the queue: every column of the ``review_needed`` row with the oldest
  ``tagged_at``, leaving out the skipped ids;
- one row by id: every column, whatever its status;
- a guarded write: ``UPDATE`` of one row by id that holds only while each expected column still
  has the expected value (``eq``, or ``IS NULL`` for None). The rows written come back, so an
  empty answer means the row had changed (or is gone) and nothing was written.

The client argument is typed ``Any`` so tests can pass a stand-in.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

REVIEW_NEEDED = 'review_needed'


class ReviewStore:
    """Review's reads and writes over a supabase-py client."""

    def __init__(self, client: Any) -> None:
        self._sb = client

    def count_review_queue(self) -> int:
        """How many rows wait for review (an exact count)."""
        result = (
            self._sb.table('reports')
            .select('id', count='exact')
            .eq('tagging_status', REVIEW_NEEDED)
            .execute()
        )
        return int(result.count or 0)

    def fetch_next_review(self, skipped_ids: Iterable[int]) -> Optional[dict[str, Any]]:
        """The waiting row tagged longest ago, apart from ``skipped_ids``; None when there is none."""
        skipped_list = list(skipped_ids)
        q = (
            self._sb.table('reports')
            .select('*')
            .eq('tagging_status', REVIEW_NEEDED)
        )
        if skipped_list:
            q = q.not_.in_('id', skipped_list)
        q = q.order('tagged_at', desc=False).limit(1)
        result = q.execute()
        data = result.data or []
        return data[0] if data else None

    def fetch_row(self, rid: int) -> Optional[dict[str, Any]]:
        """Every column of row ``rid``, whatever its status; None when there is no such row."""
        rows = self._sb.table('reports').select('*').eq('id', rid).execute().data
        return rows[0] if rows else None

    def update_if(self, rid: int, values: Mapping[str, Any],
                  expected: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Write ``values`` to row ``rid`` only while every ``expected`` column still holds its
        value (None: still null), checked in the given order. Returns the rows written: an empty
        list when nothing was written."""
        query = self._sb.table('reports').update(values).eq('id', rid)
        for column, value in expected.items():
            query = query.is_(column, 'null') if value is None else query.eq(column, value)
        return query.execute().data or []
