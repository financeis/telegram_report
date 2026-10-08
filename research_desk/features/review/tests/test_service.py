"""features.review.service: the review queue, the review actions and their undo.

Ported from langgraph_tagger/workspace/tests/test_migration.py, unchanged in meaning:
- test_review_actions_and_server_snapshot_undo (all three actions)
- test_review_does_not_overwrite_subsequent_worker_or_review
Both run on the old in-memory MemoryDB / MemoryQuery, copied as they were. The service now gets
that client from core.db.supabase_client when it prepares, instead of as an argument.

New (spec §6, §9.7, §9.8, §9.9):
- the queue: the review_needed row tagged longest ago apart from the skipped ids, an exact count,
  every column but file_path (file_hash_sha256 stays) plus pdf_url;
- the actions: the payloads written, written only while the row is still review_needed, refused
  unless it is; the 404 / 409 texts; a fresh token per action;
- the undo: only the 22 snapshot columns are put back, only while the row equals the 'after'
  snapshot, with the compare-and-set on tagging_status, tagged_at, tagging_locked_at and
  tagging_worker_id; the 404 / 409 texts; one use per token; a refused undo keeps its record; at
  most 100 records, the oldest dropped first;
- one action or undo at a time;
- coverage.invalidate() right after each successful action and undo, never after a refused or
  failed one;
- readiness: NotReady("검토", "DB 접속 설정(SUPABASE_URL, SUPABASE_SERVICE_KEY)이 없습니다") without
  DB settings, .env read again when preparing, tried again on the next call, the client made once;
  creating the service reads nothing; the checks that need no DB come first.
"""
from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from fastapi import HTTPException

from research_desk.core.settings import NotReady
from research_desk.features.review.rules import (
    SNAPSHOT_COLUMNS,
    build_oos_payload,
    build_pending_reset_payload,
    capture_snapshot,
)
from research_desk.features.review.service import ReviewService

from .fakes import KEY, URL, waiting

ROW_NOT_FOUND = '검토할 보고서가 없습니다.'
NOT_IN_QUEUE = '다른 작업에서 처리한 보고서입니다. 목록을 새로고침해 주세요.'
CHANGED = '보고서 상태가 바뀌었습니다. 새로고침해 주세요.'
UNDO_UNKNOWN = '되돌릴 작업이 없거나 서버가 재시작되었습니다.'
UNDO_LATER_WORK = '후속 작업이 처리한 보고서라 되돌릴 수 없습니다.'
UNDO_STARTED = '후속 작업이 시작되어 되돌릴 수 없습니다.'
PAGE_LIMIT = '미리보기는 첫 3페이지까지 제공됩니다.'
DB_REASON = 'DB 접속 설정(SUPABASE_URL, SUPABASE_SERVICE_KEY)이 없습니다'
NOT_READY = f'검토 기능을 지금 쓸 수 없습니다: {DB_REASON}'
GUARD_COLUMNS = ('tagging_status', 'tagged_at', 'tagging_locked_at', 'tagging_worker_id')
ACTIONS = [('verify', None), ('oos', 'foreign'), ('retag', None)]
WAIT_S = 5


def refused(call) -> HTTPException:
    with pytest.raises(HTTPException) as exc:
        call()
    return exc.value


def guard_filters(expected: dict) -> list[tuple]:
    """The filters of a guarded write of row 1 that expects these values."""
    return [('eq', 'id', 1)] + [('is', column, None) if value is None else ('eq', column, value)
                                for column, value in expected.items()]


@pytest.fixture
def service() -> ReviewService:
    return ReviewService()


# ── ported: on the old in-memory DB of test_migration.py (fakes.MemoryDB) ────

@pytest.mark.parametrize('action,reason,status', [('verify', None, 'verified'), ('oos', 'foreign', 'verified'), ('retag', None, 'pending')])
def test_review_actions_and_server_snapshot_undo(memory, action, reason, status):
    db = memory
    original = deepcopy(db.rows[0])
    service = ReviewService()
    response = service.act(1, action, reason)
    assert db.rows[0]['tagging_status'] == status
    assert db.rows[0]['file_path'] == original['file_path'] and db.rows[0]['caption'] == original['caption']
    if action == 'oos':
        assert db.rows[0]['stock_codes'] == [] and db.rows[0]['out_of_scope_reason'] == 'foreign'
    if action == 'retag':
        assert db.rows[0]['report_type'] is None and db.rows[0]['tagging_locked_at'] is None
    assert service.undo(response['undo_token']) == {'report_id': 1}
    for key, value in original.items():
        assert db.rows[0][key] == value
    with pytest.raises(HTTPException):
        service.undo(response['undo_token'])


