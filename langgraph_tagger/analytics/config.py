"""Standalone config for the analytics dashboard.

Intentionally separate from langgraph_tagger.config so the dashboard can
run without LLM API keys (OPENAI_API_KEY / ANTHROPIC_API_KEY), SUPABASE_DB_URL,
or TELEGRAM_* envs.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class AnalyticsConfig:
    supabase_url: str
    supabase_service_key: str
    storage_base_dir: Path
    krx_csv_path: Path


def load_analytics_config() -> AnalyticsConfig:
    load_dotenv()

    def required(key: str) -> str:
        v = os.getenv(key)
        if not v:
            raise SystemExit(f"Missing required env var: {key}")
        return v

    return AnalyticsConfig(
        supabase_url=required('SUPABASE_URL'),
        supabase_service_key=required('SUPABASE_SERVICE_KEY'),
        storage_base_dir=Path(os.getenv('STORAGE_BASE_DIR', './reports')),
        krx_csv_path=Path(os.getenv('KRX_CSV_PATH', 'docs/stock_data/KRX_stocks_data.csv')),
    )
