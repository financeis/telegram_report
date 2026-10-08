"""Collector settings, read with the core.settings helpers when ``collect`` starts.

``load_config()`` first re-reads ``.env`` (``core.settings.load_env``: variables
already set win), then reads the process environment. Nothing is read at
import time.

- Required: TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_CHANNEL, SUPABASE_URL,
  SUPABASE_SERVICE_KEY. Unset or empty → ``core.settings.MissingSetting``; the
  command prints ``Config error: Missing required env var: <NAME>`` and exits 1.
- Optional: TELEGRAM_CHANNEL_ID (numeric id for Telegram fetches; DB rows keep
  the TELEGRAM_CHANNEL label), TELEGRAM_SESSION_NAME (samstudy → session file
  ``sessions/<name>``, relative to the current folder), STORAGE_BASE_DIR
  (./reports), INITIAL_CUTOFF_DAYS (30), MAX_CONCURRENT_DOWNLOADS (4),
  LOG_LEVEL (INFO).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from research_desk.core import settings as core_settings

SESSIONS_DIR = Path('sessions')
DEFAULT_SESSION_NAME = 'samstudy'
DEFAULT_INITIAL_CUTOFF_DAYS = 30
DEFAULT_MAX_CONCURRENT_DOWNLOADS = 4
DEFAULT_LOG_LEVEL = 'INFO'


@dataclass(frozen=True)
class Config:
    telegram_api_id: int
    telegram_api_hash: str
    telegram_channel: str
    telegram_session_path: Path
    supabase_url: str
    supabase_service_key: str
    storage_base_dir: Path
    initial_cutoff_days: int
    max_concurrent_downloads: int
    log_level: str
    telegram_channel_id: int | None = None

    def channel_ref(self) -> int | str:
        """Identifier for telethon fetch calls.

        Returns the numeric channel id if set (required for private channels
        with no public username), else falls back to the username string for
        backward compatibility.
        """
        if self.telegram_channel_id is not None:
            return self.telegram_channel_id
        return self.telegram_channel


def load_config() -> Config:
    """Load env vars from .env (if present) and process environment.

    Raises core.settings.MissingSetting (``.name`` = the variable) if any
    required key is missing or empty, ValueError if a number is malformed.
    """
    core_settings.load_env()

    session_name = core_settings.optional('TELEGRAM_SESSION_NAME', DEFAULT_SESSION_NAME)

    return Config(
        telegram_api_id=int(core_settings.required('TELEGRAM_API_ID')),
        telegram_api_hash=core_settings.required('TELEGRAM_API_HASH'),
        telegram_channel=core_settings.required('TELEGRAM_CHANNEL'),
        telegram_session_path=SESSIONS_DIR / session_name,
        supabase_url=core_settings.required('SUPABASE_URL'),
        supabase_service_key=core_settings.required('SUPABASE_SERVICE_KEY'),
        storage_base_dir=core_settings.storage_base_dir(),
        initial_cutoff_days=core_settings.get_int('INITIAL_CUTOFF_DAYS', DEFAULT_INITIAL_CUTOFF_DAYS),
        max_concurrent_downloads=core_settings.get_int(
            'MAX_CONCURRENT_DOWNLOADS', DEFAULT_MAX_CONCURRENT_DOWNLOADS),
        log_level=core_settings.optional('LOG_LEVEL', DEFAULT_LOG_LEVEL),
        telegram_channel_id=_optional_int('TELEGRAM_CHANNEL_ID'),
    )


def _optional_int(name: str) -> int | None:
    """``int`` of ``name``; None when it is unset or empty."""
    value = core_settings.optional(name)
    if value is None or value == '':
        return None
    return int(value)
