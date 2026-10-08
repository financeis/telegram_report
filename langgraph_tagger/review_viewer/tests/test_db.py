from unittest.mock import MagicMock

import pytest

from langgraph_tagger.review_viewer.db import ReviewDB


# === count_review_queue ===

def test_count_review_queue_uses_status_filter():
    client = MagicMock()
    table = client.table.return_value
    table.select.return_value.eq.return_value.execute.return_value = MagicMock(count=42)

    db = ReviewDB(client)
    n = db.count_review_queue()

    assert n == 42
    client.table.assert_called_with('reports')
    table.select.assert_called_with('id', count='exact')
    table.select.return_value.eq.assert_called_with('tagging_status', 'review_needed')


# === fetch_next_review ===

def test_fetch_next_review_orders_by_tagged_at_asc():
    client = MagicMock()
    chain = (client.table.return_value
                       .select.return_value
                       .eq.return_value
                       .order.return_value
                       .limit.return_value)
    chain.execute.return_value = MagicMock(data=[{'id': 1, 'tagging_notes': 'krx_unmatched_in_scope'}])

    db = ReviewDB(client)
    row = db.fetch_next_review(skipped_ids=set())

    assert row['id'] == 1
    chain_root = client.table.return_value.select.return_value.eq.return_value
    chain_root.order.assert_called_with('tagged_at', desc=False)


def test_fetch_next_review_excludes_skipped():
    client = MagicMock()
    chain = (client.table.return_value
                       .select.return_value
                       .eq.return_value
                       .not_
                       .in_.return_value
                       .order.return_value
                       .limit.return_value)
    chain.execute.return_value = MagicMock(data=[{'id': 7}])

    db = ReviewDB(client)
    row = db.fetch_next_review(skipped_ids={1, 2, 3})

    assert row['id'] == 7
    not_in_args = client.table.return_value.select.return_value.eq.return_value.not_.in_.call_args
    assert not_in_args.args[0] == 'id'
    assert set(not_in_args.args[1]) == {1, 2, 3}


def test_fetch_next_review_returns_none_when_empty():
    client = MagicMock()
    chain = (client.table.return_value
                       .select.return_value
                       .eq.return_value
                       .order.return_value
                       .limit.return_value)
    chain.execute.return_value = MagicMock(data=[])
    db = ReviewDB(client)
    assert db.fetch_next_review(set()) is None
