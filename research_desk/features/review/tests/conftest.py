"""Safety net for every review test, and the shared stand-ins.

- ``clean_env`` (autouse) deletes every environment variable review could read and makes
  ``core.db.supabase_client`` refuse, so no test ever makes a real Supabase client. A test that
  needs a value sets it itself.
- ``invalidations`` (autouse): ``coverage.invalidate`` replaced where review looks it up (the
  coverage window); it counts the calls and can look at the world at each one (``probe``).
- ``db``: the DB settings set and ``core.db.supabase_client`` handing out an in-memory
  ``FakeSupabase`` (see fakes.py), recording each (url, key) it was asked for.
- ``memory``: the same with the old test_migration.py ``MemoryDB``, for the ported tests.
- ``storage``: a temp PDF folder at ``STORAGE_BASE_DIR``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

import pytest

from research_desk.core import db as core_db
from research_desk.features import coverage

from .fakes import KEY, URL, FakeSupabase, MemoryDB

ENV = ('SUPABASE_URL', 'SUPABASE_SERVICE_KEY', 'SUPABASE_DB_URL', 'STORAGE_BASE_DIR', 'KRX_CSV_PATH',
       'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'LLM_MODEL_PHASE2', 'OPENAI_MODEL_PHASE2',
       'TELEGRAM_API_ID', 'TELEGRAM_API_HASH', 'TELEGRAM_CHANNEL', 'TELEGRAM_CHANNEL_ID')


class Invalidations:
    """Stand-in for ``coverage.invalidate``: ``count`` of calls; at each call ``probe()`` (when
    set) runs and its result is kept in ``seen``."""

    def __init__(self) -> None:
        self.count = 0
        self.seen: list[Any] = []
        self.probe: Optional[Callable[[], Any]] = None

    def __call__(self) -> None:
        self.count += 1
        if self.probe is not None:
            self.seen.append(self.probe())


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)

    def refuse(url, key):
        raise AssertionError('no real Supabase client in these tests')

    monkeypatch.setattr(core_db, 'supabase_client', refuse)


@pytest.fixture(autouse=True)
def invalidations(monkeypatch) -> Invalidations:
    fake = Invalidations()
    monkeypatch.setattr(coverage, 'invalidate', fake)
    return fake


@pytest.fixture
def db(monkeypatch) -> FakeSupabase:
    fake = FakeSupabase()

    def supabase_client(url, key):
        fake.made.append((url, key))
        return fake

    monkeypatch.setenv('SUPABASE_URL', URL)
    monkeypatch.setenv('SUPABASE_SERVICE_KEY', KEY)
    monkeypatch.setattr(core_db, 'supabase_client', supabase_client)
    return fake


@pytest.fixture
def memory(monkeypatch) -> MemoryDB:
    """The old test_migration.py MemoryDB, handed out by core.db.supabase_client, with the DB
    settings set (for the ported tests)."""
    memory_db = MemoryDB()
    monkeypatch.setenv('SUPABASE_URL', URL)
    monkeypatch.setenv('SUPABASE_SERVICE_KEY', KEY)
    monkeypatch.setattr(core_db, 'supabase_client', lambda url, key: memory_db)
    return memory_db


@pytest.fixture
def storage(tmp_path, monkeypatch) -> Path:
    folder = tmp_path / 'reports'
    folder.mkdir()
    monkeypatch.setenv('STORAGE_BASE_DIR', str(folder))
    return folder
