"""Safety net for every compare test: no settings, no codex CLI, no real Supabase client.

``clean_env`` (autouse) deletes every environment variable compare or the windows it uses could
read, makes the codex CLI look missing, and makes ``core.db.supabase_client`` refuse. A test that
needs a value sets it itself.
"""
from __future__ import annotations

import pytest

from research_desk.core import db as core_db
from research_desk.core import llm as core_llm

ENV = ('ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'LLM_MODEL_PHASE2', 'OPENAI_MODEL_PHASE2',
       'PHASE2_PER_REPORT_TIMEOUT_S', 'PHASE2_MAX_INPUT_TOKENS', 'PHASE2_SUMMARY_VERSION',
       'PHASE2_MAX_CONCURRENT', 'SUPABASE_URL', 'SUPABASE_SERVICE_KEY', 'SUPABASE_DB_URL',
       'STORAGE_BASE_DIR', 'KRX_CSV_PATH', 'CODEX_BIN', 'ANTHROPIC_EFFORT',
       'CODEX_REASONING_EFFORT', 'LANGSMITH_TRACING', 'LANGSMITH_API_KEY')


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(core_llm, '_codex_bin', lambda: None)

    def refuse(url, key):
        raise AssertionError('no real Supabase client in these tests')

    monkeypatch.setattr(core_db, 'supabase_client', refuse)
