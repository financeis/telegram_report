"""In-memory stand-ins for the peers tests: Supabase REST, a MongoDB collection and an AI client.

``FakeSupabase`` serves the calls ``store.PeersStore`` makes on the five peers tables of migration
008 and the two match functions:

- ``table(name)``: select (``count='exact'``, ``head``), insert, upsert (``on_conflict``), update,
  delete; filters eq / neq / in_ / is_('null') / gt / gte / lt / lte and ``ov`` (array overlap:
  a PostgreSQL array literal such as ``{"a","b,c"}``, parsed like PostgreSQL does, or a list);
  order, range, limit. Like postgrest-py 2.29, ``range()`` adds an offset/limit pair to the query
  it is called on instead of replacing the one it has; the fake answers with the first pair (the
  worst case for a reused query: it gets its first page again and again). A query sent more than
  ``MAX_SENDS`` times fails the test instead of looping forever.
- the tables behave like the migration: primary keys (a duplicate insert fails), NOT NULL columns,
  CHECK value sets (checked only when a value is given), column defaults, the ``build_id``
  serial, foreign keys (a child row needs its parent) with ON DELETE CASCADE, and 1536-dimension
  vectors. NOT NULL is checked on the candidate row before an upsert resolves a conflict, as
  PostgreSQL does. A bulk write without a value for a column some other row has gets NULL there
  (supabase-py's ``default_to_null``); a column no row has gets its default.
- vectors are written as lists (or ``"[…]"`` text) and come back from ``select`` as text, like
  PostgREST returns pgvector values.
- an update or delete without a filter fails the test (it would touch the whole table).
- ``rpc('match_company_profiles' | 'match_company_segments', params)``: the SQL functions of
  migration 008 (only ``status='ok'`` profiles, similarity = cosine, order by distance then code
  (then segment number), ``p_limit`` capped to 0..200, null → 200).

Every executed operation is kept in ``FakeSupabase.log`` (``Op``: kind, table, filters, payload,
window, columns, on_conflict, limit, ranges).

``FakeCollection`` stands in for a pymongo Collection: ``find`` (equality and ``$in``, inclusion
projection), ``distinct``, ``count_documents`` and ``database.client.close()``.

``FakeLLM`` stands in for ``core.llm.LLMClient``: ``parse`` answers through a ``reply(model, user)``
function (a CompanyProfile, a StructuredResult, or an exception to raise), ``embed`` answers with
one vector per text (deterministic, not unit length, so normalizing is visible). It records the
calls, the most ``parse`` calls in flight at once (``max_active``), the most ``embed`` calls in
flight at once (``embed_max_active``) and how often it was closed (``closes``).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import threading
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import cmp_to_key
from types import SimpleNamespace
from typing import Any, Callable, Optional

import numpy as np

from research_desk.core.llm import EmbeddingResult, StructuredResult

DIMS = 1536
NOW = object()   # default marker: the fake's current time as ISO text
MAX_SENDS = 10   # sends of one query object before the fake calls it a read that never ends


class FakeAPIError(Exception):
    """What PostgREST would answer with an error (constraint violation, bad request)."""


@dataclass(frozen=True)
class TableSpec:
    columns: tuple[str, ...]
    key: tuple[str, ...]
    not_null: tuple[str, ...] = ()
    defaults: dict = field(default_factory=dict)
    checks: dict = field(default_factory=dict)
    parent: Optional[tuple[str, tuple[str, ...]]] = None   # (parent table, columns = parent key)
    vector: Optional[str] = None
    serial: Optional[str] = None


_PROFILE_KEY = ("fiscal_year", "profile_version", "stock_code")
_SEGMENT_KEY = _PROFILE_KEY + ("seg_no",)

# Columns as migration 008 creates them.
TABLES: dict[str, TableSpec] = {
    "company_profiles": TableSpec(
        columns=_PROFILE_KEY + (
            "corp_code", "corp_name", "rcept_no", "report_name", "fiscal_end", "parser_version",
            "status", "fail_reason", "source_sections", "source_chars", "input_truncated", "profile",
            "one_line", "is_holding", "is_financial", "info_quality", "terms", "llm_model",
            "input_tokens", "output_tokens", "grounding_ratio", "created_at"),
        key=_PROFILE_KEY,
        not_null=_PROFILE_KEY + ("created_at",),
        defaults={"created_at": NOW},
        checks={"status": {"ok", "failed"}, "info_quality": {"충분", "부족"}},
    ),
    "company_segments": TableSpec(
        columns=_SEGMENT_KEY + ("name", "products", "keywords", "revenue_share_pct"),
        key=_SEGMENT_KEY,
        not_null=_SEGMENT_KEY + ("name", "products", "keywords", "revenue_share_pct"),
        defaults={"products": [], "keywords": [], "revenue_share_pct": -1},
        parent=("company_profiles", _PROFILE_KEY),
    ),
    "company_embeddings": TableSpec(
        columns=_PROFILE_KEY + ("embed_model", "embedding", "created_at"),
        key=_PROFILE_KEY + ("embed_model",),
        not_null=_PROFILE_KEY + ("embed_model", "embedding", "created_at"),
        defaults={"created_at": NOW},
        parent=("company_profiles", _PROFILE_KEY),
        vector="embedding",
    ),
    "segment_embeddings": TableSpec(
        columns=_SEGMENT_KEY + ("embed_model", "embedding", "created_at"),
        key=_SEGMENT_KEY + ("embed_model",),
        not_null=_SEGMENT_KEY + ("embed_model", "embedding", "created_at"),
        defaults={"created_at": NOW},
        parent=("company_segments", _SEGMENT_KEY),
        vector="embedding",
    ),
    "peer_builds": TableSpec(
        columns=("build_id", "fiscal_year", "profile_version", "embed_model", "embed_dims",
                 "parser_version", "synonyms_version", "stock_list_version", "status", "eligible",
                 "profiled", "failed", "company_quantiles", "segment_quantiles", "term_table",
                 "started_at", "heartbeat_at", "finished_at", "message"),
        key=("build_id",),
        not_null=("build_id", "fiscal_year", "profile_version", "embed_model", "embed_dims",
                  "status", "started_at", "heartbeat_at"),
        defaults={"embed_dims": DIMS, "status": "running", "started_at": NOW, "heartbeat_at": NOW},
        checks={"status": {"running", "done", "incomplete", "failed", "pilot"}, "embed_dims": {DIMS}},
        serial="build_id",
    ),
}


def vector_text(vector) -> str:
    """A vector as PostgREST returns a pgvector value: ``[0.1,0.2,…]``."""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def _as_vector(value) -> list[float]:
    if isinstance(value, str):
        value = json.loads(value)
    vector = [float(x) for x in value]
    if len(vector) != DIMS:
        raise FakeAPIError(f"expected {DIMS} dimensions, not {len(vector)}")
    return vector


@dataclass
class Op:
    kind: str                     # select / insert / upsert / update / delete / rpc
    table: str
    filters: list = field(default_factory=list)
    payload: Any = None
    window: Optional[tuple[int, int]] = None      # the (start, end) the answer followed: the first sent
    columns: Optional[str] = None
    on_conflict: str = ""
    limit: Optional[int] = None
    ranges: tuple = ()            # every (start, end) the request carried, in order


def parse_array_literal(text: str) -> list[Optional[str]]:
    """The elements of a one-dimensional PostgreSQL array literal (``{a,"b,c","d\\"e"}``):
    a double-quoted element keeps commas, braces and spaces and takes backslash escapes; an
    unquoted one ends at the next comma and is trimmed (``NULL`` is a null). A malformed literal
    fails like PostgreSQL would."""
    text = text.strip()
    if not (text.startswith("{") and text.endswith("}")):
        raise FakeAPIError(f"malformed array literal: {text!r}")
    body, items, i = text[1:-1], [], 0
    if not body.strip():
        return []
    while True:
        while i < len(body) and body[i] == " ":
            i += 1
        if i < len(body) and body[i] == '"':
            i += 1
            value = []
            while True:
                if i >= len(body):
                    raise FakeAPIError(f"unterminated quoted element: {text!r}")
                if body[i] == "\\":
                    if i + 1 >= len(body):
                        raise FakeAPIError(f"unterminated escape: {text!r}")
                    value.append(body[i + 1])
                    i += 2
                elif body[i] == '"':
                    i += 1
                    break
                else:
                    value.append(body[i])
                    i += 1
            items.append("".join(value))
            while i < len(body) and body[i] == " ":
                i += 1
        else:
            end = body.find(",", i)
            end = len(body) if end == -1 else end
            raw = body[i:end].strip()
            if not raw or any(c in raw for c in '{}"\\'):
                raise FakeAPIError(f"malformed array literal: {text!r}")
            items.append(None if raw.upper() == "NULL" else raw)
            i = end
        if i >= len(body):
            return items
        if body[i] != ",":
            raise FakeAPIError(f"malformed array literal: {text!r}")
        i += 1


class FakeQuery:
    """One chained request on one table."""

    def __init__(self, db: "FakeSupabase", table: str) -> None:
        if table not in TABLES:
            raise AssertionError(f"the peers store has no business with table {table!r}")
        self.db, self.table, self.spec = db, table, TABLES[table]
        self.kind: Optional[str] = None
        self.columns: Optional[str] = None
        self.count: Optional[str] = None
        self.head = False
        self.payload: Any = None
        self.on_conflict = ""
        self.returning = "representation"
        self.filters: list[tuple[str, str, Any]] = []
        self.orders: list[tuple[str, bool, Optional[bool]]] = []
        self.window: Optional[tuple[int, int]] = None
        self.ranges: list[tuple[int, int]] = []
        self.limit_size: Optional[int] = None
        self.sends = 0

    # ── request kinds ────────────────────────────────────────────────────────

    def _kind(self, kind: str) -> "FakeQuery":
        assert self.kind is None, f"{kind} after {self.kind}"
        self.kind = kind
        return self

    def select(self, *columns: str, count: Optional[str] = None, head: Optional[bool] = None):
        assert count in (None, "exact")
        self.columns = ",".join(columns) if columns else "*"
        self.count, self.head = count, bool(head)
        return self._kind("select")

    def insert(self, json_rows, *, returning: str = "representation", count=None,
               upsert: bool = False, default_to_null: bool = True):
        self.payload, self.returning = deepcopy(json_rows), str(returning)
        return self._kind("insert")

    def upsert(self, json_rows, *, on_conflict: str = "", returning: str = "representation",
               count=None, ignore_duplicates: bool = False, default_to_null: bool = True):
        assert not ignore_duplicates
        self.payload, self.returning, self.on_conflict = deepcopy(json_rows), str(returning), on_conflict
        return self._kind("upsert")

    def update(self, json_row, *, returning: str = "representation", count=None):
        self.payload, self.returning = deepcopy(json_row), str(returning)
        return self._kind("update")

    def delete(self, *, returning: str = "representation", count=None):
        self.returning = str(returning)
        return self._kind("delete")

    # ── filters and windows ──────────────────────────────────────────────────

    def _known(self, column: str) -> str:
        if column not in self.spec.columns:
            raise FakeAPIError(f"Could not find the {column!r} column of {self.table!r}")
        return column

    def _filter(self, op: str, column: str, value: Any) -> "FakeQuery":
        self.filters.append((op, self._known(column), value))
        return self

    def eq(self, column, value):
        return self._filter("eq", column, value)

    def neq(self, column, value):
        return self._filter("neq", column, value)

    def in_(self, column, values):
        return self._filter("in", column, list(values))

    def is_(self, column, value):
        assert value == "null", "only is_(column, 'null') is supported"
        return self._filter("is", column, None)

    def gt(self, column, value):
        return self._filter("gt", column, value)

    def gte(self, column, value):
        return self._filter("gte", column, value)

    def lt(self, column, value):
        return self._filter("lt", column, value)

    def lte(self, column, value):
        return self._filter("lte", column, value)

    def ov(self, column, value):
        values = parse_array_literal(value) if isinstance(value, str) else list(value)
        return self._filter("ov", column, values)

    overlaps = ov

    def order(self, column: str, *, desc: bool = False, nullsfirst: Optional[bool] = None):
        self.orders.append((self._known(column), desc, nullsfirst))
        return self

    def range(self, start: int, end: int):
        # postgrest-py 2.29 adds offset/limit to the request on every call; the answer follows
        # the first pair.
        self.ranges.append((start, end))
        if self.window is None:
            self.window = (start, end)
        return self

    def limit(self, size: int):
        self.limit_size = size
        return self

    # ── execution ────────────────────────────────────────────────────────────

    def _passes(self, row: dict) -> bool:
        for op, column, value in self.filters:
            cell = row.get(column)
            if op == "is":
                ok = cell is None
            elif cell is None:
                ok = False   # NULL never passes a comparison, as in SQL
            elif op == "eq":
                ok = cell == value
            elif op == "neq":
                ok = cell != value
            elif op == "in":
                ok = cell in value
            elif op == "ov":
                ok = bool(set(cell) & set(value))
            elif op == "gt":
                ok = cell > value
            elif op == "gte":
                ok = cell >= value
            elif op == "lt":
                ok = cell < value
            else:
                ok = cell <= value
            if not ok:
                return False
        return True

    def _sorted(self, rows: list[dict]) -> list[dict]:
        def compare(a: dict, b: dict) -> int:
            for column, desc, nullsfirst in self.orders:
                x, y = a.get(column), b.get(column)
                if x is None and y is None:
                    continue
                if x is None or y is None:
                    nulls_first = desc if nullsfirst is None else nullsfirst
                    return (-1 if x is None else 1) * (1 if nulls_first else -1)
                if x != y:
                    return (1 if x > y else -1) * (-1 if desc else 1)
            return 0
        return sorted(rows, key=cmp_to_key(compare))

    def _shape(self, row: dict) -> dict:
        out = {}
        names = (list(self.spec.columns) if self.columns is None or self.columns.strip() == "*"
                 else [self._known(name.strip()) for name in self.columns.split(",")])
        for name in names:
            value = deepcopy(row.get(name))
            if name == self.spec.vector and value is not None:
                value = vector_text(value)
            out[name] = value
        return out

    def execute(self) -> SimpleNamespace:
        assert self.kind is not None, "no request kind (select/insert/upsert/update/delete)"
        self.sends += 1
        assert self.sends <= MAX_SENDS, (
            f"one {self.kind} on {self.table} sent {self.sends} times: a paged read that never ends? "
            f"postgrest-py's range() adds offset/limit to a query instead of replacing them "
            f"(offset/limit pairs sent: {self.ranges[:3]}…)")
        with self.db.lock:
            self.db.log.append(Op(self.kind, self.table, list(self.filters), deepcopy(self.payload),
                                  self.window, self.columns, self.on_conflict, self.limit_size,
                                  tuple(self.ranges)))
            return getattr(self, f"_run_{self.kind}")()

    def _run_select(self) -> SimpleNamespace:
        matched = [row for row in self.db.tables[self.table] if self._passes(row)]
        count = len(matched) if self.count == "exact" else None
        data = [self._shape(row) for row in self._sorted(matched)]
        if self.window is not None:
            start, end = self.window
            data = data[start:end + 1]
        if self.limit_size is not None:
            data = data[:self.limit_size]
        return SimpleNamespace(data=[] if self.head else data, count=count)

    def _rows_in(self) -> list[dict]:
        rows = self.payload if isinstance(self.payload, list) else [self.payload]
        union = {self._known(name) for row in rows for name in row}
        out = []
        for row in rows:
            candidate = {name: None for name in self.spec.columns}
            candidate.update({name: row.get(name) for name in union})     # default_to_null
            for name, default in self.spec.defaults.items():
                if name not in union:
                    candidate[name] = self.db.now_text() if default is NOW else deepcopy(default)
            out.append(candidate)
        return out

    def _check(self, row: dict) -> dict:
        for name in self.spec.not_null:
            if row.get(name) is None:
                raise FakeAPIError(f"null value in column {name!r} of {self.table} violates not-null")
        for name, allowed in self.spec.checks.items():
            if row.get(name) is not None and row[name] not in allowed:
                raise FakeAPIError(f"{self.table}.{name} = {row[name]!r} violates its check")
        if self.spec.vector and row.get(self.spec.vector) is not None:
            row[self.spec.vector] = _as_vector(row[self.spec.vector])
        return row

    def _parent_exists(self, row: dict) -> bool:
        if self.spec.parent is None:
            return True
        parent, columns = self.spec.parent
        return any(all(p.get(c) == row.get(c) for c in columns) for p in self.db.tables[parent])

    def _key(self, row: dict, columns: tuple[str, ...]) -> tuple:
        return tuple(row.get(c) for c in columns)

    def _add(self, row: dict) -> dict:
        if self.spec.serial and row.get(self.spec.serial) is None:
            self.db.serial += 1
            row[self.spec.serial] = self.db.serial
        self._check(row)
        if not self._parent_exists(row):
            raise FakeAPIError(f"insert into {self.table} violates its foreign key")
        key = self._key(row, self.spec.key)
        if any(self._key(r, self.spec.key) == key for r in self.db.tables[self.table]):
            raise FakeAPIError(f"duplicate key {key} in {self.table}")
        self.db.tables[self.table].append(row)
        return row

    def _answer(self, rows: list[dict]) -> SimpleNamespace:
        if self.returning == "minimal":
            return SimpleNamespace(data=[], count=None)
        return SimpleNamespace(data=[self._shape(r) for r in rows], count=None)

    def _run_insert(self) -> SimpleNamespace:
        return self._answer([self._add(row) for row in self._rows_in()])

    def _run_upsert(self) -> SimpleNamespace:
        conflict = tuple(c.strip() for c in self.on_conflict.split(",")) if self.on_conflict else self.spec.key
        given = {name for row in (self.payload if isinstance(self.payload, list) else [self.payload])
                 for name in row}
        written = []
        for row in self._rows_in():
            self._check(dict(row))   # NOT NULL fires on the candidate row before the conflict
            key = self._key(row, conflict)
            existing = next((r for r in self.db.tables[self.table] if self._key(r, conflict) == key), None)
            if existing is None:
                written.append(self._add(row))
            else:
                for name in given:
                    existing[name] = row[name]
                self._check(existing)
                written.append(existing)
        return self._answer(written)

    def _matched(self) -> list[dict]:
        assert self.filters, f"{self.kind} without a filter would touch all of {self.table}"
        return [row for row in self.db.tables[self.table] if self._passes(row)]

    def _run_update(self) -> SimpleNamespace:
        for name in self.payload:
            self._known(name)
        rows = self._matched()
        for row in rows:
            row.update(deepcopy(self.payload))
            self._check(row)
        return self._answer(rows)

    def _run_delete(self) -> SimpleNamespace:
        rows = self._matched()
        for row in rows:
            self.db.remove(self.table, row)
        return self._answer(rows)


class FakeRPC:
    def __init__(self, db: "FakeSupabase", name: str, params: dict) -> None:
        self.db, self.name, self.params = db, name, dict(params)

    def execute(self) -> SimpleNamespace:
        with self.db.lock:
            self.db.log.append(Op("rpc", self.name, payload=deepcopy(self.params)))
            return SimpleNamespace(data=self.db.match(self.name, self.params), count=None)


class FakeSupabase:
    """``table(name)`` starts a FakeQuery; ``rpc(name, params)`` runs a match function."""

    def __init__(self, now: Optional[Callable[[], datetime]] = None, **tables: list[dict]) -> None:
        self.tables: dict[str, list[dict]] = {name: [] for name in TABLES}
        self.log: list[Op] = []
        self.lock = threading.RLock()
        self.serial = 0
        self._now = now or (lambda: datetime.now(timezone.utc))
        for name, rows in tables.items():
            for row in rows:
                query = FakeQuery(self, name)
                query.payload = deepcopy(row)
                for candidate in query._rows_in():
                    query._add(candidate)

    def now_text(self) -> str:
        return self._now().isoformat()

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self, name)

    def rpc(self, name: str, params: dict, count=None, head: bool = False, get: bool = False) -> FakeRPC:
        return FakeRPC(self, name, params)

    # ── helpers for the fake and the tests ───────────────────────────────────

    def remove(self, table: str, row: dict) -> None:
        """Delete ``row`` from ``table`` and, like ON DELETE CASCADE, every child row."""
        self.tables[table] = [r for r in self.tables[table] if r is not row]
        for child, spec in TABLES.items():
            if spec.parent and spec.parent[0] == table:
                columns = spec.parent[1]
                for orphan in [r for r in self.tables[child]
                               if all(r.get(c) == row.get(c) for c in columns)]:
                    self.remove(child, orphan)

    def rows(self, table: str, **where: Any) -> list[dict]:
        """Rows of ``table`` whose columns equal ``where`` (copies, vectors as lists)."""
        return [deepcopy(r) for r in self.tables[table]
                if all(r.get(k) == v for k, v in where.items())]

    def ops(self, kind: Optional[str] = None, table: Optional[str] = None) -> list[Op]:
        return [op for op in self.log
                if (kind is None or op.kind == kind) and (table is None or op.table == table)]

    def match(self, name: str, params: dict) -> list[dict]:
        if name not in ("match_company_profiles", "match_company_segments"):
            raise FakeAPIError(f"no function {name}")
        query = np.asarray(_as_vector(params["p_query"]))
        limit = params.get("p_limit")
        limit = max(min(200 if limit is None else limit, 200), 0)
        ok = {(p["fiscal_year"], p["profile_version"], p["stock_code"])
              for p in self.tables["company_profiles"] if p.get("status") == "ok"}
        table = "company_embeddings" if name == "match_company_profiles" else "segment_embeddings"
        rows = []
        for e in self.tables[table]:
            if (e["fiscal_year"], e["profile_version"], e["embed_model"]) != (
                    params["p_fiscal_year"], params["p_profile_version"], params["p_embed_model"]):
                continue
            if (e["fiscal_year"], e["profile_version"], e["stock_code"]) not in ok:
                continue
            vector = np.asarray(e["embedding"])
            similarity = float(vector @ query / (np.linalg.norm(vector) * np.linalg.norm(query)))
            row = {"stock_code": e["stock_code"], "similarity": similarity}
            if table == "segment_embeddings":
                row["seg_no"] = e["seg_no"]
            rows.append(row)
        rows.sort(key=lambda r: (1 - r["similarity"], r["stock_code"], r.get("seg_no", 0)))
        return rows[:limit]


# ── MongoDB ──────────────────────────────────────────────────────────────────

class FakeCollection:
    """A pymongo Collection over a list of documents (read-only)."""

    def __init__(self, docs: list[dict]) -> None:
        self.docs = [deepcopy(d) for d in docs]
        self.finds: list[tuple[dict, Optional[dict]]] = []
        self.closed = False
        self.database = SimpleNamespace(client=SimpleNamespace(close=self._close))

    def _close(self) -> None:
        self.closed = True

    @staticmethod
    def _matches(doc: dict, query: Optional[dict]) -> bool:
        for name, wanted in (query or {}).items():
            value = doc.get(name)
            if isinstance(wanted, dict):
                assert set(wanted) <= {"$in"}, f"unsupported operator in {wanted}"
                if value not in wanted["$in"]:
                    return False
            elif value != wanted:
                return False
        return True

    @staticmethod
    def _project(doc: dict, projection: Optional[dict]) -> dict:
        if not projection:
            return deepcopy(doc)
        wanted = [name for name, on in projection.items() if on and name != "_id"]
        return {name: deepcopy(doc[name]) for name in wanted if name in doc}

    def find(self, query: Optional[dict] = None, projection: Optional[dict] = None):
        self.finds.append((deepcopy(query), deepcopy(projection)))
        return [self._project(d, projection) for d in self.docs if self._matches(d, query)]

    def distinct(self, key: str, query: Optional[dict] = None) -> list:
        values = []
        for doc in self.docs:
            if self._matches(doc, query) and key in doc and doc[key] not in values:
                values.append(doc[key])
        return values

    def count_documents(self, query: dict) -> int:
        return sum(1 for d in self.docs if self._matches(d, query))


# ── AI client ────────────────────────────────────────────────────────────────

def pseudo_vector(text: str, dims: int = DIMS) -> list[float]:
    """A fixed vector for ``text`` (same text, same vector), three units long."""
    seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")
    vector = np.random.default_rng(seed).normal(size=dims)
    return list(vector / np.linalg.norm(vector) * 3.0)


class FakeLLM:
    def __init__(self, reply: Optional[Callable[[str, str], Any]] = None, *,
                 embed: Optional[Callable[[str], list[float]]] = None, delay: float = 0.0,
                 tokens: tuple[int, int] = (100, 10)) -> None:
        self.reply = reply
        self.embedder = embed or pseudo_vector
        self.delay = delay
        self.tokens = tokens
        self.parse_calls: list[dict] = []
        self.embed_calls: list[dict] = []
        self.active = 0
        self.max_active = 0
        self.embed_active = 0
        self.embed_max_active = 0
        self.closed = False
        self.closes = 0

    async def parse(self, *, model, system, user, schema, temperature=None, constrained=True):
        self.parse_calls.append({"model": model, "system": system, "user": user,
                                 "schema": schema, "constrained": constrained})
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            answer = self.reply(model, user) if self.reply else None
            if isinstance(answer, BaseException):
                raise answer
            if isinstance(answer, StructuredResult):
                return answer
            return StructuredResult(parsed=answer, input_tokens=self.tokens[0],
                                    output_tokens=self.tokens[1])
        finally:
            self.active -= 1

    async def embed(self, *, model, texts, dimensions=None):
        self.embed_calls.append({"model": model, "texts": list(texts), "dimensions": dimensions})
        self.embed_active += 1
        self.embed_max_active = max(self.embed_max_active, self.embed_active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            return EmbeddingResult(vectors=[list(self.embedder(t)) for t in texts],
                                   input_tokens=len(texts) * 7)
        finally:
            self.embed_active -= 1

    async def close(self) -> None:
        self.closed = True
        self.closes += 1


def unit(vector) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector]