def test_review_does_not_overwrite_subsequent_worker_or_review(memory):
    db = memory; service = ReviewService()
    result = service.act(1, 'retag')
    db.rows[0]['tagging_worker_id'] = 'another-worker'
    with pytest.raises(HTTPException) as exc:
        service.undo(result['undo_token'])
    assert exc.value.status_code == 409
    assert db.rows[0]['tagging_worker_id'] == 'another-worker'
    with pytest.raises(HTTPException):
        service.act(1, 'verify')


# ── the queue ────────────────────────────────────────────────────────────────

def test_the_queue_shows_the_waiting_row_tagged_longest_ago_and_the_exact_count(service, db):
    db.rows = [waiting(1, '2026-05-03T00:00:00+00:00'), waiting(2, '2026-05-01T00:00:00+00:00'),
               waiting(3, '2026-05-02T00:00:00+00:00'),
               waiting(4, '2026-04-01T00:00:00+00:00', tagging_status='auto'),
               waiting(5, '2026-04-01T00:00:00+00:00', tagging_status='verified'),
               waiting(6, '2026-04-01T00:00:00+00:00', tagging_status='pending')]
    body = service.queue([])
    assert list(body) == ['remaining', 'report']
    assert body['remaining'] == 3
    assert body['report']['id'] == 2


def test_skipped_ids_are_left_out_of_the_queue_but_still_counted(service, db):
    db.rows = [waiting(1, '2026-05-03T00:00:00+00:00'), waiting(2, '2026-05-01T00:00:00+00:00'),
               waiting(3, '2026-05-02T00:00:00+00:00')]
    assert service.queue([2])['report']['id'] == 3
    assert service.queue([2, 3])['report']['id'] == 1
    assert service.queue([2, 3, 1]) == {'remaining': 3, 'report': None}
    assert service.queue([2])['remaining'] == 3


def test_the_queue_row_has_every_column_but_file_path_and_a_pdf_url(service, db):
    db.rows = [waiting(7)]
    report = service.queue([])['report']
    expected = {k: v for k, v in waiting(7).items() if k != 'file_path'} | {'pdf_url': '/api/review/7/pdf'}
    assert report == expected
    assert list(report) == list(expected)          # the row's columns in order, then pdf_url
    assert 'file_path' not in report
    assert report['file_hash_sha256'] == 'ab' * 32  # kept, as today (spec §6)
    assert report['caption'] == 'internal caption'


def test_an_empty_queue_is_null_with_none_remaining(service, db):
    db.rows = [waiting(1, tagging_status='auto')]
    assert service.queue([]) == {'remaining': 0, 'report': None}


def test_the_queue_reads_the_next_row_then_the_count(service, db):
    db.rows = [waiting(1)]
    service.queue([5])
    first, second = db.reads()
    assert first.columns == ('*',)
    assert first.filter_list() == [('eq', 'tagging_status', 'review_needed'), ('not.in', 'id', [5])]
    assert (first.ordering, first.limit_n) == (('tagged_at', False), 1)
    assert (second.columns, second.count) == (('id',), 'exact')
    assert second.filter_list() == [('eq', 'tagging_status', 'review_needed')]
    assert db.writes() == []


# ── the actions ──────────────────────────────────────────────────────────────

def test_verify_writes_only_the_status(service, db):
    db.rows = [waiting(1)]
    service.act(1, 'verify')
    (write,) = db.writes()
    assert write.values == {'tagging_status': 'verified'}
    assert write.filter_list() == guard_filters({'tagging_status': 'review_needed'})
    assert db.row(1) == waiting(1) | {'tagging_status': 'verified'}


@pytest.mark.parametrize('reason', ['foreign', 'fund', 'digital', 'private', 'ir_self'])
def test_out_of_scope_writes_the_shared_out_of_scope_shape(service, db, reason):
    db.rows = [waiting(1)]
    service.act(1, 'oos', reason)
    (write,) = db.writes()
    assert write.values == build_oos_payload(waiting(1), reason)
    assert write.filter_list() == guard_filters({'tagging_status': 'review_needed'})
    row = db.row(1)
    assert row['tagging_status'] == 'verified' and row['out_of_scope_reason'] == reason
    assert row['published_at'] is None and row['stock_codes'] == [] and row['products'] == []
    assert row['title'] == 'report 1' and row['stock_codes_raw'] == ['005935']


