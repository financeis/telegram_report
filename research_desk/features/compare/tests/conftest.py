"""Safety net for every compare test, and the window stand-ins.

- ``clean_env`` (autouse) deletes every environment variable compare or the windows it uses could
  read, makes the codex CLI look missing, and makes ``core.db.supabase_client`` refuse. A test
  that needs a value sets it itself. So the real ``analysis.phase2_llm`` raises
  ``NotReady("분석", …)`` unless a test gives it a key.
- ``world``: ``reports.get_report`` and ``analysis.save_comparison`` replaced where compare looks
  them up (see fakes.World).
- ``ai``: ``analysis.phase2_llm`` replaced the same way (see fakes.FakeAI). ``analysis.ai_slot``
  is never replaced: compare always shares the real 2-call limit.
"""
from __future__ import annotations

import pytest

from research_desk.core import db as core_db
from research_desk.core import llm as core_llm
from research_desk.features import analysis, reports

from .fakes import FakeAI, World

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


@pytest.fixture
def world(monkeypatch) -> World:
    fake = World()
    monkeypatch.setattr(reports, 'get_report', fake.get_report)
    monkeypatch.setattr(analysis, 'save_comparison', fake.save_comparison)
    return fake


@pytest.fixture
def ai(monkeypatch) -> FakeAI:
    fake = FakeAI()
    monkeypatch.setattr(analysis, 'phase2_llm', fake.phase2_llm)
    return fake
