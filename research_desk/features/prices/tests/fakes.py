"""In-memory stand-ins for the prices tests: the supabase-py builder over the two price tables,
and the KIS client.

``FakeSupabase`` serves the calls features.prices makes: select (columns as one comma-separated
string, or '*'), eq, in_, order, limit, range, insert, upsert (on_conflict='stock_code'),
update, execute. It behaves like Supabase / PostgREST where the feature depends on it:

- one response holds at most ``MAX_ROWS`` (1000) rows;
- a NULL value never passes eq / in_; ``order`` puts NULLs last ascending and first descending;
- a bulk upsert writes the union of its rows' keys (a key missing from one row is NULL there,
  supabase-py's ``default_to_null``); an existing row changes only in those columns; a new row
  gets the table defaults for the columns not written (flags '{}', source 'KIS', updated_at now);
  one statement may not touch the same stock twice;
- inserting a run gives it the next ``run_id`` and the defaults (started_at now, status
  'running');
- the NOT NULL and CHECK constraints of migration 008 hold and unknown columns are refused; a
  violation raises ``FakeDBError`` and the statement writes nothing (like the API error of a real
  request). Payloads must be JSON (a ``date`` object or NaN fails, like supabase-py's request);
- ``range()`` may be called once per query: postgrest-py 2.29 appends offset/limit instead of
  replacing them, so a paged read must build a new query for each page.

Each execution is kept in ``executed``; ``writes()`` lists the inserts, upserts and updates.
``fail_next(kind)`` makes the next query of that kind ('select', 'insert', 'upsert', 'update')
raise FakeDBError (a DB outage).

``FakeKis`` is the KIS client of one run (``KisClient.daily_prices`` / ``quote``).
"""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, timedelta
from functools import cmp_to_key
from types import SimpleNamespace
from typing import Any, Callable, Optional

SNAPSHOT = "stock_price_snapshot"
RUNS = "price_update_runs"
MAX_ROWS = 1000

# migration 008
SNAPSHOT_COLUMNS = ("stock_code", "market", "as_of", "close", "market_cap", "avg_value_20d", "traded",
                    "ret_1w", "ret_1m", "ret_3m", "ret_6m", "xret_1w", "xret_1m", "xret_3m", "xret_6m",
                    "flags", "source", "updated_at")
RUN_COLUMNS = ("run_id", "started_at", "finished_at", "status", "as_of", "stocks_total", "stocks_ok",
               "stocks_failed", "message")
COLUMNS = {SNAPSHOT: SNAPSHOT_COLUMNS, RUNS: RUN_COLUMNS}
MARKETS = (None, "KOSPI", "KOSDAQ")
STATUSES = ("running", "ok", "partial", "failed")

# The DB settings the tests give (never a real project).
URL, KEY = "https://example.supabase.test", "service-key"


class FakeDBError(Exception):
    """What a failed request raises (postgrest's APIError in the real client)."""


