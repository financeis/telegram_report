"""Tagger settings (spec §7), read with the core.settings helpers when asked.

The ``tag`` command reads them when it starts, never at import. Shared values
(SUPABASE_DB_URL, KRX_CSV_PATH, STORAGE_BASE_DIR, the API keys) are the
core.settings getters. A malformed number raises ValueError naming the variable.
"""
from __future__ import annotations

from research_desk.core import settings

DEFAULT_MODEL = "claude-haiku-5-5"        # LLM_MODEL_DEFAULT (old name OPENAI_MODEL_DEFAULT)
DEFAULT_ESCALATION_MODEL = "gpt-5.4"      # LLM_MODEL_ESCALATION (old name OPENAI_MODEL_ESCALATION)
# Operating ceiling: the provider's tokens-per-minute limit is the bottleneck,
# not requests. Raise only after checking the limit and the token trend.
DEFAULT_MAX_CONCURRENT_LLM = 2
DEFAULT_BATCH_SIZE = 10
DEFAULT_LOCK_TTL_MINUTES = 30
DEFAULT_PER_ROW_DEADLINE_S = 90.0

# Fixed values, not settings.
DB_POOL_MAX_SIZE = 10
LLM_MAX_RETRIES = 2       # SDK retries per AI call (spec §9.3)
LLM_TIMEOUT_S = 60.0      # per AI request


def default_model() -> str:
    """Model for ``tag run``."""
    return settings.model_name("DEFAULT", DEFAULT_MODEL)


def escalation_model() -> str:
    """Model for ``tag escalate``."""
    return settings.model_name("ESCALATION", DEFAULT_ESCALATION_MODEL)


def max_concurrent_llm() -> int:
    """AI calls running at once (``--max-concurrent-llm`` overrides it)."""
    return settings.get_int("MAX_CONCURRENT_LLM", DEFAULT_MAX_CONCURRENT_LLM)


def batch_size_default() -> int:
    """Rows claimed per ``tag run`` (``--batch-size`` overrides it)."""
    return settings.get_int("TAGGER_BATCH_SIZE_DEFAULT", DEFAULT_BATCH_SIZE)


def lock_ttl_minutes() -> int:
    """A 'processing' row older than this goes back to 'pending' when a run starts."""
    return settings.get_int("LOCK_TTL_MINUTES", DEFAULT_LOCK_TTL_MINUTES)


def per_row_deadline_s() -> float:
    """Time limit per row; past it the row goes back to 'pending'."""
    return settings.get_float("PER_ROW_DEADLINE_S", DEFAULT_PER_ROW_DEADLINE_S)
