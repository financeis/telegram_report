"""Tagger settings, read with the core.settings helpers when a command starts."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from research_desk.core import settings
from research_desk.core.llm import api_key_env


@dataclass(frozen=True)
class TaggerConfig:
    # Only the key for the configured model's provider is required at load;
    # the other is checked on first use (e.g. escalate on another provider).
    openai_api_key: Optional[str]
    anthropic_api_key: Optional[str]
    model_default: str
    model_escalation: str
    max_concurrent_llm: int
    batch_size_default: int
    krx_csv_path: Path
    supabase_db_url: str
    # Concurrency / lock safety knobs (spec §9.5)
    lock_ttl_minutes: int
    per_row_deadline_s: float
    # Heartbeat: reserved for v2. Loaded from env for forward-compat but
    # orchestrator does NOT consume these in v1 — see spec §9.2 note.
    heartbeat_enabled: bool
    heartbeat_interval_s: int


def load_config() -> TaggerConfig:
    settings.load_env()
    model_default = settings.model_name("DEFAULT", "claude-haiku-5-5")
    if api_key_env(model_default):
        settings.required(api_key_env(model_default))
    return TaggerConfig(
        openai_api_key=settings.optional("OPENAI_API_KEY"),
        anthropic_api_key=settings.optional("ANTHROPIC_API_KEY"),
        model_default=model_default,
        model_escalation=settings.model_name("ESCALATION", "gpt-5.4"),
        max_concurrent_llm=settings.get_int("MAX_CONCURRENT_LLM", 10),
        batch_size_default=settings.get_int("TAGGER_BATCH_SIZE_DEFAULT", 10),
        krx_csv_path=settings.krx_csv_path(),
        supabase_db_url=settings.required("SUPABASE_DB_URL"),
        lock_ttl_minutes=settings.get_int("LOCK_TTL_MINUTES", 30),
        per_row_deadline_s=settings.get_float("PER_ROW_DEADLINE_S", 90.0),
        heartbeat_enabled=settings.get_bool("HEARTBEAT_ENABLED", False),
        heartbeat_interval_s=settings.get_int("HEARTBEAT_INTERVAL_S", 30),
    )