class FakeQuery:
    """One chained query on one of the two tables."""

    def __init__(self, db: "FakeSupabase", table: str) -> None:
        assert table in COLUMNS, f"prices reads and writes only its two tables, got {table!r}"
        self.db = db
        self.table = table
        self.kind: Optional[str] = None
        self.columns: Optional[list[str]] = None       # None: every column
        self.payload: Any = None
        self.on_conflict: Optional[str] = None
        self.filters: list[tuple[str, str, Any]] = []  # (operator, column, value)
        self.orders: list[tuple[str, bool]] = []       # (column, desc)
        self.offset: Optional[int] = None
        self.limit_n: Optional[int] = None

    # ── building ─────────────────────────────────────────────────────────────

    def _begin(self, kind: str) -> None:
        assert self.kind is None, f"one query is one statement: {self.kind} then {kind}"
        self.kind = kind

    def select(self, columns: str = "*", count: Optional[str] = None) -> "FakeQuery":
        self._begin("select")
        if columns.strip() != "*":
            self.columns = [name.strip() for name in columns.split(",")]
            for name in self.columns:
                self.db.check_column(self.table, name)
        return self

    def insert(self, payload: Any, **options: Any) -> "FakeQuery":
        self._begin("insert")
        self.payload = self.db.as_json(payload)
        return self

    def upsert(self, payload: Any, *, on_conflict: str = "", **options: Any) -> "FakeQuery":
        self._begin("upsert")
        self.payload, self.on_conflict = self.db.as_json(payload), on_conflict
        return self

    def update(self, values: dict, **options: Any) -> "FakeQuery":
        self._begin("update")
        self.payload = self.db.as_json(values)
        return self

    def eq(self, column: str, value: Any) -> "FakeQuery":
        self.db.check_column(self.table, column)
        self.filters.append(("eq", column, value))
        return self

    def in_(self, column: str, values) -> "FakeQuery":
        self.db.check_column(self.table, column)
        self.filters.append(("in", column, list(values)))
        return self

    def order(self, column: str, *, desc: bool = False, nullsfirst: Optional[bool] = None) -> "FakeQuery":
        self.db.check_column(self.table, column)
        assert nullsfirst is None, "the prices reads keep PostgreSQL's NULL placement"
        self.orders.append((column, desc))
        return self

    def limit(self, size: int) -> "FakeQuery":
        self.limit_n = size
        return self

    def range(self, start: int, end: int) -> "FakeQuery":
        assert self.offset is None, ("range() twice on one query: postgrest-py appends offset/limit "
                                     "instead of replacing them; build a new query for each page")
        self.offset, self.limit_n = start, end - start + 1
        return self

    # ── looking at it ────────────────────────────────────────────────────────

    def filter_values(self, operator: str, column: str) -> list[Any]:
        return [value for op, col, value in self.filters if (op, col) == (operator, column)]

    @property
    def rows_sent(self) -> list[dict]:
        """The rows of an insert or upsert."""
        return self.payload if isinstance(self.payload, list) else [self.payload]

    # ── running it ───────────────────────────────────────────────────────────

    def execute(self) -> SimpleNamespace:
        assert self.kind is not None, "a query must call select / insert / upsert / update first"
        self.db.executed.append(self)
        if self.db.failing.get(self.kind):
            self.db.failing[self.kind] -= 1
            raise FakeDBError(f"fake DB outage on {self.kind} {self.table}")
        return SimpleNamespace(data=getattr(self, f"_{self.kind}")(), count=None)

    def _passes(self, row: dict) -> bool:
        for operator, column, value in self.filters:
            cell = row.get(column)
            if cell is None:
                return False
            if operator == "eq" and cell != value:
                return False
            if operator == "in" and cell not in value:
                return False
        return True

    def _sorted(self, rows: list[dict]) -> list[dict]:
        def compare(a: dict, b: dict) -> int:
            for column, desc in self.orders:
                x, y = a.get(column), b.get(column)
                if x == y:
                    continue
                if x is None or y is None:   # NULLs last ascending, first descending
                    return -1 if (x is None) == desc else 1
                return (1 if x > y else -1) * (-1 if desc else 1)
            return 0
        return sorted(rows, key=cmp_to_key(compare))

    def _select(self) -> list[dict]:
        rows = self._sorted([row for row in self.db.tables[self.table] if self._passes(row)])
        if self.offset:
            rows = rows[self.offset:]
        if self.limit_n is not None:
            rows = rows[:self.limit_n]
        rows = rows[:MAX_ROWS]
        if self.columns is None:
            return deepcopy(rows)
        return [{name: deepcopy(row.get(name)) for name in self.columns} for row in rows]

    def _insert(self) -> list[dict]:
        assert self.table == RUNS, "prices inserts only run records"
        made = []
        for sent in self.rows_sent:
            for name in sent:
                self.db.check_column(RUNS, name)
            row = {name: None for name in RUN_COLUMNS} | {"started_at": self.db.now, "status": "running"}
            row |= deepcopy(sent)
            if row["run_id"] is None:
                row["run_id"] = self.db.next_run_id + len(made)
            self.db.check_run(row)
            made.append(row)
        self.db.next_run_id += len(made)
        self.db.tables[RUNS].extend(made)
        return deepcopy(made)

    def _upsert(self) -> list[dict]:
        assert self.table == SNAPSHOT and self.on_conflict == "stock_code", (
            "prices upserts only snapshot rows, on stock_code")
        sent = self.rows_sent
        columns = list(dict.fromkeys(name for row in sent for name in row))   # PostgREST `columns`
        for name in columns:
            self.db.check_column(SNAPSHOT, name)
        codes = [row.get("stock_code") for row in sent]
        if len(set(codes)) != len(codes):
            raise FakeDBError("ON CONFLICT DO UPDATE command cannot affect row a second time")
        table = {row["stock_code"]: deepcopy(row) for row in self.db.tables[SNAPSHOT]}
        order = list(table)
        written = []
        for row in sent:
            values = {name: deepcopy(row.get(name)) for name in columns}
            code = values.get("stock_code")
            if code in table:
                table[code].update(values)
            else:
                table[code] = ({name: None for name in SNAPSHOT_COLUMNS}
                               | {"flags": [], "source": "KIS", "updated_at": self.db.now} | values)
                order.append(code)
            written.append(table[code])
        for row in written:
            self.db.check_snapshot(row)
        self.db.tables[SNAPSHOT] = [table[code] for code in order]
        return deepcopy(written)

    def _update(self) -> list[dict]:
        for name in self.payload:
            self.db.check_column(self.table, name)
        matched = [row for row in self.db.tables[self.table] if self._passes(row)]
        check = self.db.check_run if self.table == RUNS else self.db.check_snapshot
        for row in matched:
            check(row | self.payload)
        for row in matched:
            row.update(deepcopy(self.payload))
        return deepcopy(matched)