def test_retag_writes_the_full_reset_to_pending(service, db):
    db.rows = [waiting(1)]
    service.act(1, 'retag')
    (write,) = db.writes()
    assert write.values == build_pending_reset_payload()
    assert write.filter_list() == guard_filters({'tagging_status': 'review_needed'})
    row = db.row(1)
    assert row['tagging_status'] == 'pending' and row['report_type'] is None
    assert {k: row[k] for k in ('file_path', 'caption', 'file_hash_sha256')} == {
        'file_path': '2026/1.pdf', 'caption': 'internal caption', 'file_hash_sha256': 'ab' * 32}


def test_each_action_answers_a_fresh_token_and_the_report_id(service, db):
    db.rows = [waiting(1), waiting(2)]
    first, second = service.act(1, 'verify'), service.act(2, 'retag')
    assert list(first) == ['undo_token', 'report_id']
    assert (first['report_id'], second['report_id']) == (1, 2)
    for answer in (first, second):
        assert re.fullmatch(r'[0-9a-f]{32}', answer['undo_token'])
    assert first['undo_token'] != second['undo_token']


@pytest.mark.parametrize('status', ['pending', 'processing', 'auto', 'verified'])
def test_a_row_not_waiting_for_review_is_refused_and_left_alone(service, db, invalidations, status):
    db.rows = [waiting(1, tagging_status=status)]
    error = refused(lambda: service.act(1, 'verify'))
    assert (error.status_code, error.detail) == (409, NOT_IN_QUEUE)
    assert db.writes() == [] and db.row(1) == waiting(1, tagging_status=status)
    assert invalidations.count == 0


def test_a_missing_row_is_404(service, db, invalidations):
    db.rows = [waiting(1)]
    for action, reason in ACTIONS:
        error = refused(lambda: service.act(99, action, reason))
        assert (error.status_code, error.detail) == (404, ROW_NOT_FOUND)
    assert db.writes() == [] and invalidations.count == 0


def test_a_row_changed_between_the_read_and_the_write_is_not_overwritten(service, db, invalidations):
    db.rows = [waiting(1)]
    worker = {'tagging_status': 'processing', 'tagging_locked_at': '2026-05-08T00:00:00+00:00',
              'tagging_worker_id': 'escalate-1'}
    db.before_write.append(lambda: db.row(1).update(worker))   # another writer gets there first

    error = refused(lambda: service.act(1, 'retag'))

    assert (error.status_code, error.detail) == (409, CHANGED)
    assert db.row(1) == waiting(1) | worker
    assert invalidations.count == 0
    assert service.undo_records == {}   # no undo for a write that did not happen


def test_an_out_of_scope_action_without_a_valid_reason_writes_nothing(service, db, invalidations):
    db.rows = [waiting(1)]
    for reason in (None, 'not_a_reason'):
        with pytest.raises(ValueError):
            service.act(1, 'oos', reason)
    assert db.writes() == [] and service.undo_records == {} and invalidations.count == 0


# ── the undo ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('action,reason', ACTIONS)
def test_undo_puts_back_the_row_as_it_was(service, db, action, reason):
    db.rows = [waiting(1)]
    token = service.act(1, action, reason)['undo_token']
    assert service.undo(token) == {'report_id': 1}
    assert db.row(1) == waiting(1)


@pytest.mark.parametrize('action,reason', ACTIONS)
def test_undo_writes_the_snapshot_columns_while_the_four_columns_still_hold(service, db, action, reason):
    db.rows = [waiting(1)]
    token = service.act(1, action, reason)['undo_token']
    after = capture_snapshot(db.row(1))
    service.undo(token)
    _, undo = db.writes()
    assert undo.values == capture_snapshot(waiting(1))   # the 22 columns from server memory
    assert list(undo.values) == list(SNAPSHOT_COLUMNS)
    assert undo.filter_list() == guard_filters({column: after[column] for column in GUARD_COLUMNS})


def test_undo_leaves_columns_outside_the_snapshot_alone(service, db):
    db.rows = [waiting(1)]
    token = service.act(1, 'oos', 'fund')['undo_token']
    db.row(1)['caption'] = 'changed by the collector'   # not a snapshot column
    service.undo(token)
    assert db.row(1) == waiting(1) | {'caption': 'changed by the collector'}


