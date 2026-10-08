"""``tag`` command: ``python -m research_desk tag run|inspect|escalate|reset-worker``."""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
from datetime import datetime, timezone

from research_desk.core import db
from research_desk.core.llm import LLMClient
from research_desk.domain.stocks import StockList
from research_desk.tagger.orchestrator import run_batch
from research_desk.tagger.settings import load_config
from research_desk.tagger.sql import ESCALATION_PICK_SQL, INSPECT_SUMMARY_SQL, RESET_WORKER_SQL


def _make_worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}-{secrets.token_hex(2)}"


def _parse_row_ids(s: str) -> list[int]:
    return [int(x) for x in s.split(",") if x.strip()]


def _make_llm_client(cfg) -> LLMClient:
    """Provider-routing client (claude-* → Anthropic, codex:* → Codex CLI, else OpenAI).

    LangSmith wrapping happens inside LLMClient when LANGSMITH_TRACING=true.
    """
    return LLMClient(
        openai_api_key=cfg.openai_api_key,
        anthropic_api_key=cfg.anthropic_api_key,
        max_retries=2, timeout=60.0,
    )


def _mtime_version(stock_list: StockList) -> str:
    ts = datetime.fromtimestamp(stock_list.path.stat().st_mtime)
    return f"KRX@{ts:%Y-%m-%d}"


async def _cmd_run(args, cfg):
    sb = await db.SupabaseSQL.from_env(max_size=10)
    client = _make_llm_client(cfg)
    krx = StockList.load(cfg.krx_csv_path)
    # Auto-generated unless --worker-id provided. Wrappers pass an explicit id
    # so they can scope reset-worker to exactly the failed run if it crashes.
    worker_id = args.worker_id or _make_worker_id()
    try:
        report = await run_batch(
            sb=sb, client=client, krx=krx,
            taxonomy_version=_mtime_version(krx),
            batch_size=args.batch_size,
            dry_run=args.dry_run,
            row_ids=_parse_row_ids(args.row_ids) if args.row_ids else [],
            model=args.model or cfg.model_default,
            max_concurrent_llm=args.max_concurrent_llm or cfg.max_concurrent_llm,
            worker_id=worker_id,
            lock_ttl_minutes=cfg.lock_ttl_minutes,
            per_row_deadline_s=cfg.per_row_deadline_s,
        )
        report["worker_id"] = worker_id
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    finally:
        await sb.close()
        await client.close()


async def _cmd_reset_worker(args, cfg):
    sb = await db.SupabaseSQL.from_env(max_size=10)
    try:
        rows = await sb.fetch(RESET_WORKER_SQL, [args.worker_id])
        result = {
            "worker_id": args.worker_id,
            "reset_count": len(rows),
            "ids": [r["id"] for r in rows],
        }
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    finally:
        await sb.close()


async def _cmd_inspect(args, cfg):
    sb = await db.SupabaseSQL.from_env(max_size=10)
    try:
        rows = await sb.fetch(INSPECT_SUMMARY_SQL)
        print(json.dumps(rows[0] if rows else {}, indent=2, ensure_ascii=False, default=str))
    finally:
        await sb.close()


async def _cmd_escalate(args, cfg):
    sb = await db.SupabaseSQL.from_env(max_size=10)
    client = _make_llm_client(cfg)
    krx = StockList.load(cfg.krx_csv_path)
    try:
        since = datetime.fromisoformat(args.since)
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        rows = await sb.fetch(ESCALATION_PICK_SQL, [since])
        ids = [r["id"] for r in rows]
        if not ids:
            print(json.dumps({"escalated": 0, "since": args.since}))
            return
        report = await run_batch(
            sb=sb, client=client, krx=krx,
            taxonomy_version=_mtime_version(krx),
            batch_size=len(ids),
            dry_run=False,
            row_ids=ids,
            model=args.model or cfg.model_escalation,
            max_concurrent_llm=args.max_concurrent_llm or cfg.max_concurrent_llm,
            worker_id=_make_worker_id(),
            lock_ttl_minutes=cfg.lock_ttl_minutes,
            per_row_deadline_s=cfg.per_row_deadline_s,
        )
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    finally:
        await sb.close()
        await client.close()


def _handler(command):
    def run(args) -> int:
        cfg = load_config()
        args.batch_size = getattr(args, "batch_size", None) or cfg.batch_size_default
        asyncio.run(command(args, cfg))
        return 0
    return run


def register(subparsers) -> None:
    """Add ``tag`` and its subcommands. Each leaf sets ``func(args) -> exit code``."""
    tag = subparsers.add_parser("tag", help="리포트 분류: run / inspect / escalate / reset-worker")
    sub = tag.add_subparsers(dest="tag_command", required=True)

    p_run = sub.add_parser("run", help="claim + tag a batch of pending rows")
    p_run.add_argument("--batch-size", type=int, default=None)
    p_run.add_argument("--model", type=str, default=None)
    p_run.add_argument("--dry-run", action="store_true")
    p_run.add_argument("--row-ids", type=str, default=None,
                       help="comma-separated ids; skips atomic claim")
    p_run.add_argument("--max-concurrent-llm", type=int, default=None)
    p_run.add_argument("--worker-id", type=str, default=None,
                       help="override auto-generated worker_id; required for "
                            "wrapper-managed reset-worker cleanup on failure")
    p_run.set_defaults(func=_handler(_cmd_run))

    p_inspect = sub.add_parser("inspect", help="print queue distribution")
    p_inspect.set_defaults(func=_handler(_cmd_inspect))

    p_esc = sub.add_parser("escalate", help="re-tag rows in review_needed since a timestamp")
    p_esc.add_argument("--since", type=str, required=True,
                       help="ISO timestamp, e.g. 2026-05-08T09:00")
    p_esc.add_argument("--model", type=str, default=None)
    p_esc.add_argument("--max-concurrent-llm", type=int, default=None)
    p_esc.set_defaults(func=_handler(_cmd_escalate))

    p_reset = sub.add_parser(
        "reset-worker",
        help="revert 'processing' rows back to 'pending' for ONE worker_id "
             "(safe wrapper cleanup after a crashed run)",
    )
    p_reset.add_argument("--worker-id", type=str, required=True)
    p_reset.set_defaults(func=_handler(_cmd_reset_worker))
