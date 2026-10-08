"""Tests for collector run() — the two-phase orchestration logic.

Ported from the root tests/test_collector.py; the last section adds checks of
spec §9.1 behaviour the old suite did not pin down (attempt warning threshold,
PDF file name / hash / size).
"""
from __future__ import annotations

import hashlib
import logging

import pytest

from research_desk.collector import run as run_module
from research_desk.collector.run import ATTEMPT_WARN_THRESHOLD, RunResult, run
from research_desk.collector.tests.fakes import TrackingFakeClient, make_msg


@pytest.fixture
def cfg():
    """Minimal config-shaped object. channel_ref() falls back to username
    when telegram_channel_id is None (backward-compatible default)."""
    from types import SimpleNamespace

    def channel_ref(self):
        if self.telegram_channel_id is not None:
            return self.telegram_channel_id
        return self.telegram_channel

    ns = SimpleNamespace(
        telegram_channel='sunstudy1004',
        telegram_channel_id=None,
        initial_cutoff_days=30,
        max_concurrent_downloads=4,
    )
    ns.channel_ref = channel_ref.__get__(ns, SimpleNamespace)
    return ns


# === First-run behavior ===

@pytest.mark.asyncio
async def test_first_run_uses_iter_since_date(fake_client, fake_storage, cfg):
    fake_client.new_messages = [make_msg(101)]
    # max_seen=0 → first run path
    result = await run(fake_client, fake_storage, cfg)
    assert ('iter_since_date', 'sunstudy1004', 30) in fake_client.calls


@pytest.mark.asyncio
async def test_subsequent_run_uses_iter_after_id(fake_client, fake_storage, cfg):
    fake_storage._max_seen = 100
    fake_client.new_messages = [make_msg(101)]
    await run(fake_client, fake_storage, cfg)
    assert ('iter_after_id', 'sunstudy1004', 100) in fake_client.calls


# === Stage B: new-message processing ===

@pytest.mark.asyncio
async def test_new_pdf_message_is_downloaded_and_inserted(fake_client, fake_storage, cfg):
    msg = make_msg(101, file_name='samsung_q1.pdf', caption='삼성전자 Q1 실적')
    fake_client.new_messages = [msg]

    result = await run(fake_client, fake_storage, cfg)

    assert result.processed == 1
    assert result.failed == 0
    # File saved with correct name
    assert fake_storage.saved_files[0][0] == '101_samsung_q1.pdf'
    # Metadata inserted with correct keys
    assert len(fake_storage.inserted) == 1
    inserted = fake_storage.inserted[0]
    assert inserted['message_id'] == 101
    assert inserted['chat_username'] == 'sunstudy1004'
    assert inserted['file_name'] == 'samsung_q1.pdf'
    assert inserted['file_path'] == '101_samsung_q1.pdf'
    assert inserted['caption'] == '삼성전자 Q1 실적'
    assert 'file_size_bytes' in inserted
    assert 'file_hash_sha256' in inserted
    assert inserted['sent_at'] == msg.date


@pytest.mark.asyncio
async def test_non_pdf_message_is_skipped(fake_client, fake_storage, cfg):
    fake_client.new_messages = [make_msg(101, has_pdf=False)]
    result = await run(fake_client, fake_storage, cfg)
    assert result.processed == 0
    assert result.skipped == 1
    assert fake_storage.inserted == []


@pytest.mark.asyncio
async def test_download_failure_records_failed_attempt(fake_client, fake_storage, cfg):
    fake_client.new_messages = [make_msg(101)]
    fake_client.download_errors[101] = RuntimeError('network glitch')

    result = await run(fake_client, fake_storage, cfg)

    assert result.processed == 0
    assert result.failed == 1
    assert fake_storage.inserted == []
    assert len(fake_storage.failed_upserts) == 1
    chat, mid, err = fake_storage.failed_upserts[0]
    assert chat == 'sunstudy1004'
    assert mid == 101
    assert 'network glitch' in err


@pytest.mark.asyncio
async def test_one_failure_does_not_block_other_messages(fake_client, fake_storage, cfg):
    fake_client.new_messages = [make_msg(101), make_msg(102), make_msg(103)]
    fake_client.download_errors[102] = RuntimeError('boom')

    result = await run(fake_client, fake_storage, cfg)

    assert result.processed == 2
    assert result.failed == 1
    inserted_ids = [m['message_id'] for m in fake_storage.inserted]
    assert 101 in inserted_ids
    assert 103 in inserted_ids
    assert 102 not in inserted_ids
    # 102 went to failed_attempts
    assert any(mid == 102 for _, mid, _ in fake_storage.failed_upserts)