LATER_WORK = {
    'a tagger worker took it': {'tagging_status': 'processing', 'tagging_worker_id': 'run-1',
                                'tagging_locked_at': '2026-05-08T00:00:00+00:00'},
    'escalate classified it again': {'tagging_status': 'auto', 'tagging_notes': None,
                                     'tagged_at': '2026-05-08T00:00:00+00:00'},
    'only the notes changed': {'tagging_notes': 'krx_name_code_mismatch'},
    'only the classification changed': {'report_type': '산업'},
}


@pytest.mark.parametrize('change', list(LATER_WORK.values()), ids=list(LATER_WORK))
@pytest.mark.parametrize('action,reason', ACTIONS)
def test_undo_after_later_work_is_refused(service, db, invalidations, action, reason, change):
    db.rows = [waiting(1)]
    token = service.act(1, action, reason)['undo_token']
    db.row(1).update(change)
    later = deepcopy(db.row(1))

    error = refused(lambda: service.undo(token))

    assert (error.status_code, error.detail) == (409, UNDO_LATER_WORK)
    assert len(db.writes()) == 1 and db.row(1) == later
    assert invalidations.count == 1   # the action's only


def test_undo_is_refused_when_later_work_starts_during_it(service, db, invalidations):
    db.rows = [waiting(1)]
    token = service.act(1, 'retag')['undo_token']
    worker = {'tagging_status': 'processing', 'tagging_worker_id': 'run-1',
              'tagging_locked_at': '2026-05-08T00:00:00+00:00'}
    db.before_write.append(lambda: db.row(1).update(worker))   # picked up after the check

    error = refused(lambda: service.undo(token))

    assert (error.status_code, error.detail) == (409, UNDO_STARTED)
    assert db.row(1)['tagging_worker_id'] == 'run-1' and db.row(1)['tagging_status'] == 'processing'
    assert db.row(1)['report_type'] is None   # the worker's row was not overwritten
    assert invalidations.count == 1


def test_undo_of_a_row_that_is_gone_is_404(service, db, invalidations):
    db.rows = [waiting(1)]
    token = service.act(1, 'verify')['undo_token']
    db.rows.clear()
    error = refused(lambda: service.undo(token))
    assert (error.status_code, error.detail) == (404, ROW_NOT_FOUND)
    assert invalidations.count == 1


def test_an_unknown_token_is_refused_without_reading_the_db(service, db, invalidations):
    error = refused(lambda: service.undo('0' * 32))
    assert (error.status_code, error.detail) == (409, UNDO_UNKNOWN)
    assert db.executed == [] and invalidations.count == 0


def test_a_token_undoes_once(service, db, invalidations):
    db.rows = [waiting(1)]
    token = service.act(1, 'verify')['undo_token']
    service.undo(token)
    error = refused(lambda: service.undo(token))
    assert (error.status_code, error.detail) == (409, UNDO_UNKNOWN)
    assert invalidations.count == 2


def test_a_refused_undo_keeps_its_record(service, db):
    db.rows = [waiting(1)]
    token = service.act(1, 'verify')['undo_token']
    after = deepcopy(db.row(1))
    db.row(1)['tagging_notes'] = 'changed'
    assert refused(lambda: service.undo(token)).detail == UNDO_LATER_WORK
    db.rows = [after]                                   # the later change went away again
    assert service.undo(token) == {'report_id': 1}
    assert db.row(1) == waiting(1)


def test_undoing_one_report_leaves_the_others_alone(service, db):
    db.rows = [waiting(1), waiting(2)]
    first = service.act(1, 'verify')['undo_token']
    second = service.act(2, 'oos', 'fund')['undo_token']
    service.undo(first)
    assert db.row(1) == waiting(1)
    assert db.row(2)['tagging_status'] == 'verified' and db.row(2)['out_of_scope_reason'] == 'fund'
    service.undo(second)
    assert db.row(2) == waiting(2)


def test_the_server_keeps_the_last_100_undo_records(service, db):
    db.rows = [waiting(rid) for rid in range(1, 102)]
    tokens = [service.act(rid, 'verify')['undo_token'] for rid in range(1, 102)]
    assert len(service.undo_records) == 100
    assert refused(lambda: service.undo(tokens[0])).detail == UNDO_UNKNOWN   # the oldest went first
    assert service.undo(tokens[1]) == {'report_id': 2}
    assert service.undo(tokens[-1]) == {'report_id': 101}
    assert db.row(1)['tagging_status'] == 'verified'
    assert db.row(2) == waiting(2) and db.row(101) == waiting(101)


