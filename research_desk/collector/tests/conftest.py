"""Shared fixtures for the collector tests (the fake classes live in fakes.py)."""
from __future__ import annotations

import pytest
import telethon

from research_desk.collector import cli
from research_desk.collector.tests.fakes import FakeStorage, FakeTelegramClient

# Every variable the collector reads. Tests must not depend on values another
# test (or the real .env) left in the process environment.
COLLECTOR_ENV_VARS = (
    'TELEGRAM_API_ID', 'TELEGRAM_API_HASH', 'TELEGRAM_CHANNEL', 'TELEGRAM_CHANNEL_ID',
    'TELEGRAM_SESSION_NAME', 'SUPABASE_URL', 'SUPABASE_SERVICE_KEY', 'STORAGE_BASE_DIR',
    'INITIAL_CUTOFF_DAYS', 'MAX_CONCURRENT_DOWNLOADS', 'LOG_LEVEL',
)

REQUIRED_TEST_ENV = {
    'TELEGRAM_API_ID': '12345',
    'TELEGRAM_API_HASH': 'abcdef0123456789',
    'TELEGRAM_CHANNEL': 'sunstudy1004',
    'SUPABASE_URL': 'https://test.supabase.co',
    'SUPABASE_SERVICE_KEY': 'eyJtest',
}


@pytest.fixture(autouse=True)
def _no_real_connections(monkeypatch):
    """No collector test may reach Telegram or Supabase.

    The collect command's real client factories, and Telethon's client class,
    fail the test unless the test swaps in a fake.
    """
    def refuse(*args, **kwargs):
        pytest.fail('a collector test tried to open a real Telegram or Supabase connection')

    monkeypatch.setattr(cli, 'TelegramClient', refuse)
    monkeypatch.setattr(cli, 'build_storage', refuse)
    monkeypatch.setattr(telethon, 'TelegramClient', refuse)


@pytest.fixture
def clean_env(monkeypatch):
    """All collector variables unset; returns monkeypatch for further setenv calls."""
    for name in COLLECTOR_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.fixture
def required_env(clean_env) -> dict[str, str]:
    """The five required variables set to test values, every optional one unset."""
    for name, value in REQUIRED_TEST_ENV.items():
        clean_env.setenv(name, value)
    return dict(REQUIRED_TEST_ENV)


@pytest.fixture
def fake_client() -> FakeTelegramClient:
    return FakeTelegramClient()


@pytest.fixture
def fake_storage(tmp_path) -> FakeStorage:
    return FakeStorage(base_dir=tmp_path)