# === Stage A: failed-message retry ===

@pytest.mark.asyncio
async def test_failed_message_retried_at_start(fake_client, fake_storage, cfg):
    # 100 was previously failed; we'll retry it successfully
    fake_storage._failed_ids = [100]
    fake_storage._max_seen = 100
    fake_client.failed_lookups[100] = make_msg(100, file_name='retry.pdf')
    # Stage B: nothing new
    fake_client.new_messages = []

    result = await run(fake_client, fake_storage, cfg)

    assert result.retried_success == 1
    # Retry lookup happened
    assert ('get_by_id', 'sunstudy1004', 100) in fake_client.calls
    # Insert happened for the retry
    assert any(m['message_id'] == 100 for m in fake_storage.inserted)
    # Failed_attempts row was removed
    assert ('sunstudy1004', 100) in fake_storage.failed_removes


@pytest.mark.asyncio
async def test_failed_message_still_failing_increments_attempt(
    fake_client, fake_storage, cfg
):
    fake_storage._failed_ids = [100]
    fake_storage._max_seen = 100
    fake_client.failed_lookups[100] = make_msg(100)
    fake_client.download_errors[100] = RuntimeError('still broken')
    fake_client.new_messages = []

    result = await run(fake_client, fake_storage, cfg)

    assert result.retried_fail == 1
    # No insert
    assert fake_storage.inserted == []
    # Failed_attempts upsert (attempt_count++)
    assert any(mid == 100 for _, mid, _ in fake_storage.failed_upserts)
    # Was NOT removed
    assert ('sunstudy1004', 100) not in fake_storage.failed_removes


@pytest.mark.asyncio
async def test_deleted_message_is_cleaned_from_failed_attempts(
    fake_client, fake_storage, cfg
):
    fake_storage._failed_ids = [100]
    fake_storage._max_seen = 100
    # Telegram returns None → message was deleted
    fake_client.failed_lookups[100] = None
    fake_client.new_messages = []

    result = await run(fake_client, fake_storage, cfg)

    assert ('sunstudy1004', 100) in fake_storage.failed_removes
    assert result.retried_fail == 0
    assert result.retried_success == 0


@pytest.mark.asyncio
async def test_failed_lookup_returning_non_pdf_is_cleaned(
    fake_client, fake_storage, cfg
):
    # Edge case: a message that was once a PDF is now reported as something else
    fake_storage._failed_ids = [100]
    fake_storage._max_seen = 100
    fake_client.failed_lookups[100] = make_msg(100, has_pdf=False)
    fake_client.new_messages = []

    result = await run(fake_client, fake_storage, cfg)

    assert ('sunstudy1004', 100) in fake_storage.failed_removes


# === RunResult shape ===

@pytest.mark.asyncio
async def test_run_returns_run_result_with_all_counters(fake_client, fake_storage, cfg):
    result = await run(fake_client, fake_storage, cfg)
    assert isinstance(result, RunResult)
    for attr in ('processed', 'skipped', 'failed', 'retried_success', 'retried_fail'):
        assert hasattr(result, attr)
        assert getattr(result, attr) == 0


# === Concurrency observation ===

def _make_cfg(concurrency: int):
    """Build a config-shaped object with channel_ref() that mirrors the cfg fixture."""
    from types import SimpleNamespace

    def channel_ref(self):
        if self.telegram_channel_id is not None:
            return self.telegram_channel_id
        return self.telegram_channel

    ns = SimpleNamespace(
        telegram_channel='sunstudy1004',
        telegram_channel_id=None,
        initial_cutoff_days=30,
        max_concurrent_downloads=concurrency,
    )
    ns.channel_ref = channel_ref.__get__(ns, SimpleNamespace)
    return ns


@pytest.mark.asyncio
async def test_concurrency_respects_semaphore_limit(fake_storage):
    """With N=3 and 10 messages, max concurrent downloads should be at most 3
    AND at least 2 (proving real parallelism)."""
    cfg = _make_cfg(concurrency=3)
    client = TrackingFakeClient()
    client.new_messages = [make_msg(100 + i) for i in range(10)]

    await run(client, fake_storage, cfg)

    assert client.max_concurrent_observed <= 3, \
        f"Semaphore breach: {client.max_concurrent_observed} > 3"
    assert client.max_concurrent_observed >= 2, \
        f"No real parallelism observed (max={client.max_concurrent_observed})"


