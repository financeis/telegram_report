"""Two-phase orchestration: retry failures (A), then fetch new (B).

This module contains all the business rules described in collector design §3.2
and §5.3 (design notes: see the package docstring). It depends only on the
abstract interfaces of TelegramClient and Storage, not their concrete
implementations — so it can be tested with simple fakes.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from research_desk.collector.storage import compute_sha256, sanitize_filename
from research_desk.collector.telegram import _get_original_filename, has_pdf

log = logging.getLogger(__name__)

# Threshold above which we log a WARNING for a chronically-failing message
# (collector design §5.3 — operations guidance, no automatic action in MVP)
ATTEMPT_WARN_THRESHOLD = 10


@dataclass(frozen=True)
class RunResult:
    """Counters returned by `run()`. Used by the collect command to set the exit code."""
    processed: int = 0       # Stage B: PDF messages successfully downloaded + inserted
    skipped: int = 0         # Stage B: non-PDF messages
    failed: int = 0          # Stage B: PDF messages whose download/insert failed
    retried_success: int = 0  # Stage A: previously-failed messages now succeeded
    retried_fail: int = 0    # Stage A: still failing after retry


async def run(
    client: Any,
    storage: Any,
    config: Any,
    backfill_days: int | None = None,
) -> RunResult:
    """Run one collection cycle for the configured channel.

    Stage A: re-attempt every msg_id currently in failed_attempts (parallelized).
    Stage B: fetch messages and process them (parallelized).
      - Normal mode (backfill_days=None): start from MAX(reports + failed_attempts).
      - Backfill mode (backfill_days=int): iterate from N days ago, skipping
        message_ids already in reports OR still in failed_attempts after Stage A.

    Telegram fetch uses config.channel_ref() (int channel id for private
    channels, or username str for public). DB rows always use
    config.telegram_channel as the chat_username label so legacy and new
    data live in the same namespace.

    Both stages share a single Semaphore so total concurrent downloads
    cannot exceed config.max_concurrent_downloads.
    """
    channel_ref = config.channel_ref()       # for telethon
    chat_label = config.telegram_channel     # for storage (DB column)
    sem = asyncio.Semaphore(config.max_concurrent_downloads)

    # === Stage A: retry past failures (parallel) ===
    failed_ids = storage.get_failed_message_ids(chat_label)
    log.info("Stage A: retrying %d previously failed messages (concurrency=%d)",
             len(failed_ids), config.max_concurrent_downloads)

    async def retry_one(msg_id: int) -> str:
        async with sem:
            msg = await client.get_message_by_id(channel_ref, msg_id)
            if msg is None or not has_pdf(msg):
                log.info("Cleaning failed_attempts row for msg_id=%s (deleted or not PDF)", msg_id)
                storage.remove_failed_attempt(chat_label, msg_id)
                return 'cleaned'
            try:
                await _process_one_message(client, storage, chat_label, msg)
                storage.remove_failed_attempt(chat_label, msg_id)
                return 'success'
            except Exception as e:
                log.exception("Retry still failing for msg_id=%s", msg_id)
                new_count = storage.upsert_failed_attempt(chat_label, msg_id, str(e))
                if new_count >= ATTEMPT_WARN_THRESHOLD:
                    log.warning("msg_id=%s has failed %d times — investigate manually",
                                msg_id, new_count)
                return 'fail'

    stage_a_results = await asyncio.gather(*[retry_one(mid) for mid in failed_ids])
    retried_success = sum(1 for r in stage_a_results if r == 'success')
    retried_fail = sum(1 for r in stage_a_results if r == 'fail')

    # === Stage B: fetch new messages (parallel) ===
    if backfill_days is not None:
        # Backfill mode: pre-fetch dedupe set AFTER Stage A so newly-added
        # reports rows AND still-failing failed_attempts rows are both included.
        # Skipping the latter avoids double-processing the same msg_id in one run
        # (backfill design §3.3).
        existing_ids = storage.get_all_message_ids(chat_label)
        existing_ids.update(storage.get_failed_message_ids(chat_label))
        log.info(
            "Backfill mode: %d existing message_ids will be skipped "
            "(reports + still-failed), fetching from %d days ago",
            len(existing_ids), backfill_days,
        )
        message_iter = client.iter_messages_since_date(channel_ref, backfill_days)
    else:
        existing_ids = None
        last_seen = storage.get_max_seen_message_id(chat_label)
        log.info("Stage B: last_seen_message_id=%s", last_seen)
        if last_seen == 0:
            log.info("First run; using cutoff=%d days", config.initial_cutoff_days)
            message_iter = client.iter_messages_since_date(channel_ref, config.initial_cutoff_days)
        else:
            message_iter = client.iter_messages_after_id(channel_ref, last_seen)

    async def process_new(msg) -> str:
        async with sem:
            try:
                await _process_one_message(client, storage, chat_label, msg)
                return 'processed'
            except Exception as e:
                log.exception("Failed to process message_id=%s", msg.id)
                new_count = storage.upsert_failed_attempt(chat_label, msg.id, str(e))
                if new_count >= ATTEMPT_WARN_THRESHOLD:
                    log.warning("msg_id=%s has failed %d times — investigate manually",
                                msg.id, new_count)
                return 'failed'

    tasks = []
    skipped = 0
    async for msg in message_iter:
        if not has_pdf(msg):
            skipped += 1
            continue
        if existing_ids is not None and msg.id in existing_ids:
            skipped += 1
            continue
        tasks.append(asyncio.create_task(process_new(msg)))

    stage_b_results = await asyncio.gather(*tasks)
    processed = sum(1 for r in stage_b_results if r == 'processed')
    failed = sum(1 for r in stage_b_results if r == 'failed')

    log.info(
        "Run complete. Stage A: retried_success=%d retried_fail=%d. "
        "Stage B: processed=%d skipped=%d failed=%d",
        retried_success, retried_fail, processed, skipped, failed,
    )
    return RunResult(
        processed=processed,
        skipped=skipped,
        failed=failed,
        retried_success=retried_success,
        retried_fail=retried_fail,
    )


async def _process_one_message(client: Any, storage: Any, channel: str, msg: Any) -> None:
    """Download a message's PDF and write metadata to storage. Idempotent enough that
    a re-run after partial failure converges (download to .partial → atomic rename →
    UNIQUE-constrained INSERT)."""
    original = _get_original_filename(msg) or 'unnamed.pdf'
    filename = f"{msg.id}_{sanitize_filename(original)}"

    pdf_bytes = await client.download_pdf_bytes(msg)
    target_path = storage.save_pdf_atomically(pdf_bytes, filename)

    file_hash = compute_sha256(target_path)
    file_size = target_path.stat().st_size

    storage.insert_report_metadata({
        'message_id': msg.id,
        'chat_username': channel,
        'sent_at': msg.date,
        'file_name': original,
        'file_path': filename,
        'file_size_bytes': file_size,
        'file_hash_sha256': file_hash,
        'caption': msg.message,
    })
