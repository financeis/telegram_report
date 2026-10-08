"""``tag`` command (spec §5): ``python -m research_desk tag run|inspect|escalate|reset-worker``.

``register(subparsers)`` adds the command. Each subcommand sets
``func(args) -> exit code``: 0 = done, 4 = not ready (a start-up check failed;
the message is on stderr and no DB function was called, so no row was claimed).
Any other error propagates as an exception (exit code 1).

Start-up checks, all before the DB pool opens (spec §5, §7, §8):
- run / escalate: the key of the model actually used (``--model``, else
  LLM_MODEL_DEFAULT / LLM_MODEL_ESCALATION; a ``codex:`` model needs the codex
  CLI instead of a key), SUPABASE_DB_URL, then the stock list (KRX_CSV_PATH):
  it must load and match its version file. Tagged rows record that file's
  version as ``taxonomy_version``. ``--dry-run`` checks the same.
- inspect / reset-worker: SUPABASE_DB_URL only.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from research_desk.core import db, llm, settings
from research_desk.domain.stocks import StockList, StockListError
from research_desk.tagger import settings as tagger_settings
from research_desk.tagger.orchestrator import run_batch
from research_desk.tagger.sql import ESCALATION_PICK_SQL, INSPECT_SUMMARY_SQL, RESET_WORKER_SQL

EXIT_NOT_READY = 4

# stderr texts for exit code 4 besides "<NAME> is required" (missing setting)
# and "codex CLI not found for model <model>" (spec §5, §8).
STOCK_LIST_UNREADABLE = "종목표 파일을 읽을 수 없습니다: {reason}"
STOCK_LIST_VERSION_MISMATCH = (
    "종목표 파일 내용이 버전 정보와 다릅니다. 종목표를 바꿨다면 "
    "python -m research_desk stocks set-version --as-of <자료 기준일, YYYY-MM-DD>를 "
    "실행한 뒤 다시 시작하세요."
)


class NotReadyToRun(Exception):
    """A start-up check failed; ``str()`` is the stderr message (exit code 4)."""


@dataclass(frozen=True)
class _Tagging:
    """What run / escalate use once the start-up checks passed."""
    model: str
    krx: StockList
    taxonomy_version: str
    max_concurrent_llm: int
    lock_ttl_minutes: int
    per_row_deadline_s: float


# ── start-up checks ──────────────────────────────────────────────────────────

def _require(name: str) -> str:
    try:
        return settings.required(name)
    except settings.MissingSetting as exc:   # "<NAME> is required"
        raise NotReadyToRun(str(exc)) from exc


def _check_model_access(model: str) -> None:
    """The API key the model's provider needs; for a ``codex:`` model, the codex CLI."""
    env = llm.api_key_env(model)
    if env is not None:
        _require(env)
        return
    try:
        llm.LLMClient().require_key(model)
    except RuntimeError as exc:   # "codex CLI not found for model <model>"
        raise NotReadyToRun(str(exc)) from exc


def _load_stock_list() -> tuple[StockList, str]:
    """The stock list and its version. It must load and match its version file."""
    try:
        krx = StockList.load(settings.krx_csv_path())
    except StockListError as exc:
        raise NotReadyToRun(STOCK_LIST_UNREADABLE.format(reason=exc)) from exc
    check = krx.verify()
    if not check.ok:   # version file missing, invalid, or for other content
        raise NotReadyToRun(STOCK_LIST_VERSION_MISMATCH)
    return krx, check.version


def _prepare_tagging(model: str, max_concurrent_flag: Optional[int]) -> _Tagging:
    """run / escalate start-up checks, then the settings the batch uses."""
    _check_model_access(model)
    _require("SUPABASE_DB_URL")
    krx, taxonomy_version = _load_stock_list()
    # Read even when a flag overrides it (as before): a malformed value fails.
    max_concurrent_setting = tagger_settings.max_concurrent_llm()
    return _Tagging(
        model=model,
        krx=krx,
        taxonomy_version=taxonomy_version,
        max_concurrent_llm=max_concurrent_flag or max_concurrent_setting,
        lock_ttl_minutes=tagger_settings.lock_ttl_minutes(),
        per_row_deadline_s=tagger_settings.per_row_deadline_s(),
    )


def _not_ready(exc: NotReadyToRun) -> int:
    print(exc, file=sys.stderr)
    return EXIT_NOT_READY


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}-{secrets.token_hex(2)}"


def _parse_row_ids(s: str) -> list[int]:
    return [int(x) for x in s.split(",") if x.strip()]


def _make_llm_client() -> llm.LLMClient:
    """Provider-routing client (claude-* → Anthropic, codex:* → Codex CLI, else OpenAI).

    SDK retries 2, request timeout 60 s (spec §9.3). LangSmith wrapping happens
    inside LLMClient when LANGSMITH_TRACING=true.
    """
    return llm.LLMClient(
        openai_api_key=settings.openai_api_key(),
        anthropic_api_key=settings.anthropic_api_key(),
        max_retries=tagger_settings.LLM_MAX_RETRIES,
        timeout=tagger_settings.LLM_TIMEOUT_S,
    )


async def _open_db() -> db.SupabaseSQL:
    return await db.SupabaseSQL.from_env(max_size=tagger_settings.DB_POOL_MAX_SIZE)


def _print_json(data: dict) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


# ── run ──────────────────────────────────────────────────────────────────────