@pytest.mark.asyncio
async def test_concurrency_n1_is_serial(fake_storage):
    """With N=1 (Semaphore(1)), only one download at a time."""
    cfg = _make_cfg(concurrency=1)
    client = TrackingFakeClient()
    client.new_messages = [make_msg(100 + i) for i in range(5)]

    await run(client, fake_storage, cfg)

    assert client.max_concurrent_observed == 1


# === Backfill mode ===

@pytest.mark.asyncio
async def test_backfill_mode_pre_fetches_existing_ids(fake_client, fake_storage, cfg):
    """Backfill mode skips messages whose id is already in reports."""
    fake_storage._existing_ids = {100, 101}
    fake_client.new_messages = [make_msg(100), make_msg(101), make_msg(102)]

    result = await run(fake_client, fake_storage, cfg, backfill_days=30)

    assert result.processed == 1  # only 102 is new
    assert result.skipped == 2     # 100, 101 are pre-known
    inserted_ids = [m['message_id'] for m in fake_storage.inserted]
    assert 102 in inserted_ids
    assert 100 not in inserted_ids
    assert 101 not in inserted_ids


@pytest.mark.asyncio
async def test_backfill_mode_includes_failed_attempts_in_skip_set(
    fake_client, fake_storage, cfg
):
    """Messages still in failed_attempts after Stage A must be skipped in Stage B,
    otherwise we double-process them in the same run."""
    # Stage A: msg_id=200 is in failed_attempts, retry will fail again
    fake_storage._failed_ids = [200]
    fake_storage._max_seen = 200
    fake_client.failed_lookups[200] = make_msg(200)
    fake_client.download_errors[200] = RuntimeError('persistent fail')
    # Stage B: cutoff_date iter returns the same msg_id 200 + a new 201
    fake_client.new_messages = [make_msg(200), make_msg(201)]

    result = await run(fake_client, fake_storage, cfg, backfill_days=30)

    # Stage A should record one fail
    assert result.retried_fail == 1
    # Stage B should NOT re-process 200 (it's in failed_attempts post-Stage-A)
    inserted_ids = [m['message_id'] for m in fake_storage.inserted]
    assert 201 in inserted_ids
    assert 200 not in inserted_ids
    assert result.processed == 1  # only 201
    # 200 should appear EXACTLY ONCE in download attempts (Stage A only)
    download_calls = [c for c in fake_client.calls if c[0] == 'download' and c[1] == 200]
    assert len(download_calls) == 1


@pytest.mark.asyncio
async def test_backfill_mode_uses_since_date_iter(fake_client, fake_storage, cfg):
    """In backfill mode, iter_messages_since_date is called with backfill_days,
    even when last_seen > 0."""
    fake_storage._max_seen = 999  # nonzero — would normally trigger after_id mode
    fake_client.new_messages = [make_msg(101)]

    await run(fake_client, fake_storage, cfg, backfill_days=90)

    assert ('iter_since_date', 'sunstudy1004', 90) in fake_client.calls
    assert not any(c[0] == 'iter_after_id' for c in fake_client.calls)


@pytest.mark.asyncio
async def test_backfill_mode_runs_stage_a(fake_client, fake_storage, cfg):
    """Backfill mode does NOT skip Stage A — failed retries still happen."""
    fake_storage._failed_ids = [50]
    fake_client.failed_lookups[50] = make_msg(50)
    fake_client.new_messages = []

    await run(fake_client, fake_storage, cfg, backfill_days=30)

    assert ('get_by_id', 'sunstudy1004', 50) in fake_client.calls


@pytest.mark.asyncio
async def test_normal_mode_does_not_pre_fetch_existing_ids(
    fake_client, fake_storage, cfg
):
    """Without backfill_days, get_all_message_ids should never be called."""
    # Set existing_ids to non-empty; if the collector mistakenly calls
    # get_all_message_ids in normal mode, msg 101 would be skipped.
    fake_storage._existing_ids = {101}
    fake_client.new_messages = [make_msg(101)]

    result = await run(fake_client, fake_storage, cfg)  # no backfill_days

    # Normal mode uses min_id/since_date, not the pre-fetched set,
    # so 101 should be processed normally.
    assert result.processed == 1
    inserted_ids = [m['message_id'] for m in fake_storage.inserted]
    assert 101 in inserted_ids


# === Channel id mode (private channels) ===