def test_an_undone_record_frees_its_place(service, db):
    db.rows = [waiting(rid) for rid in range(1, 103)]
    tokens = [service.act(rid, 'verify')['undo_token'] for rid in range(1, 101)]
    service.undo(tokens[50])
    service.act(101, 'verify')                       # 100 records again: nothing dropped
    assert service.undo(tokens[0]) == {'report_id': 1}
    service.act(102, 'verify')
    service.act(1, 'verify')                         # 101 → the oldest left (row 2's) is dropped
    assert refused(lambda: service.undo(tokens[1])).detail == UNDO_UNKNOWN
    assert service.undo(tokens[2]) == {'report_id': 3}


# ── one action or undo at a time ─────────────────────────────────────────────

def test_actions_and_undos_run_one_at_a_time(service, db):
    db.rows = [waiting(1), waiting(2), waiting(3)]
    earlier = service.act(3, 'verify')['undo_token']
    writing, release = threading.Event(), threading.Event()

    def slow_write():
        writing.set()
        assert release.wait(WAIT_S)

    db.before_write.append(slow_write)
    results = {}
    first = threading.Thread(target=lambda: results.update(first=service.act(1, 'verify')))
    first.start()
    assert writing.wait(WAIT_S)                      # the first action is inside its write
    reads_before = len(db.reads())
    others = [threading.Thread(target=lambda: results.update(second=service.act(2, 'retag'))),
              threading.Thread(target=lambda: results.update(undo=service.undo(earlier)))]
    for thread in others:
        thread.start()
    time.sleep(0.3)
    assert len(db.reads()) == reads_before           # neither has read its row yet
    release.set()
    for thread in (first, *others):
        thread.join(WAIT_S)
    assert results['first']['report_id'] == 1 and results['second']['report_id'] == 2
    assert results['undo'] == {'report_id': 3}
    assert db.row(1)['tagging_status'] == 'verified' and db.row(2)['tagging_status'] == 'pending'
    assert db.row(3) == waiting(3)


def test_two_actions_on_one_row_settle_it_once(service, db):
    db.rows = [waiting(1)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(service.act, 1, 'verify') for _ in range(8)]
    outcomes = []
    for future in futures:
        try:
            outcomes.append(future.result()['report_id'])
        except HTTPException as exc:
            outcomes.append((exc.status_code, exc.detail))
    assert outcomes.count(1) == 1
    assert outcomes.count((409, NOT_IN_QUEUE)) == 7
    assert len(db.writes()) == 1


# ── the coverage cache (spec §9.7) ───────────────────────────────────────────

@pytest.mark.parametrize('action,reason', ACTIONS)
def test_a_successful_action_clears_the_coverage_cache_after_its_write(service, db, invalidations,
                                                                      action, reason):
    db.rows = [waiting(1)]
    invalidations.probe = lambda: db.row(1)['tagging_status']
    service.act(1, action, reason)
    assert invalidations.count == 1
    assert invalidations.seen == ['pending' if action == 'retag' else 'verified']


@pytest.mark.parametrize('action,reason', ACTIONS)
def test_a_successful_undo_clears_the_coverage_cache_after_its_write(service, db, invalidations,
                                                                    action, reason):
    db.rows = [waiting(1)]
    token = service.act(1, action, reason)['undo_token']
    invalidations.probe = lambda: db.row(1)['tagging_status']
    service.undo(token)
    assert invalidations.count == 2
    assert invalidations.seen == ['review_needed']


def test_nothing_refused_or_failed_clears_the_coverage_cache(service, db, invalidations, monkeypatch):
    db.rows = [waiting(1), waiting(2, tagging_status='auto'), waiting(3)]
    attempts = [
        lambda: service.act(99, 'verify'),                       # no such row
        lambda: service.act(2, 'verify'),                        # not waiting
        lambda: service.act(3, 'oos', None),                     # no reason
        lambda: service.undo('unknown'),                         # no such record
    ]
    db.before_write.append(lambda: db.row(1).update(tagging_status='processing'))
    attempts.append(lambda: service.act(1, 'verify'))            # changed before the write
    for attempt in attempts:
        with pytest.raises((HTTPException, ValueError)):
            attempt()
    assert invalidations.count == 0

    db.row(1)['tagging_status'] = 'review_needed'
    token = service.act(1, 'verify')['undo_token']
    assert invalidations.count == 1
    db.row(1)['tagging_notes'] = 'later'
    refused(lambda: service.undo(token))                         # later work
    db.row(1)['tagging_notes'] = 'krx_unmatched_in_scope'
    db.before_write.append(lambda: db.row(1).update(tagging_worker_id='run-1'))
    refused(lambda: service.undo(token))                         # later work started
    db.rows.remove(db.row(1))
    refused(lambda: service.undo(token))                         # the row is gone
    assert invalidations.count == 1

    monkeypatch.delenv('SUPABASE_URL')
    fresh = ReviewService()
    with pytest.raises(NotReady):
        fresh.act(3, 'verify')                                   # not ready
    assert invalidations.count == 1


