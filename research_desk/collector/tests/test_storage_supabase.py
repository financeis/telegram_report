"""Storage's Supabase metadata calls, against a recording fake client (no network).

Pins spec §9.1: dedupe key (chat_username, message_id), max message id over
reports ∪ failed_attempts, 1000-row paging, attempt_count increments.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from research_desk.collector.storage import Storage, build_storage


class FakeQuery:
    """Chained supabase-py query builder that records every call."""

    def __init__(self, sb: 'FakeSupabase', table: str) -> None:
        self._sb = sb
        self.table = table
        self.ops: list[tuple] = []

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)

        def call(*args, **kwargs):
            self.ops.append((name, args, kwargs))
            return self
        return call

    def execute(self):
        self._sb.executed.append((self.table, self.ops))
        return SimpleNamespace(data=self._sb.respond(self.table, self.ops))


class FakeSupabase:
    def __init__(self, respond=None) -> None:
        self.executed: list[tuple[str, list]] = []
        self._respond = respond or (lambda table, ops: [])

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self, name)

    def respond(self, table, ops):
        return self._respond(table, ops)


def _op(ops, name):
    (found,) = [(args, kwargs) for n, args, kwargs in ops if n == name]
    return found


def _rows(start, stop):
    return [{'message_id': i} for i in range(start, stop)]


def test_max_seen_message_id_is_the_max_of_reports_and_failed_attempts(tmp_path):
    sb = FakeSupabase(lambda table, ops: {'reports': [{'message_id': 120}],
                                          'failed_attempts': [{'message_id': 130}]}[table])

    assert Storage(sb, tmp_path).get_max_seen_message_id('chan') == 130

    expected_ops = [('select', ('message_id',), {}), ('eq', ('chat_username', 'chan'), {}),
                    ('order', ('message_id',), {'desc': True}), ('limit', (1,), {})]
    assert sb.executed == [('reports', expected_ops), ('failed_attempts', expected_ops)]


def test_max_seen_message_id_is_0_on_first_run(tmp_path):
    assert Storage(FakeSupabase(), tmp_path).get_max_seen_message_id('chan') == 0


def test_get_all_message_ids_pages_by_1000(tmp_path):
    def respond(table, ops):
        start, end = _op(ops, 'range')[0]
        return {0: _rows(1, 1001), 1000: _rows(1001, 1006)}[start]

    sb = FakeSupabase(respond)
    ids = Storage(sb, tmp_path).get_all_message_ids('chan')

    assert ids == set(range(1, 1006))
    assert [t for t, _ in sb.executed] == ['reports', 'reports']
    assert [_op(ops, 'range')[0] for _, ops in sb.executed] == [(0, 999), (1000, 1999)]
    first = sb.executed[0][1]
    assert _op(first, 'eq')[0] == ('chat_username', 'chan')
    assert _op(first, 'order') == (('message_id',), {})


def test_get_all_message_ids_reads_one_more_page_after_a_full_one(tmp_path):
    sb = FakeSupabase(lambda table, ops: _rows(1, 1001) if _op(ops, 'range')[0][0] == 0 else [])

    assert len(Storage(sb, tmp_path).get_all_message_ids('chan')) == 1000
    assert len(sb.executed) == 2


def test_get_failed_message_ids_pages_and_keeps_order(tmp_path):
    def respond(table, ops):
        start = _op(ops, 'range')[0][0]
        return {0: _rows(5000, 6000), 1000: [{'message_id': 7}]}[start]

    sb = FakeSupabase(respond)
    ids = Storage(sb, tmp_path).get_failed_message_ids('chan')

    assert ids == list(range(5000, 6000)) + [7]
    assert [t for t, _ in sb.executed] == ['failed_attempts', 'failed_attempts']
    assert _op(sb.executed[0][1], 'order') == (('message_id',), {'desc': False})


def test_insert_report_metadata_upserts_on_the_message_key(tmp_path):
    sb = FakeSupabase()
    sent_at = datetime(2026, 5, 5, 12, 0, tzinfo=timezone.utc)
    meta = {'message_id': 101, 'chat_username': 'chan', 'sent_at': sent_at,
            'file_name': 'a.pdf', 'file_path': '101_a.pdf', 'file_size_bytes': 3,
            'file_hash_sha256': 'f' * 64, 'caption': None}

    Storage(sb, tmp_path).insert_report_metadata(meta)

    ((table, ops),) = sb.executed
    assert table == 'reports'
    args, kwargs = _op(ops, 'upsert')
    assert args == ({**meta, 'sent_at': '2026-05-05T12:00:00+00:00'},)
    assert kwargs == {'on_conflict': 'chat_username,message_id'}
    assert meta['sent_at'] is sent_at  # the caller's dict is not modified


@pytest.mark.parametrize('sent_at', ['2026-05-05T12:00:00+00:00', None])
def test_insert_report_metadata_keeps_string_or_missing_sent_at(tmp_path, sent_at):
    sb = FakeSupabase()

    Storage(sb, tmp_path).insert_report_metadata({'message_id': 1, 'sent_at': sent_at})

    assert _op(sb.executed[0][1], 'upsert')[0][0]['sent_at'] == sent_at


def test_upsert_failed_attempt_inserts_the_first_failure(tmp_path):
    sb = FakeSupabase()

    assert Storage(sb, tmp_path).upsert_failed_attempt('chan', 5, 'boom') == 1

    (select_table, select_ops), (insert_table, insert_ops) = sb.executed
    assert (select_table, insert_table) == ('failed_attempts', 'failed_attempts')
    assert select_ops == [('select', ('id, attempt_count',), {}),
                          ('eq', ('chat_username', 'chan'), {}),
                          ('eq', ('message_id', 5), {}),
                          ('limit', (1,), {})]
    assert insert_ops == [('insert', ({'message_id': 5, 'chat_username': 'chan',
                                       'attempt_count': 1, 'error_message': 'boom'},), {})]


def test_upsert_failed_attempt_increments_an_existing_row(tmp_path):
    sb = FakeSupabase(lambda table, ops: [{'id': 7, 'attempt_count': 3}] if ops[0][0] == 'select' else [])
    before = datetime.now(timezone.utc)

    assert Storage(sb, tmp_path).upsert_failed_attempt('chan', 5, 'boom again') == 4

    update_table, update_ops = sb.executed[1]
    assert update_table == 'failed_attempts'
    (values,), _ = _op(update_ops, 'update')
    assert values['attempt_count'] == 4
    assert values['error_message'] == 'boom again'
    failed_at = datetime.fromisoformat(values['last_failed_at'])
    assert failed_at.tzinfo is not None
    assert before - timedelta(seconds=1) <= failed_at <= datetime.now(timezone.utc) + timedelta(seconds=1)
    assert _op(update_ops, 'eq') == (('id', 7), {})


def test_remove_failed_attempt_deletes_by_the_message_key(tmp_path):
    sb = FakeSupabase()

    Storage(sb, tmp_path).remove_failed_attempt('chan', 5)

    assert sb.executed == [('failed_attempts', [('delete', (), {}),
                                                ('eq', ('chat_username', 'chan'), {}),
                                                ('eq', ('message_id', 5), {})])]


def test_build_storage_creates_the_client_through_core_db(monkeypatch, tmp_path):
    from research_desk.core import db

    calls = []
    client = object()

    def fake_supabase_client(url, service_key):
        calls.append((url, service_key))
        return client

    monkeypatch.setattr(db, 'supabase_client', fake_supabase_client)

    storage = build_storage(supabase_url='https://x.supabase.co',
                            supabase_service_key='service-key', base_dir=tmp_path)

    assert calls == [('https://x.supabase.co', 'service-key')]
    assert storage._sb is client
    assert storage.base_dir == tmp_path
