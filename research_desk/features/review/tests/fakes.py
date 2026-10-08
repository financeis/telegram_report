"""In-memory stand-in for the supabase-py query builder on the reports table, for the review tests.

Serves the calls review makes: select (``count='exact'`` too), eq, in_, not_ (negates the next
filter), is_('null'), order, limit, update, execute. Filters follow PostgREST: a NULL value never
passes eq / in_ nor their negations, and ``is_(col, 'null')`` passes only NULL. ``order`` puts NULL
last when ascending, like PostgreSQL. ``count='exact'`` counts every matching row, whatever the
limit. ``update`` writes to every matching row and returns the rows written (supabase-py's default,
``return=representation``); reads return copies.

Each execution is kept in ``FakeSupabase.executed`` (a snapshot of the query at that moment).
``FakeSupabase.before_write``: one-shot callables run inside the next update, just before it is
applied. They stand in for another writer (a tagger worker, escalate, a second review) changing the
row between review's read and its write.

``MemoryDB`` / ``MemoryQuery`` are the old langgraph_tagger/workspace/tests/test_migration.py
stand-ins, copied unchanged for the tests ported from there. ``waiting(rid)`` builds a full
``review_needed`` row.
"""
from __future__ import annotations

from copy import copy, deepcopy
from types import SimpleNamespace
from typing import Any, Callable, Optional

TABLE = 'reports'

# The DB settings the tests give (never a real project).
URL, KEY = 'https://example.supabase.test', 'service-key'


class FakeQuery:
    """One chained query on the reports table."""

    def __init__(self, db: 'FakeSupabase', table: str) -> None:
        assert table == TABLE, f'review only reads and writes the reports table, got {table!r}'
        self.db = db
        self.kind: Optional[str] = None                     # 'select' or 'update'
        self.columns: tuple[str, ...] = ()
        self.count: Optional[str] = None
        self.values: Optional[dict] = None
        self.filters: list[tuple[str, str, Any, bool]] = []  # (operator, column, value, negated)
        self.ordering: Optional[tuple[str, bool]] = None     # (column, desc)
        self.limit_n: Optional[int] = None
        self._negate_next = False

    # ── building ─────────────────────────────────────────────────────────────

    def select(self, *columns: str, count: Optional[str] = None) -> 'FakeQuery':
        self.kind, self.columns, self.count = 'select', columns or ('*',), count
        return self

    def update(self, values: dict) -> 'FakeQuery':
        self.kind, self.values = 'update', deepcopy(dict(values))
        return self

    @property
    def not_(self) -> 'FakeQuery':
        self._negate_next = True
        return self

    def _filter(self, operator: str, column: str, value: Any) -> 'FakeQuery':
        self.filters.append((operator, column, value, self._negate_next))
        self._negate_next = False
        return self

    def eq(self, column: str, value: Any) -> 'FakeQuery':
        return self._filter('eq', column, value)

    def in_(self, column: str, values) -> 'FakeQuery':
        return self._filter('in', column, list(values))

    def is_(self, column: str, value: str) -> 'FakeQuery':
        assert value == 'null', f"only is_(col, 'null') is supported, got {value!r}"
        return self._filter('is', column, None)

    def order(self, column: str, desc: bool = False) -> 'FakeQuery':
        self.ordering = (column, desc)
        return self

    def limit(self, n: int) -> 'FakeQuery':
        self.limit_n = n
        return self

    # ── looking at it ────────────────────────────────────────────────────────

    def filter_list(self) -> list[tuple]:
        """The filters in call order: (operator, column, value), 'not.' before negated ones."""
        return [(('not.' if negated else '') + op, column, value)
                for op, column, value, negated in self.filters]

    def filter_value(self, operator: str, column: str) -> Any:
        values = [value for op, col, value, _ in self.filters if (op, col) == (operator, column)]
        assert len(values) == 1, (operator, column, values)
        return values[0]

    # ── running it ───────────────────────────────────────────────────────────

    def _passes(self, row: dict) -> bool:
        for operator, column, value, negated in self.filters:
            cell = row.get(column)
            if operator == 'is':
                ok = cell is None
            elif cell is None:
                return False   # NULL compared is unknown: the row is left out, negated or not
            elif operator == 'eq':
                ok = cell == value
            else:  # in
                ok = cell in value
            if ok == negated:
                return False
        return True

    def _shape(self, row: dict) -> dict:
        if self.columns == ('*',):
            return deepcopy(row)
        return {name: deepcopy(row.get(name)) for name in self.columns}

    def _ordered(self, rows: list[dict]) -> list[dict]:
        if self.ordering is None:
            return rows
        column, desc = self.ordering
        present = sorted((r for r in rows if r.get(column) is not None),
                         key=lambda r: r[column], reverse=desc)
        missing = [r for r in rows if r.get(column) is None]
        return missing + present if desc else present + missing

    def execute(self) -> SimpleNamespace:
        assert self.kind is not None, 'a query must call select() or update() first'
        snapshot = copy(self)
        snapshot.filters = list(self.filters)
        self.db.executed.append(snapshot)
        if self.kind == 'update':
            hooks, self.db.before_write = self.db.before_write, []
            for hook in hooks:
                hook()
            written = []
            for row in self.db.rows:
                if self._passes(row):
                    row.update(deepcopy(self.values))
                    written.append(deepcopy(row))
            return SimpleNamespace(data=written, count=None)
        matching = self._ordered([row for row in self.db.rows if self._passes(row)])
        total = len(matching)
        if self.limit_n is not None:
            matching = matching[:self.limit_n]
        return SimpleNamespace(data=[self._shape(row) for row in matching],
                               count=total if self.count == 'exact' else None)


