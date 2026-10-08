"""Env loader for tagger."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from langgraph_tagger.llm_provider import api_key_env

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = lambda *a, **k: False


def _model(role: str, default: str) -> str:
    """LLM_MODEL_<ROLE>, falling back to the legacy OPENAI_MODEL_<ROLE> name."""
    return (os.environ.get(f"LLM_MODEL_{role}")
            or os.environ.get(f"OPENAI_MODEL_{role}")
            or default)


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
    load_dotenv()
    def _req(name: str) -> str:
        v = os.environ.get(name)
        if not v:
            raise RuntimeError(f"{name} is required")
        return v
    model_default = _model("DEFAULT", "claude-haiku-5-5")
    if api_key_env(model_default):
        _req(api_key_env(model_default))
    return TaggerConfig(
        openai_api_key=os.environ.get("OPENAI_API_KEY"),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
        model_default=model_default,
        model_escalation=_model("ESCALATION", "gpt-5.4"),
        max_concurrent_llm=int(os.environ.get("MAX_CONCURRENT_LLM", "10")),
        batch_size_default=int(os.environ.get("TAGGER_BATCH_SIZE_DEFAULT", "10")),
        krx_csv_path=Path(os.environ.get("KRX_CSV_PATH", "docs/stock_data/KRX_stocks_data.csv")),
        supabase_db_url=_req("SUPABASE_DB_URL"),
        lock_ttl_minutes=int(os.environ.get("LOCK_TTL_MINUTES", "30")),
        per_row_deadline_s=float(os.environ.get("PER_ROW_DEADLINE_S", "90")),
        heartbeat_enabled=os.environ.get("HEARTBEAT_ENABLED", "false").lower() == "true",
        heartbeat_interval_s=int(os.environ.get("HEARTBEAT_INTERVAL_S", "30")),
    )
