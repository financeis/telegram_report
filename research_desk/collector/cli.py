"""The ``collect`` command: ``python -m research_desk collect [options]``.

``register(subparsers)`` adds the subcommand. Parsing sets ``args.func`` to
``collect``; the entry point calls ``args.func(args)`` and exits with the
code it returns.

Exit codes (collector design §5.5):
  0 = complete success (no failures, possibly nothing to do)
  1 = total failure (config / auth / network / unhandled)
  2 = partial failure (some messages went to failed_attempts this run)
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import logging
import sys

from research_desk.collector.run import RunResult, run as run_collection
from research_desk.collector.settings import Config, load_config
from research_desk.collector.storage import build_storage
from research_desk.collector.telegram import TelegramClient, _get_original_filename, has_pdf
from research_desk.core.settings import MissingSetting

log = logging.getLogger(__name__)

DESCRIPTION = 'Collect PDF reports from a Telegram channel into Supabase + local FS.'


def register(subparsers) -> argparse.ArgumentParser:
    """Add the ``collect`` subcommand to ``subparsers`` (from ``add_subparsers()``).

    Returns the new parser. Its parsed namespace carries ``func=collect``.
    """
    p = subparsers.add_parser('collect', help=DESCRIPTION, description=DESCRIPTION)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        '--cutoff-days',
        type=int,
        default=None,
        help='Override INITIAL_CUTOFF_DAYS for this run (only effective on FIRST run when DB is empty).',
    )
    mode.add_argument(
        '--backfill-days',
        type=int,
        default=None,
        help='Backfill mode: fetch from N days ago, skip already-downloaded ones. '
             'Ignores last_seen state. For one-off historical collection.',
    )
    p.add_argument(
        '--dry-run',
        action='store_true',
        help='Show which messages would be downloaded without saving anything.',
    )
    p.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Set log level to DEBUG.',
    )
    p.set_defaults(func=collect)
    return p


def setup_logging(verbose: bool, level_str: str = 'INFO') -> None:
    level = logging.DEBUG if verbose else getattr(logging, level_str.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format='%(asctime)s %(levelname)-8s %(name)-10s %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        stream=sys.stderr,
    )


def compute_exit_code(result: RunResult) -> int:
    """Map a RunResult to an exit code per collector design §5.5."""
    if result.failed > 0 or result.retried_fail > 0:
        return 2
    return 0


async def _amain(args: argparse.Namespace, config: Config) -> int:
    async with TelegramClient(
        api_id=config.telegram_api_id,
        api_hash=config.telegram_api_hash,
        session_path=config.telegram_session_path,
    ) as client:
        storage = build_storage(
            supabase_url=config.supabase_url,
            supabase_service_key=config.supabase_service_key,
            base_dir=config.storage_base_dir,
        )
        if args.dry_run:
            return await _dry_run(client, storage, config, backfill_days=args.backfill_days)
        result = await run_collection(client, storage, config, backfill_days=args.backfill_days)
        return compute_exit_code(result)


async def _dry_run(client, storage, config, backfill_days: int | None = None) -> int:
    """List which messages would be processed, without writing anything.

    With backfill_days set, mirrors the real backfill-mode dedupe: fetch
    from N days ago and skip ids already in reports OR failed_attempts.
    Reports separate counts of "new" vs "already-known skipped" so the
    user can size disk and time before launching the real run
    (backfill design §1.3).

    Telegram fetch uses config.channel_ref(); storage lookups use the
    chat_username label (config.telegram_channel) so existing rows are
    deduped correctly.
    """
    channel_ref = config.channel_ref()
    chat_label = config.telegram_channel

    if backfill_days is not None:
        existing_ids = storage.get_all_message_ids(chat_label)
        existing_ids.update(storage.get_failed_message_ids(chat_label))
        log.info(
            "DRY RUN (backfill mode): %d existing message_ids will be skipped, "
            "fetching from %d days ago",
            len(existing_ids), backfill_days,
        )
        msgs = client.iter_messages_since_date(channel_ref, backfill_days)
    else:
        existing_ids = None
        last_seen = storage.get_max_seen_message_id(chat_label)
        if last_seen == 0:
            msgs = client.iter_messages_since_date(channel_ref, config.initial_cutoff_days)
        else:
            msgs = client.iter_messages_after_id(channel_ref, last_seen)

    log.info("DRY RUN — would process the following:")
    n = 0
    n_skipped_existing = 0
    async for msg in msgs:
        if not has_pdf(msg):
            continue
        if existing_ids is not None and msg.id in existing_ids:
            n_skipped_existing += 1
            continue
        log.info("  msg_id=%s sent_at=%s file=%s",
                 msg.id, msg.date.isoformat(), _get_original_filename(msg))
        n += 1

    log.info("DRY RUN — total %d new PDF messages", n)
    if existing_ids is not None:
        log.info("DRY RUN — also %d already-known messages skipped", n_skipped_existing)
    return 0


def collect(args: argparse.Namespace) -> int:
    """Run the collect command for the parsed ``args``; return the exit code."""
    try:
        config = load_config()
    except MissingSetting as e:
        print(f"Config error: Missing required env var: {e.name}", file=sys.stderr)
        return 1

    setup_logging(args.verbose, level_str=config.log_level)

    # Apply CLI overrides on top of env config
    if args.cutoff_days is not None:
        config = dataclasses.replace(config, initial_cutoff_days=args.cutoff_days)

    try:
        return asyncio.run(_amain(args, config))
    except KeyboardInterrupt:
        log.warning("Interrupted by user")
        return 1
    except Exception:
        log.exception("Fatal error")
        return 1
