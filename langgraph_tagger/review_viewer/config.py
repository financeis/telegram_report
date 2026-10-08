"""Standalone config for the review viewer.

Intentionally separate from langgraph_tagger.config so the viewer can run
without LLM API keys, SUPABASE_DB_URL, or TELEGRAM_* envs — those belong
to the tagger and collector.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class ReviewViewerConfig:
    supabase_url: str
    supabase_service_key: str
    storage_base_dir: Path


def load_review_viewer_config() -> ReviewViewerConfig:
    load_dotenv()

    def required(key: str) -> str:
        v = os.getenv(key)
        if not v:
            raise SystemExit(f"Missing required env var: {key}")
        return v

    return ReviewViewerConfig(
        supabase_url=required('SUPABASE_URL'),
        supabase_service_key=required('SUPABASE_SERVICE_KEY'),
        storage_base_dir=Path(os.getenv('STORAGE_BASE_DIR', './reports')),
    )
