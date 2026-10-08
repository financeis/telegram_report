"""Batch orchestration: claim → fan-out via Semaphore → aggregate.

Per-row deadline + broad except boundary so a single row failure cannot crash
asyncio.gather() and leave others stuck in 'processing'. lock_ttl_minutes is
bound to STALE_LOCK_RECLAIM_SQL via $1.

NOTE: env values are NOT read at module import time. The ``tag`` command reads
its settings when it starts and passes lock_ttl_minutes / per_row_deadline_s
into run_batch() explicitly.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

from research_desk.domain.stocks import StockList
from research_desk.tagger.graph import build_graph
from research_desk.tagger.nodes.llm_extract import LLMTransientError
from research_desk.tagger.sql import (
    ATOMIC_CLAIM_SQL, DRY_RUN_SELECT_SQL, REVERT_TO_PENDING_SQL,
    ROW_IDS_FETCH_SQL, STALE_LOCK_RECLAIM_SQL,
)


async def run_batch(
    *,
    sb,
    client,
    krx: StockList,
    taxonomy_version: str,
    batch_size: int,
    dry_run: bool,
    row_ids: list[int],
    model: str,
    max_concurrent_llm: int,
    worker_id: str,
    lock_ttl_minutes: int = 30,
    per_row_deadline_s: float = 90.0,
) -> dict[str, Any]:
    """Process a batch of pending reports.

    - run mode: stale_reclaim → atomic_claim → graph fan-out → aggregate
    - dry_run mode: SELECT only, no status mutation
    - row_ids mode: skip claim, fetch by id, status not mutated even if not 'pending'
    """
    # 1. stale lock reclaim (only in normal run mode). lock_ttl_minutes bound.
    if not row_ids and not dry_run:
        await sb.execute(STALE_LOCK_RECLAIM_SQL, [lock_ttl_minutes])

    # 2. fetch rows
    if row_ids:
        rows = await sb.fetch(ROW_IDS_FETCH_SQL, [row_ids])
    elif dry_run:
        rows = await sb.fetch(DRY_RUN_SELECT_SQL, [batch_size])
    else:
        rows = await sb.fetch(ATOMIC_CLAIM_SQL, [worker_id, batch_size])

    if not rows:
        return _empty_report(model)

    # 3. fan-out via Semaphore + per-row deadline + broad except boundary
    app = build_graph(client, sb, krx=krx, dry_run=dry_run, taxonomy_version=taxonomy_version)
    sem = asyncio.Semaphore(max_concurrent_llm)

    async def _revert(row_id: int) -> None:
        if not dry_run and not row_ids:
            try:
                await sb.execute(REVERT_TO_PENDING_SQL, [row_id])
            except Exception:
                # If REVERT itself fails, row stays 'processing' and stale-lock
                # reclaim recovers it after LOCK_TTL_MINUTES.
                pass

    async def _process(row):
        async with sem:
            init_state = {**row, "worker_id": worker_id, "model": model}
            try:
                final = await asyncio.wait_for(
                    app.ainvoke(init_state),
                    timeout=per_row_deadline_s,
                )
                return {"id": row["id"], **final}
            except LLMTransientError as e:
                await _revert(row["id"])
                return {"id": row["id"], "error": "transient", "detail": str(e)}
            except asyncio.TimeoutError:
                await _revert(row["id"])
                return {"id": row["id"], "error": "deadline_exceeded"}
            except Exception as e:
                # Broad except defends gather() from any unexpected node/IO error.
                # Row reverts to pending so a future run retries.
                await _revert(row["id"])
                return {"id": row["id"], "error": "unhandled",
                        "detail": f"{type(e).__name__}:{e}"}

    results = await asyncio.gather(*[_process(r) for r in rows])

    # 4. aggregate
    return _aggregate(results, model=model, batch_size=batch_size, dry_run=dry_run)


def _empty_report(model: str) -> dict:
    return {
        "model": model, "processed": 0,
        "auto": 0, "review_needed": 0,
        "confidence": {"high": 0, "medium": 0, "low": 0},
        "oos": {"foreign": 0, "fund": 0, "digital": 0, "private": 0, "ir_self": 0},
        "review_reasons": {},
        "transient_errors": 0,
        "deadline_errors": 0,
        "unhandled_errors": 0,
    }


def _aggregate(results: list[dict], *, model: str, batch_size: int, dry_run: bool) -> dict:
    auto = sum(1 for r in results if r.get("tagging_status") == "auto")
    review = sum(1 for r in results if r.get("tagging_status") == "review_needed")
    transient = sum(1 for r in results if r.get("error") == "transient")
    deadline = sum(1 for r in results if r.get("error") == "deadline_exceeded")
    unhandled = sum(1 for r in results if r.get("error") == "unhandled")

    conf_counter = Counter(r.get("tagging_confidence") for r in results if "tagging_confidence" in r)
    oos_counter = Counter(r.get("oos_reason") for r in results if r.get("is_oos"))

    review_reasons: Counter[str] = Counter()
    for r in results:
        notes = r.get("tagging_notes") or ""
        for token in notes.split(";"):
            if not token:
                continue
            tag = token.split(":", 1)[0]
            if tag in ("first_page_unreadable", "llm_refusal", "type_indeterminate",
                       "krx_unmatched_in_scope"):
                review_reasons[tag] += 1

    return {
        "model": model,
        "processed": len(results),
        "auto": auto,
        "review_needed": review,
        "confidence": {
            "high": conf_counter.get("high", 0),
            "medium": conf_counter.get("medium", 0),
            "low": conf_counter.get("low", 0),
        },
        "oos": {
            "foreign":  oos_counter.get("foreign", 0),
            "fund":     oos_counter.get("fund", 0),
            "digital":  oos_counter.get("digital", 0),
            "private":  oos_counter.get("private", 0),
            "ir_self":  oos_counter.get("ir_self", 0),
        },
        "review_reasons": dict(review_reasons),
        "transient_errors": transient,
        "deadline_errors": deadline,
        "unhandled_errors": unhandled,
        "dry_run": dry_run,
        "batch_size": batch_size,
    }