class FakeSupabase:
    """``table(name)`` starts a FakeQuery over ``tables[name]`` (a list of row dicts)."""

    def __init__(self, snapshots: Optional[list[dict]] = None, runs: Optional[list[dict]] = None,
                 now: str = "2026-10-01T00:00:00+00:00") -> None:
        self.tables: dict[str, list[dict]] = {SNAPSHOT: [], RUNS: []}
        self.now = now   # the DB's now() for column defaults
        self.next_run_id = 1
        self.executed: list[FakeQuery] = []
        self.failing: dict[str, int] = {}
        self.made: list[tuple[str, str]] = []   # (url, key) of each client handed out
        for row in snapshots or []:
            self.add_snapshot(row)
        for row in runs or []:
            self.add_run(row)

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self, name)

    # ── setting up ───────────────────────────────────────────────────────────

    def add_snapshot(self, row: dict) -> dict:
        full = ({name: None for name in SNAPSHOT_COLUMNS}
                | {"flags": [], "source": "KIS", "updated_at": self.now} | deepcopy(row))
        self.check_snapshot(full)
        self.tables[SNAPSHOT].append(full)
        return full

    def add_run(self, row: dict) -> dict:
        full = {name: None for name in RUN_COLUMNS} | {"status": "running", "started_at": self.now} | deepcopy(row)
        if full["run_id"] is None:
            full["run_id"] = self.next_run_id
        self.next_run_id = max(self.next_run_id, full["run_id"] + 1)
        self.check_run(full)
        self.tables[RUNS].append(full)
        return full

    def fail_next(self, kind: str, times: int = 1) -> None:
        self.failing[kind] = self.failing.get(kind, 0) + times

    # ── looking at it ────────────────────────────────────────────────────────

    def snapshot(self, code: str) -> Optional[dict]:
        found = [row for row in self.tables[SNAPSHOT] if row["stock_code"] == code]
        return deepcopy(found[0]) if found else None

    def runs(self) -> list[dict]:
        return deepcopy(self.tables[RUNS])

    def queries(self, table: str, kind: Optional[str] = None) -> list[FakeQuery]:
        return [q for q in self.executed if q.table == table and kind in (None, q.kind)]

    def writes(self) -> list[FakeQuery]:
        return [q for q in self.executed if q.kind != "select"]

    # ── the DB's rules ───────────────────────────────────────────────────────

    @staticmethod
    def as_json(payload: Any) -> Any:
        """The payload as the request body carries it: JSON only, no NaN or infinity."""
        try:
            return json.loads(json.dumps(payload, allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise TypeError(f"not a JSON request body: {exc}") from exc

    @staticmethod
    def check_column(table: str, name: str) -> None:
        if name not in COLUMNS[table]:
            raise FakeDBError(f'column "{name}" of relation "{table}" does not exist')

    @staticmethod
    def check_snapshot(row: dict) -> None:
        for name in ("stock_code", "flags", "source", "updated_at"):
            if row.get(name) is None:
                raise FakeDBError(f'null value in column "{name}" violates not-null constraint')
        if row.get("market") not in MARKETS:
            raise FakeDBError('new row violates check constraint "chk_price_snapshot_market"')
        if not (isinstance(row["flags"], list) and all(isinstance(flag, str) for flag in row["flags"])):
            raise FakeDBError("flags must be a text array")

    @staticmethod
    def check_run(row: dict) -> None:
        for name in ("run_id", "started_at", "status"):
            if row.get(name) is None:
                raise FakeDBError(f'null value in column "{name}" violates not-null constraint')
        if row["status"] not in STATUSES:
            raise FakeDBError('new row violates check constraint "chk_price_runs_status"')


# ── KIS ──────────────────────────────────────────────────────────────────────

AS_OF = date(2026, 10, 8)   # a Thursday
QUOTE = {"market_cap": 5_000_000_000, "listed_shares": 1_000_000, "halted": False, "admin_issue": False}


def trading_rows(closes: list, *, last_day: date = AS_OF, values: Optional[list] = None) -> list[dict]:
    """KIS daily rows (oldest first) for these closes on consecutive weekdays ending at ``last_day``."""
    days, day = [], last_day
    while len(days) < len(closes):
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    days.reverse()
    return [{"date": day, "close": close, "volume": 1000,
             "trading_value": 1_000_000 if values is None else values[i]}
            for i, (day, close) in enumerate(zip(days, closes))]


class FakeKis:
    """The KIS client of one run: ``daily`` rows and ``quotes`` per code, or an exception from
    ``daily_errors`` / ``quote_errors``. Every call is kept in ``calls``; ``during(call)`` runs
    inside each call (to interrupt a run). ``closed`` after ``close()``."""

    def __init__(self) -> None:
        self.daily: dict[str, list[dict]] = {}
        self.quotes: dict[str, dict] = {}
        self.daily_errors: dict[str, BaseException] = {}
        self.quote_errors: dict[str, BaseException] = {}
        self.calls: list[tuple] = []
        self.during: Optional[Callable[[tuple], None]] = None
        self.closed = False

    def add(self, code: str, closes: list, *, last_day: date = AS_OF, values: Optional[list] = None,
            **quote: Any) -> None:
        self.daily[code] = trading_rows(closes, last_day=last_day, values=values)
        self.quotes[code] = QUOTE | quote

    def daily_prices(self, code: str, start: date, end: date) -> list[dict]:
        self._called(("daily_prices", code, start, end))
        if code in self.daily_errors:
            raise self.daily_errors[code]
        return [dict(row) for row in self.daily.get(code, []) if start <= row["date"] <= end]

    def quote(self, code: str) -> dict:
        self._called(("quote", code))
        if code in self.quote_errors:
            raise self.quote_errors[code]
        return dict(self.quotes.get(code, QUOTE))

    def _called(self, call: tuple) -> None:
        assert not self.closed, "the KIS client was used after close()"
        self.calls.append(call)
        if self.during is not None:
            self.during(call)

    def codes(self, method: str) -> list[str]:
        return [call[1] for call in self.calls if call[0] == method]

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> "FakeKis":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