@pytest.mark.asyncio
async def test_id_mode_uses_int_for_fetch_but_username_for_db(
    fake_client, fake_storage, cfg
):
    """When channel_id is set, telethon receives the int id, but
    storage rows still use the human-readable chat_username label."""
    cfg.telegram_channel_id = 1378197756
    fake_client.new_messages = [make_msg(101)]

    result = await run(fake_client, fake_storage, cfg)

    # Fetch goes with the int id
    assert any(c[0] == 'iter_since_date' and c[1] == 1378197756 for c in fake_client.calls)
    # DB row keeps the string label (continuity with existing data)
    assert result.processed == 1
    assert fake_storage.inserted[0]['chat_username'] == 'sunstudy1004'


@pytest.mark.asyncio
async def test_id_mode_uses_int_for_after_id_fetch(
    fake_client, fake_storage, cfg
):
    """In subsequent (last_seen > 0) mode, iter_after_id also gets the int id."""
    cfg.telegram_channel_id = 1378197756
    fake_storage._max_seen = 125164
    fake_client.new_messages = [make_msg(125165)]

    await run(fake_client, fake_storage, cfg)

    assert any(c[0] == 'iter_after_id' and c[1] == 1378197756 for c in fake_client.calls)
    assert fake_storage.inserted[0]['chat_username'] == 'sunstudy1004'


# === Added: spec §9.1 details ===

def test_attempt_warning_threshold_is_10():
    assert ATTEMPT_WARN_THRESHOLD == 10


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records
            if r.name == run_module.__name__ and r.levelno == logging.WARNING]


@pytest.mark.asyncio
@pytest.mark.parametrize('attempts, warned', [(9, False), (10, True), (11, True)])
async def test_stage_a_warns_once_attempts_reach_threshold(
    fake_client, fake_storage, cfg, monkeypatch, caplog, attempts, warned
):
    fake_storage._failed_ids = [100]
    fake_storage._max_seen = 100
    fake_client.failed_lookups[100] = make_msg(100)
    fake_client.download_errors[100] = RuntimeError('still broken')
    monkeypatch.setattr(fake_storage, 'upsert_failed_attempt', lambda chat, mid, err: attempts)

    with caplog.at_level(logging.INFO, logger=run_module.__name__):
        result = await run(fake_client, fake_storage, cfg)

    assert result.retried_fail == 1
    expected = [f'msg_id=100 has failed {attempts} times — investigate manually'] if warned else []
    assert _warnings(caplog) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('attempts, warned', [(9, False), (10, True)])
async def test_stage_b_warns_once_attempts_reach_threshold(
    fake_client, fake_storage, cfg, monkeypatch, caplog, attempts, warned
):
    fake_client.new_messages = [make_msg(101)]
    fake_client.download_errors[101] = RuntimeError('network glitch')
    monkeypatch.setattr(fake_storage, 'upsert_failed_attempt', lambda chat, mid, err: attempts)

    with caplog.at_level(logging.INFO, logger=run_module.__name__):
        result = await run(fake_client, fake_storage, cfg)

    assert result.failed == 1
    expected = [f'msg_id=101 has failed {attempts} times — investigate manually'] if warned else []
    assert _warnings(caplog) == expected


@pytest.mark.asyncio
async def test_saved_file_name_hash_and_size(fake_client, fake_storage, cfg):
    """<message id>_<sanitized original name>; the DB keeps the original name,
    the SHA-256 and the size of the saved file."""
    content = b'%PDF-1.7 report body'
    fake_client.new_messages = [make_msg(101, file_name='a/b:c?.pdf')]
    fake_client.download_results[101] = content

    await run(fake_client, fake_storage, cfg)

    row = fake_storage.inserted[0]
    assert row['file_path'] == '101_a_b_c_.pdf'
    assert row['file_name'] == 'a/b:c?.pdf'
    assert row['file_hash_sha256'] == hashlib.sha256(content).hexdigest()
    assert row['file_size_bytes'] == len(content)
    assert (fake_storage.base_dir / '101_a_b_c_.pdf').read_bytes() == content


@pytest.mark.asyncio
async def test_pdf_without_file_name_is_saved_as_unnamed(fake_client, fake_storage, cfg):
    msg = make_msg(101)
    msg.document.attributes = []  # PDF by mime type, no file_name attribute
    fake_client.new_messages = [msg]

    await run(fake_client, fake_storage, cfg)

    row = fake_storage.inserted[0]
    assert row['file_name'] == 'unnamed.pdf'
    assert row['file_path'] == '101_unnamed.pdf'