class FakeSupabase:
    """``table('reports')`` starts a FakeQuery over ``rows`` (a list of row dicts)."""

    def __init__(self, rows: Optional[list[dict]] = None) -> None:
        self.rows: list[dict] = [deepcopy(r) for r in rows or []]
        self.executed: list[FakeQuery] = []
        self.before_write: list[Callable[[], None]] = []
        self.made: list[tuple[str, str]] = []   # (url, key) of each client handed out

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self, name)

    def row(self, rid: int) -> dict:
        (found,) = [r for r in self.rows if r['id'] == rid]
        return found

    def reads(self) -> list[FakeQuery]:
        return [q for q in self.executed if q.kind == 'select']

    def writes(self) -> list[FakeQuery]:
        return [q for q in self.executed if q.kind == 'update']


class MemoryQuery:
    """The old test_migration.py stand-in, copied unchanged (with MemoryDB below)."""

    def __init__(self, db):
        self.db, self.filters, self.payload = db, {}, None

    def select(self, *_args, **_kwargs): return self
    def eq(self, key, value): self.filters[key] = value; return self
    def is_(self, key, value): self.filters[key] = None if value == 'null' else value; return self
    def update(self, payload): self.payload = payload; return self
    def execute(self):
        data = []
        for row in self.db.rows:
            if all(row.get(k) == v for k, v in self.filters.items()):
                if self.payload is not None:
                    row.update(deepcopy(self.payload))
                data.append(deepcopy(row))
        return SimpleNamespace(data=data, count=len(data))


class MemoryDB:
    def __init__(self):
        self.rows = [{'id': 1, 'tagging_status': 'review_needed', 'tagging_notes': 'low_confidence',
                      'report_type': '단일종목', 'publisher': 'KB', 'stock_codes': ['016360'],
                      'file_path': 'original.pdf', 'caption': 'unchanged', 'published_at': '2026-05-11'}]
    def table(self, _): return MemoryQuery(self)


def waiting(rid: int, tagged_at: str = '2026-05-07T08:30:00+00:00', **extra) -> dict:
    """A ``review_needed`` row as the collector and the tagger leave it (every column)."""
    return {
        # collector columns
        'id': rid,
        'message_id': 124000 + rid,
        'chat_username': 'research_channel',
        'file_path': f'2026/{rid}.pdf',
        'file_name': f'{rid}_report.pdf',
        'file_size_bytes': 12345,
        'file_hash_sha256': 'ab' * 32,
        'caption': 'internal caption',
        'downloaded_at': '2026-05-07T08:04:14+00:00',
        'sent_at': '2026-05-07T08:04:14+00:00',
        # classification
        'published_at': '2026-05-07',
        'report_type': '단일종목',
        'publisher': '삼성증권',
        'publisher_type': 'broker',
        'analysts': ['홍길동'],
        'title': f'report {rid}',
        'stock_codes': ['005930'],
        'company_names': ['삼성전자'],
        'stock_codes_raw': ['005935'],
        'company_names_raw': ['삼성전자우'],
        'sectors_major': ['반도체'],
        'sectors_minor': ['메모리'],
        'products': ['DRAM'],
        'out_of_scope_reason': None,
        # tagging meta
        'tagging_status': 'review_needed',
        'tagging_confidence': 'low',
        'tagging_notes': 'krx_unmatched_in_scope',
        'tagged_at': tagged_at,
        'tagger_version': 'langgraph-tagger@2.0',
        'taxonomy_version': 'KRX@2026-05-08',
        'tagging_locked_at': None,
        'tagging_worker_id': None,
        **extra,
    }