def _run(args) -> int:
    settings.load_env()
    try:
        tagging = _prepare_tagging(args.model or tagger_settings.default_model(),
                                   args.max_concurrent_llm)
    except NotReadyToRun as exc:
        return _not_ready(exc)
    batch_size_setting = tagger_settings.batch_size_default()
    batch_size = args.batch_size or batch_size_setting
    row_ids = _parse_row_ids(args.row_ids) if args.row_ids else []
    # Auto-generated unless --worker-id provided. Wrappers pass an explicit id
    # so they can scope reset-worker to exactly the failed run if it crashes.
    worker_id = args.worker_id or _make_worker_id()
    asyncio.run(_cmd_run(tagging, batch_size=batch_size, dry_run=args.dry_run,
                         row_ids=row_ids, worker_id=worker_id))
    return 0


async def _cmd_run(tagging: _Tagging, *, batch_size: int, dry_run: bool,
                   row_ids: list[int], worker_id: str) -> None:
    sb = await _open_db()
    client = _make_llm_client()
    try:
        report = await run_batch(
            sb=sb, client=client, krx=tagging.krx,
            taxonomy_version=tagging.taxonomy_version,
            batch_size=batch_size,
            dry_run=dry_run,
            row_ids=row_ids,
            model=tagging.model,
            max_concurrent_llm=tagging.max_concurrent_llm,
            worker_id=worker_id,
            lock_ttl_minutes=tagging.lock_ttl_minutes,
            per_row_deadline_s=tagging.per_row_deadline_s,
        )
        report["worker_id"] = worker_id
        _print_json(report)
    finally:
        await sb.close()
        await client.close()


# ── escalate ─────────────────────────────────────────────────────────────────

def _escalate(args) -> int:
    settings.load_env()
    try:
        tagging = _prepare_tagging(args.model or tagger_settings.escalation_model(),
                                   args.max_concurrent_llm)
    except NotReadyToRun as exc:
        return _not_ready(exc)
    asyncio.run(_cmd_escalate(tagging, since_text=args.since))
    return 0


async def _cmd_escalate(tagging: _Tagging, *, since_text: str) -> None:
    sb = await _open_db()
    client = _make_llm_client()
    try:
        since = datetime.fromisoformat(since_text)
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        rows = await sb.fetch(ESCALATION_PICK_SQL, [since])
        ids = [r["id"] for r in rows]
        if not ids:
            print(json.dumps({"escalated": 0, "since": since_text}))
            return
        # Re-tags the picked rows by id without re-checking their status
        # (known race with a manual review of the same row, kept as is).
        report = await run_batch(
            sb=sb, client=client, krx=tagging.krx,
            taxonomy_version=tagging.taxonomy_version,
            batch_size=len(ids),
            dry_run=False,
            row_ids=ids,
            model=tagging.model,
            max_concurrent_llm=tagging.max_concurrent_llm,
            worker_id=_make_worker_id(),
            lock_ttl_minutes=tagging.lock_ttl_minutes,
            per_row_deadline_s=tagging.per_row_deadline_s,
        )
        _print_json(report)
    finally:
        await sb.close()
        await client.close()


# ── inspect / reset-worker ───────────────────────────────────────────────────

def _inspect(args) -> int:
    settings.load_env()
    try:
        _require("SUPABASE_DB_URL")
    except NotReadyToRun as exc:
        return _not_ready(exc)
    asyncio.run(_cmd_inspect())
    return 0


async def _cmd_inspect() -> None:
    sb = await _open_db()
    try:
        rows = await sb.fetch(INSPECT_SUMMARY_SQL)
        _print_json(rows[0] if rows else {})
    finally:
        await sb.close()


def _reset_worker(args) -> int:
    settings.load_env()
    try:
        _require("SUPABASE_DB_URL")
    except NotReadyToRun as exc:
        return _not_ready(exc)
    asyncio.run(_cmd_reset_worker(args.worker_id))
    return 0


async def _cmd_reset_worker(worker_id: str) -> None:
    """Revert 'processing' rows back to 'pending' for ONE specific worker_id.

    Scoped narrower than stale-lock-reclaim's TTL-based sweep so a wrapper can
    safely cleanup after its own failed child without racing other live workers
    on the same machine. Returns affected ids as JSON for audit.
    """
    sb = await _open_db()
    try:
        rows = await sb.fetch(RESET_WORKER_SQL, [worker_id])
        _print_json({
            "worker_id": worker_id,
            "reset_count": len(rows),
            "ids": [r["id"] for r in rows],
        })
    finally:
        await sb.close()


# ── command list entry ───────────────────────────────────────────────────────

def register(subparsers) -> None:
    """Add ``tag`` and its subcommands. Each subcommand sets ``func(args) -> exit code``."""
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
    p_run.set_defaults(func=_run)

    p_inspect = sub.add_parser("inspect", help="print queue distribution")
    p_inspect.set_defaults(func=_inspect)

    p_esc = sub.add_parser("escalate", help="re-tag rows in review_needed since a timestamp")
    p_esc.add_argument("--since", type=str, required=True,
                       help="ISO timestamp, e.g. 2026-05-08T09:00")
    p_esc.add_argument("--model", type=str, default=None)
    p_esc.add_argument("--max-concurrent-llm", type=int, default=None)
    p_esc.set_defaults(func=_escalate)

    p_reset = sub.add_parser(
        "reset-worker",
        help="revert 'processing' rows back to 'pending' for ONE worker_id "
             "(safe wrapper cleanup after a crashed run)",
    )
    p_reset.add_argument("--worker-id", type=str, required=True)
    p_reset.set_defaults(func=_reset_worker)
