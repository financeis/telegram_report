"""core.db: Supabase REST client, Postgres pool and the fetch/execute adapter (no real DB)."""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from unittest.mock import AsyncMock, MagicMock

import pytest

from research_desk.core import db
from research_desk.core.settings import MissingSetting


def test_supabase_client_uses_the_service_key(monkeypatch):
    calls = []
    client = object()

    def fake_create_client(url, key):
        calls.append((url, key))
        return client

    monkeypatch.setattr(db, "create_client", fake_create_client)
    assert db.supabase_client("https://x.supabase.co", "service-key") is client
    assert calls == [("https://x.supabase.co", "service-key")]


@pytest.fixture
def fake_create_pool(monkeypatch):
    pool = MagicMock(name="pool")
    create = AsyncMock(return_value=pool)
    monkeypatch.setattr(db.asyncpg, "create_pool", create)
    return create


async def test_pool_settings(fake_create_pool, monkeypatch):
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://user@host:6543/postgres")

    pool = await db.create_pool(max_size=10)

    assert pool is fake_create_pool.return_value
    fake_create_pool.assert_awaited_once_with(
        "postgresql://user@host:6543/postgres",
        min_size=1, max_size=10, statement_cache_size=0,
    )


async def test_pool_explicit_url_wins(fake_create_pool, monkeypatch):
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://from-env")
    await db.create_pool("postgresql://explicit", max_size=3)
    assert fake_create_pool.await_args.args == ("postgresql://explicit",)
    assert fake_create_pool.await_args.kwargs["max_size"] == 3


@pytest.mark.parametrize("value", [None, ""])
async def test_pool_requires_db_url(fake_create_pool, monkeypatch, value):
    if value is None:
        monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    else:
        monkeypatch.setenv("SUPABASE_DB_URL", value)

    with pytest.raises(MissingSetting) as exc:
        await db.create_pool(max_size=10)
    assert str(exc.value) == "SUPABASE_DB_URL is required"
    assert isinstance(exc.value, RuntimeError)
    fake_create_pool.assert_not_awaited()

    with pytest.raises(MissingSetting, match="^SUPABASE_DB_URL is required$"):
        await db.SupabaseSQL.from_env(max_size=10)


class FakeRecord(Mapping):
    """Stands in for asyncpg.Record: a mapping, but not a dict."""

    def __init__(self, **values):
        self._values = values

    def __getitem__(self, key):
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)


class FakeTransaction:
    """Stands in for asyncpg's Transaction: start / commit / rollback land in the conn's calls."""

    def __init__(self, conn):
        self.conn = conn

    async def start(self):
        self.conn.calls.append(("begin",))

    async def commit(self):
        self.conn.calls.append(("commit",))

    async def rollback(self):
        self.conn.calls.append(("rollback",))


class FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return self.rows

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return "UPDATE 1"

    def transaction(self):
        return FakeTransaction(self)


class FakePool:
    def __init__(self, conn):
        self.conn = conn
        self.close = AsyncMock()
        self.acquired = 0
        self.released = 0

    def acquire(self):
        conn = self.conn
        pool = self

        class _Acquire:
            async def __aenter__(self):
                pool.acquired += 1
                return conn

            async def __aexit__(self, *exc):
                pool.released += 1
                return False

        return _Acquire()


async def test_fetch_returns_plain_dicts_and_passes_args_positionally():
    conn = FakeConn([FakeRecord(id=1, title="a"), FakeRecord(id=2, title="b")])
    sql = db.SupabaseSQL(FakePool(conn))

    rows = await sql.fetch("SELECT id, title FROM t WHERE id = ANY($1) AND w = $2", [[1, 2], "w"])

    assert rows == [{"id": 1, "title": "a"}, {"id": 2, "title": "b"}]
    assert all(type(row) is dict for row in rows)
    assert conn.calls == [("fetch", "SELECT id, title FROM t WHERE id = ANY($1) AND w = $2",
                           ([1, 2], "w"))]