# ── readiness (spec §9.9) ────────────────────────────────────────────────────

SERVICE_CALLS = {
    'queue': lambda s: s.queue([]),
    'row': lambda s: s.row(1),
    'act': lambda s: s.act(1, 'verify'),
    'pdf': lambda s: s.pdf_path(1),
    'preview': lambda s: s.preview(1),
    'page': lambda s: s.page_png(1, 1),
}


@pytest.mark.parametrize('call', list(SERVICE_CALLS.values()), ids=list(SERVICE_CALLS))
@pytest.mark.parametrize('missing', ['SUPABASE_URL', 'SUPABASE_SERVICE_KEY'])
@pytest.mark.parametrize('value', [None, ''], ids=['unset', 'empty'])
def test_without_db_settings_review_is_not_ready(monkeypatch, invalidations, call, missing, value):
    monkeypatch.setenv('SUPABASE_URL', URL)
    monkeypatch.setenv('SUPABASE_SERVICE_KEY', KEY)
    if value is None:
        monkeypatch.delenv(missing)
    else:
        monkeypatch.setenv(missing, value)
    with pytest.raises(NotReady) as exc:
        call(ReviewService())
    assert (exc.value.area, exc.value.reason) == ('검토', DB_REASON)
    assert str(exc.value) == NOT_READY   # a fixed sentence: no URL, no key
    assert invalidations.count == 0


def test_the_checks_that_need_no_db_come_first(service):
    # no DB settings, and core.db.supabase_client refuses
    for page in (0, 4):
        error = refused(lambda: service.page_png(1, page))
        assert (error.status_code, error.detail) == (404, PAGE_LIMIT)
    error = refused(lambda: service.undo('unknown'))
    assert (error.status_code, error.detail) == (409, UNDO_UNKNOWN)


def test_a_failed_preparation_is_tried_again_on_the_next_call(service, db, monkeypatch):
    db.rows = [waiting(1)]
    monkeypatch.delenv('SUPABASE_SERVICE_KEY')
    for _ in range(2):
        with pytest.raises(NotReady):
            service.queue([])
    monkeypatch.setenv('SUPABASE_SERVICE_KEY', KEY)
    assert service.queue([])['report']['id'] == 1
    assert db.made == [(URL, KEY)]


def test_db_settings_added_to_env_file_are_used_on_the_next_call(env_file, service, db, monkeypatch):
    db.rows = [waiting(1)]
    monkeypatch.delenv('SUPABASE_URL')
    monkeypatch.delenv('SUPABASE_SERVICE_KEY')
    with pytest.raises(NotReady):
        service.queue([])
    env_file.write_text('SUPABASE_URL=https://from-env-file.test\nSUPABASE_SERVICE_KEY=k\n', encoding='utf-8')
    assert service.queue([])['remaining'] == 1
    assert db.made == [('https://from-env-file.test', 'k')]


def test_the_db_client_is_prepared_once_and_kept(service, db):
    db.rows = [waiting(1)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        answers = list(pool.map(lambda _: service.queue([]), range(16)))
    assert {answer['report']['id'] for answer in answers} == {1}
    service.act(1, 'verify')
    assert db.made == [(URL, KEY)]


def test_creating_the_service_reads_nothing():
    # no settings at all, and core.db.supabase_client refuses: still no error
    created = ReviewService()
    assert created.undo_records == {}


def test_review_needs_no_ai_key_telegram_or_db_url(service, db):
    # clean_env removed the model keys, the Telegram settings and SUPABASE_DB_URL
    db.rows = [waiting(1)]
    token = service.act(1, 'verify')['undo_token']
    assert service.undo(token) == {'report_id': 1}
