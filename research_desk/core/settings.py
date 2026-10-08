"""Settings: the one way to read configuration (``.env`` + process environment).

- ``load_env()`` re-reads ``.env`` on every call and never overrides a variable
  that is already set. Commands call it when they start; web features call it
  whenever they prepare, analysis whenever it checks the model key. So a key
  *added* to ``.env`` takes effect on the next attempt without a restart, while
  a *changed* value needs a restart (the old value is already set).
- Value helpers read the process environment at call time. Shared values are
  defined here; each area reads its own values in its ``settings.py`` with the
  same helpers.
- Nothing is read at import time.
- ``NotReady(area, reason)``: a feature could not prepare (missing setting,
  file or connection) and is unusable until the cause is fixed.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Union

from dotenv import load_dotenv

StrPath = Union[str, os.PathLike]

# ── .env reading switch ──────────────────────────────────────────────────────
# load_env() checks these on every call. Tests set them with monkeypatch
# (research_desk/conftest.py switches reading off for every test). A switch
# here, rather than swapping the function, also covers modules that did
# ``from research_desk.core.settings import load_env``.
ENV_FILE_ENABLED: bool = True
# When set, load_env() reads this file instead of searching upward.
ENV_FILE_PATH: Optional[Path] = None

ENV_FILE_NAME = ".env"


def find_env_file(start: StrPath, name: str = ENV_FILE_NAME) -> Optional[Path]:
    """The nearest ``name`` file in ``start`` or one of its parent folders."""
    start = Path(start).resolve()
    for folder in (start, *start.parents):
        candidate = folder / name
        if candidate.is_file():
            return candidate
    return None


def load_env(path: Optional[StrPath] = None) -> Optional[Path]:
    """Load ``.env`` into the process environment; existing variables win.

    Reads ``path`` if given, else ``ENV_FILE_PATH`` if set, else the nearest
    ``.env`` from this file's folder upward. Returns the file read, or None
    when reading is switched off or there is no such file.
    """
    if not ENV_FILE_ENABLED:
        return None
    if path is not None:
        target: Optional[Path] = Path(path)
    elif ENV_FILE_PATH is not None:
        target = Path(ENV_FILE_PATH)
    else:
        target = find_env_file(Path(__file__).resolve().parent)
    if target is None or not target.is_file():
        return None
    load_dotenv(dotenv_path=target, override=False)
    return target


# ── value helpers ────────────────────────────────────────────────────────────

class MissingSetting(RuntimeError):
    """A required variable is unset or empty. ``str()``: ``<NAME> is required``."""

    def __init__(self, name: str):
        super().__init__(name)
        self.name = name

    def __str__(self) -> str:
        return f"{self.name} is required"


def required(name: str) -> str:
    """The value of ``name``; MissingSetting when it is unset or empty."""
    value = os.environ.get(name)
    if not value:
        raise MissingSetting(name)
    return value


def optional(name: str, default: Optional[str] = None) -> Optional[str]:
    """The value of ``name``; ``default`` only when unset (an empty value is kept)."""
    return os.environ.get(name, default)


def _number(name: str, default, kind, what: str):
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return kind(raw)
    except ValueError:
        raise ValueError(f"{name} must be {what}, got {raw!r}") from None


def get_int(name: str, default: int) -> int:
    """``int`` of ``name``; ``default`` when unset. Any other value (even empty) raises ValueError."""
    return _number(name, default, int, "an integer")


def get_float(name: str, default: float) -> float:
    """``float`` of ``name``; ``default`` when unset. Any other value (even empty) raises ValueError."""
    return _number(name, default, float, "a number")


def get_bool(name: str, default: bool = False) -> bool:
    """True only for ``true`` (any case); ``default`` when unset."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() == "true"


def model_name(role: str, default: str) -> str:
    """``LLM_MODEL_<ROLE>``, else the legacy ``OPENAI_MODEL_<ROLE>``, else ``default``.

    An empty value counts as unset.
    """
    role = role.upper()
    return (os.environ.get(f"LLM_MODEL_{role}")
            or os.environ.get(f"OPENAI_MODEL_{role}")
            or default)


# ── shared values ────────────────────────────────────────────────────────────

DEFAULT_STORAGE_BASE_DIR = "./reports"
DEFAULT_KRX_CSV_PATH = "docs/stock_data/KRX_stocks_data.csv"


def _secret(name: str) -> Optional[str]:
    return optional(name) or None


def supabase_url() -> Optional[str]:
    return _secret("SUPABASE_URL")


def supabase_service_key() -> Optional[str]:
    return _secret("SUPABASE_SERVICE_KEY")


def supabase_db_url() -> Optional[str]:
    return _secret("SUPABASE_DB_URL")


def openai_api_key() -> Optional[str]:
    return _secret("OPENAI_API_KEY")


def anthropic_api_key() -> Optional[str]:
    return _secret("ANTHROPIC_API_KEY")


def storage_base_dir() -> Path:
    """Folder holding the collected PDFs (relative paths are from the current folder)."""
    return Path(optional("STORAGE_BASE_DIR", DEFAULT_STORAGE_BASE_DIR))


def krx_csv_path() -> Path:
    """The stock list CSV (relative paths are from the current folder)."""
    return Path(optional("KRX_CSV_PATH", DEFAULT_KRX_CSV_PATH))


# ── feature not ready ────────────────────────────────────────────────────────

class NotReady(Exception):
    """A feature could not prepare and cannot be used until the cause is fixed.

    ``area`` is the feature's display name (e.g. ``분석``). ``reason`` is a fixed
    Korean sentence; it may name a variable but holds no file path, key value
    or traceback. ``str()`` is the user-facing text:
    ``<area> 기능을 지금 쓸 수 없습니다: <reason>``.
    """

    def __init__(self, area: str, reason: str):
        super().__init__(area, reason)
        self.area = area
        self.reason = reason

    def __str__(self) -> str:
        return f"{self.area} 기능을 지금 쓸 수 없습니다: {self.reason}"