async def test_fetch_without_args_and_execute_and_close():
    conn = FakeConn([])
    pool = FakePool(conn)
    sql = db.SupabaseSQL(pool)

    assert await sql.fetch("SELECT 1") == []
    assert await sql.execute("UPDATE t SET x=$1 WHERE id=$2", (5, 7)) is None
    await sql.close()

    assert conn.calls == [("fetch", "SELECT 1", ()),
                          ("execute", "UPDATE t SET x=$1 WHERE id=$2", (5, 7))]
    pool.close.assert_awaited_once()


async def test_from_env_builds_the_adapter_on_a_new_pool(monkeypatch):
    conn = FakeConn([FakeRecord(n=1)])
    create = AsyncMock(return_value=FakePool(conn))
    monkeypatch.setattr(db.asyncpg, "create_pool", create)
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://from-env")

    sql = await db.SupabaseSQL.from_env(max_size=10)

    assert await sql.fetch("SELECT 1 AS n") == [{"n": 1}]
    create.assert_awaited_once_with("postgresql://from-env", min_size=1, max_size=10,
                                    statement_cache_size=0)


# ── one transaction on one connection ────────────────────────────────────────

async def test_transaction_runs_every_statement_on_one_connection_then_commits():
    conn = FakeConn([FakeRecord(id=7)])
    pool = FakePool(conn)
    sql = db.SupabaseSQL(pool)

    async with sql.transaction() as tx:
        rows = await tx.fetch("SELECT id FROM t WHERE id = ANY($1) FOR UPDATE", [[7]])
        assert await tx.execute("UPDATE t SET x=$1 WHERE id=$2", (5, 7)) is None
        assert conn.calls[-1][0] == "execute"   # nothing committed inside the block

    assert rows == [{"id": 7}]
    assert all(type(row) is dict for row in rows)
    assert conn.calls == [
        ("begin",),
        ("fetch", "SELECT id FROM t WHERE id = ANY($1) FOR UPDATE", ([7],)),
        ("execute", "UPDATE t SET x=$1 WHERE id=$2", (5, 7)),
        ("commit",),
    ]
    assert (pool.acquired, pool.released) == (1, 1)


async def test_transaction_rolls_back_and_reraises_when_the_block_raises():
    conn = FakeConn([FakeRecord(id=7)])
    pool = FakePool(conn)
    sql = db.SupabaseSQL(pool)

    class CountMismatch(Exception):
        pass

    with pytest.raises(CountMismatch):
        async with sql.transaction() as tx:
            await tx.execute("UPDATE t SET x=1")
            raise CountMismatch("1 row changed, 2 expected")

    assert conn.calls == [("begin",), ("execute", "UPDATE t SET x=1", ()), ("rollback",)]
    assert (pool.acquired, pool.released) == (1, 1)


async def test_transaction_rolls_back_when_a_statement_fails():
    class FailingConn(FakeConn):
        async def execute(self, sql, *args):
            self.calls.append(("execute", sql, args))
            raise RuntimeError("deadlock detected")

    conn = FailingConn([])
    sql = db.SupabaseSQL(FakePool(conn))

    with pytest.raises(RuntimeError, match="deadlock detected"):
        async with sql.transaction() as tx:
            await tx.execute("UPDATE t SET x=1")

    assert conn.calls[-1] == ("rollback",)
    assert ("commit",) not in conn.calls


async def test_transaction_rolls_back_when_cancelled():
    """Ctrl+C / task cancellation is not an Exception; it must not commit either."""
    conn = FakeConn([])
    sql = db.SupabaseSQL(FakePool(conn))

    with pytest.raises(asyncio.CancelledError):
        async with sql.transaction():
            raise asyncio.CancelledError()

    assert conn.calls == [("begin",), ("rollback",)]


async def test_fetch_and_execute_still_take_their_own_connection():
    conn = FakeConn([])
    pool = FakePool(conn)
    sql = db.SupabaseSQL(pool)

    await sql.fetch("SELECT 1")
    await sql.execute("SELECT 2")

    assert (pool.acquired, pool.released) == (2, 2)
    assert ("begin",) not in conn.calls
