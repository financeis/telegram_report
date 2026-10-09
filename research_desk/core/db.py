"""DB connections: Supabase REST client (service key) and a direct Postgres pool.

``SupabaseSQL`` is a thin fetch/execute adapter over the pool that returns rows
as plain dicts. The SQL itself lives with the area that owns the table.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

import asyncpg
from supabase import Client, create_client

from research_desk.core import settings


def supabase_client(url: str, service_key: str) -> Client:
    """Supabase REST client authenticated with the service key."""
    return create_client(url, service_key)


async def create_pool(url: Optional[str] = None, *, max_size: int) -> asyncpg.Pool:
    """asyncpg pool for ``url`` (default SUPABASE_DB_URL; MissingSetting if unset).

    statement_cache_size=0 is required when the URL points at Supabase's
    transaction pooler (port 6543): pgbouncer in transaction mode does not
    keep named prepared statements across pooled connections. Direct
    connections (port 5432) tolerate caching, so disabling it is harmless.
    """
    url = url or settings.supabase_db_url()
    if not url:
        raise settings.MissingSetting("SUPABASE_DB_URL")
    return await asyncpg.create_pool(url, min_size=1, max_size=max_size,
                                     statement_cache_size=0)


class SupabaseSQL:
    """fetch/execute over an asyncpg pool; rows come back as dicts."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @classmethod
    async def from_env(cls, *, max_size: int) -> "SupabaseSQL":
        """Adapter on a new pool for SUPABASE_DB_URL (MissingSetting if unset)."""
        return cls(await create_pool(max_size=max_size))

    async def close(self) -> None:
        await self._pool.close()

    async def fetch(self, sql: str, args: Iterable[Any] = ()) -> list[dict]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
        return [dict(r) for r in rows]

    async def execute(self, sql: str, args: Iterable[Any] = ()) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(sql, *args)
