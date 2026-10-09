"""features.review.store: the review's reads and writes on the reports table.

Ported from langgraph_tagger/review_viewer/tests/test_db.py (every test, unchanged in meaning;
``ReviewDB`` is now ``ReviewStore``).

New: the row read by id and the guarded write (the queries langgraph_tagger/workspace/review.py
ran inline): the exact supabase-py calls, and what comes back.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from research_desk.features.review.store import ReviewStore


# === count_review_queue ===

def test_count_review_queue_uses_status_filter():
    client = MagicMock()
    table = client.table.return_value
    table.select.return_value.eq.return_value.execute.return_value = MagicMock(count=42)

    db = ReviewStore(client)
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

    db = ReviewStore(client)
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

    db = ReviewStore(client)
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
    db = ReviewStore(client)
    assert db.fetch_next_review(set()) is None


# === new: the queue reads in full ===

def test_the_queue_reads_are_exactly_the_old_calls():
    client = MagicMock()
    table = client.table.return_value
    select = table.select.return_value.eq.return_value
    select.order.return_value.limit.return_value.execute.return_value = MagicMock(data=None)

    assert ReviewStore(client).fetch_next_review([]) is None   # no data at all is None too

    client.table.assert_called_once_with('reports')
    table.select.assert_called_once_with('*')
    table.select.return_value.eq.assert_called_once_with('tagging_status', 'review_needed')
    select.order.assert_called_once_with('tagged_at', desc=False)
    select.order.return_value.limit.assert_called_once_with(1)
    assert not select.not_.in_.called   # nothing skipped: no id filter


def test_the_skipped_ids_are_given_as_a_list_in_their_order():
    client = MagicMock()
    select = client.table.return_value.select.return_value.eq.return_value
    ReviewStore(client).fetch_next_review(iter([5, 3, 9]))
    select.not_.in_.assert_called_once_with('id', [5, 3, 9])
    select.not_.in_.return_value.order.assert_called_once_with('tagged_at', desc=False)
    select.not_.in_.return_value.order.return_value.limit.assert_called_once_with(1)


def test_an_unknown_count_is_zero():
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(count=None)
    assert ReviewStore(client).count_review_queue() == 0


# === new: one row by id ===

def test_fetch_row_reads_every_column_of_that_id_whatever_its_status():
    client = MagicMock()
    table = client.table.return_value
    table.select.return_value.eq.return_value.execute.return_value = MagicMock(
        data=[{'id': 7, 'tagging_status': 'auto'}])

    assert ReviewStore(client).fetch_row(7) == {'id': 7, 'tagging_status': 'auto'}
    client.table.assert_called_once_with('reports')
    table.select.assert_called_once_with('*')
    table.select.return_value.eq.assert_called_once_with('id', 7)   # no status filter


def test_fetch_row_is_none_without_that_row():
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
    assert ReviewStore(client).fetch_row(7) is None


# === new: the guarded write ===

def test_update_if_writes_one_row_while_each_expected_value_holds():
    client = MagicMock()
    table = client.table.return_value
    by_id = table.update.return_value.eq.return_value
    guarded = by_id.eq.return_value
    guarded.execute.return_value = MagicMock(data=[{'id': 3, 'tagging_status': 'verified'}])
    payload = {'tagging_status': 'verified'}

    written = ReviewStore(client).update_if(3, payload, {'tagging_status': 'review_needed'})

    assert written == [{'id': 3, 'tagging_status': 'verified'}]
    client.table.assert_called_once_with('reports')
    table.update.assert_called_once_with(payload)
    table.update.return_value.eq.assert_called_once_with('id', 3)
    by_id.eq.assert_called_once_with('tagging_status', 'review_needed')


class RecordingChain:
    """A query builder that records every call in order and returns itself."""

    def __init__(self, data):
        self.calls, self.data = [], data

    def table(self, name):
        self.calls.append(('table', name))
        return self

    def update(self, values):
        self.calls.append(('update', values))
        return self

    def eq(self, column, value):
        self.calls.append(('eq', column, value))
        return self

    def is_(self, column, value):
        self.calls.append(('is_', column, value))
        return self

    def execute(self):
        self.calls.append(('execute',))
        return SimpleNamespace(data=self.data)


def test_update_if_checks_null_with_is_null_in_the_given_order():
    chain = RecordingChain([{'id': 3}])
    values = {'tagging_status': 'review_needed'}
    expected = {'tagging_status': 'pending', 'tagged_at': None,
                'tagging_locked_at': None, 'tagging_worker_id': 'w-1'}

    assert ReviewStore(chain).update_if(3, values, expected) == [{'id': 3}]

    assert chain.calls == [
        ('table', 'reports'),
        ('update', values),
        ('eq', 'id', 3),
        ('eq', 'tagging_status', 'pending'),
        ('is_', 'tagged_at', 'null'),
        ('is_', 'tagging_locked_at', 'null'),
        ('eq', 'tagging_worker_id', 'w-1'),
        ('execute',),
    ]


def test_update_if_without_expected_values_writes_by_id_only():
    chain = RecordingChain([{'id': 3}])
    ReviewStore(chain).update_if(3, {'title': 't'}, {})
    assert chain.calls == [('table', 'reports'), ('update', {'title': 't'}), ('eq', 'id', 3), ('execute',)]


def test_update_if_is_empty_when_nothing_was_written():
    client = MagicMock()
    by_id = client.table.return_value.update.return_value.eq.return_value
    for data in ([], None):
        by_id.eq.return_value.execute.return_value = MagicMock(data=data)
        assert ReviewStore(client).update_if(3, {'tagging_status': 'verified'},
                                             {'tagging_status': 'review_needed'}) == []
