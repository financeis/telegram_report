"""In-memory stand-in for the supabase-py query builder, for the reports tests.

Serves the read calls the reports feature makes on ``reports`` and the analysis window makes on
``report_summaries``: select, eq, in_, is_('null'), gte, contains, range, execute. Filters
follow PostgREST: a NULL value never passes eq / in_ / gte / contains, and ``is_(col, 'null')``
passes only NULL. ``select`` returns only the listed columns (``'*'`` returns all of them).

Like the real builder, ``range()`` changes the query it is called on and returns it, so a paged
read executes one query object several times; the latest window wins. Each execution is kept in
``FakeSupabase.executed`` as a snapshot (table, columns, filters and window at that moment).

Read-only on purpose: there is no insert / upsert / update / delete, so any write fails the test.
"""
from __future__ import annotations

from copy import copy, deepcopy
from types import SimpleNamespace
from typing import Any, Optional


class FakeQuery:
    """One chained query on one table."""

    def __init__(self, db: "FakeSupabase", table: str) -> None:
        self.db = db
        self.table = table
        self.columns: Optional[str] = None
        self.filters: list[tuple[str, str, Any]] = []   # (operator, column, value) in call order
        self.window: Optional[tuple[int, int]] = None   # range(start, end), both inclusive

    def select(self, columns: str = "*") -> "FakeQuery":
        self.columns = columns
        return self

    def eq(self, column: str, value: Any) -> "FakeQuery":
        self.filters.append(("eq", column, value))
        return self

    def in_(self, column: str, values) -> "FakeQuery":
        self.filters.append(("in", column, list(values)))
        return self

    def is_(self, column: str, value: str) -> "FakeQuery":
        assert value == "null", f"only is_(col, 'null') is supported, got {value!r}"
        self.filters.append(("is", column, None))
        return self

    def gte(self, column: str, value: Any) -> "FakeQuery":
        self.filters.append(("gte", column, value))
        return self

    def contains(self, column: str, values) -> "FakeQuery":
        self.filters.append(("contains", column, list(values)))
        return self

    def range(self, start: int, end: int) -> "FakeQuery":
        self.window = (start, end)
        return self

    def filter_values(self, operator: str, column: str) -> list[Any]:
        """Values given to ``operator`` on ``column``, in call order."""
        return [value for op, col, value in self.filters if (op, col) == (operator, column)]

    def _passes(self, row: dict) -> bool:
        for operator, column, value in self.filters:
            cell = row.get(column)
            if operator == "is":
                ok = cell is None
            elif cell is None:
                ok = False
            elif operator == "eq":
                ok = cell == value
            elif operator == "in":
                ok = cell in value
            elif operator == "gte":
                ok = cell >= value
            else:  # contains
                ok = set(value) <= set(cell)
            if not ok:
                return False
        return True

    def _shape(self, row: dict) -> dict:
        if self.columns is None or self.columns.strip() == "*":
            return deepcopy(row)
        return {name.strip(): deepcopy(row.get(name.strip())) for name in self.columns.split(",")}

    def execute(self) -> SimpleNamespace:
        assert self.columns is not None, "a read query must call select() first"
        snapshot = copy(self)
        snapshot.filters = list(self.filters)
        self.db.executed.append(snapshot)
        data = [self._shape(row) for row in self.db.tables.get(self.table, []) if self._passes(row)]
        if self.window is not None:
            start, end = self.window
            data = data[start:end + 1]
        return SimpleNamespace(data=data)


class FakeSupabase:
    """``table(name)`` starts a FakeQuery over ``tables[name]`` (a list of row dicts)."""

    def __init__(self, **tables: list[dict]) -> None:
        self.tables: dict[str, list[dict]] = {name: list(rows) for name, rows in tables.items()}
        self.executed: list[FakeQuery] = []

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self, name)

    def queries(self, table: str) -> list[FakeQuery]:
        """Executed queries on ``table``, in order."""
        return [q for q in self.executed if q.table == table]
