"""DB reads and writes of the two price tables (Supabase REST).

This feature owns ``stock_price_snapshot`` (one row per stock, the latest snapshot) and
``price_update_runs`` (one row per ``prices update`` run), spec §14 / migration 008. Every function
takes the supabase-py client as its first argument. Values are stored as the logic makes them:
returns in percent, money in whole won, dates as ``YYYY-MM-DD``, times as ISO text with an offset.

- Snapshot upserts go ``UPSERT_ROWS`` rows a request, on ``stock_code``. A row changes only in the
  columns sent, so a ``{stock_code, flags}`` upsert leaves the values as they were.
- Reads by code ask ``CODES_PER_QUERY`` codes a request (a short request address; each answer is
  under Supabase's 1000-row limit). The full read of stored returns pages ``PAGE`` rows at a time
  in stock-code order, with a new query for each page: postgrest-py's ``range()`` adds
  offset/limit to the query instead of replacing them.
- A run starts as ``running`` and is finished with its status, counts and message. "Latest" is the
  latest ``started_at``, then the higher ``run_id``.

Not atomic: a run's upserts are separate requests. If one fails half way, the stocks written so
far keep their new values and the run is closed as ``failed``; the next run writes them all again.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from .logic import SUCCESS_STATUSES

SNAPSHOT_TABLE = "stock_price_snapshot"
RUNS_TABLE = "price_update_runs"

PAGE = 1000            # Supabase REST answers at most 1000 rows a request
CODES_PER_QUERY = 200  # codes in one in_() filter
UPSERT_ROWS = 500      # rows in one upsert request

SNAPSHOT_COLUMNS: tuple[str, ...] = (
    "stock_code", "market", "as_of", "close", "market_cap", "avg_value_20d", "traded",
    "ret_1w", "ret_1m", "ret_3m", "ret_6m", "xret_1w", "xret_1m", "xret_3m", "xret_6m",
    "flags", "source", "updated_at",
)
RETURN_COLUMNS: tuple[str, ...] = ("stock_code", "market", "ret_1w", "ret_1m", "ret_3m", "ret_6m")
RUN_COLUMNS: tuple[str, ...] = (
    "run_id", "started_at", "finished_at", "status", "as_of", "stocks_total", "stocks_ok",
    "stocks_failed", "message",
)


def _columns(names: Sequence[str]) -> str:
    return ", ".join(names)


# ── snapshots ────────────────────────────────────────────────────────────────

def upsert_snapshots(sb, rows: Sequence[Mapping[str, Any]]) -> None:
    """Insert or update snapshot rows by stock_code, ``UPSERT_ROWS`` a request. All rows of one
    call should carry the same columns (a column missing from one row would be written as null)."""
    rows = list(rows)
    for start in range(0, len(rows), UPSERT_ROWS):
        sb.table(SNAPSHOT_TABLE).upsert(rows[start:start + UPSERT_ROWS], on_conflict="stock_code").execute()


def read_snapshots(sb, codes: Sequence[str], columns: Sequence[str] = SNAPSHOT_COLUMNS) -> dict[str, dict]:
    """``{stock_code: row}`` of the stored snapshots of ``codes`` (distinct codes; absent ones are
    left out), ``CODES_PER_QUERY`` codes a request."""
    codes = list(codes)
    found: dict[str, dict] = {}
    for start in range(0, len(codes), CODES_PER_QUERY):
        result = (sb.table(SNAPSHOT_TABLE)
                    .select(_columns(columns))
                    .in_("stock_code", codes[start:start + CODES_PER_QUERY])
                    .execute())
        found.update((row["stock_code"], row) for row in result.data or [])
    return found


def read_all_returns(sb) -> list[dict]:
    """Every stored snapshot's stock_code, market and ret_*, in stock-code order, ``PAGE`` rows a
    request until a short page."""
    rows: list[dict] = []
    offset = 0
    while True:
        result = (sb.table(SNAPSHOT_TABLE)
                    .select(_columns(RETURN_COLUMNS))
                    .order("stock_code")
                    .range(offset, offset + PAGE - 1)
                    .execute())
        page = result.data or []
        rows.extend(page)
        if len(page) < PAGE:
            return rows
        offset += PAGE


# ── run records ──────────────────────────────────────────────────────────────

def start_run(sb, *, started_at: str, total: int) -> int:
    """Record a new ``running`` run; returns its run_id."""
    result = (sb.table(RUNS_TABLE)
                .insert({"started_at": started_at, "status": "running", "stocks_total": total})
                .execute())
    return result.data[0]["run_id"]


def finish_run(sb, run_id: int, *, status: str, finished_at: str, as_of: Optional[str] = None,
               stocks_ok: Optional[int] = None, stocks_failed: Optional[int] = None,
               message: Optional[str] = None) -> None:
    """Close run ``run_id`` with its status, end time, as_of, counts and message."""
    (sb.table(RUNS_TABLE)
       .update({"status": status, "finished_at": finished_at, "as_of": as_of, "stocks_ok": stocks_ok,
                "stocks_failed": stocks_failed, "message": message})
       .eq("run_id", run_id)
       .execute())


def _latest(query) -> Optional[dict]:
    result = query.order("started_at", desc=True).order("run_id", desc=True).limit(1).execute()
    return result.data[0] if result.data else None


def latest_run(sb) -> Optional[dict]:
    """The latest run record (any status), or None."""
    return _latest(sb.table(RUNS_TABLE).select(_columns(RUN_COLUMNS)))


def latest_successful_run(sb) -> Optional[dict]:
    """The latest ``ok`` / ``partial`` run record, or None."""
    return _latest(sb.table(RUNS_TABLE).select(_columns(RUN_COLUMNS)).in_("status", list(SUCCESS_STATUSES)))
