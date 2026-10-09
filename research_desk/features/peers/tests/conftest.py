"""Safety net for every peers test.

- every setting the peers commands could read is deleted; a test that needs one sets it itself;
- no real Supabase client, MongoDB client or AI SDK client can be made: the core factories refuse
  (a test that needs a DB, a collection or an AI client hands out a fake from ``fakes``).
"""
from __future__ import annotations

import pytest

from research_desk.core import db as core_db
from research_desk.core import llm as core_llm
from research_desk.core import mongo as core_mongo

ENV = ('SUPABASE_URL', 'SUPABASE_SERVICE_KEY', 'SUPABASE_DB_URL', 'KRX_CSV_PATH',
       'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'CODEX_BIN', 'ANTHROPIC_EFFORT',
       'CODEX_REASONING_EFFORT', 'LANGSMITH_TRACING', 'LANGSMITH_API_KEY',
       'LLM_MODEL_PEERS', 'OPENAI_MODEL_PEERS', 'LLM_MODEL_PEERS_ESCALATION',
       'OPENAI_MODEL_PEERS_ESCALATION', 'PEERS_PROFILE_VERSION', 'PEERS_EMBED_MODEL',
       'PEERS_FISCAL_YEAR', 'PEERS_MAX_CONCURRENT_LLM', 'PEERS_PER_COMPANY_TIMEOUT_S',
       'DART_MONGO_URL', 'DART_MONGO_DB', 'DART_MONGO_COLLECTION')


@pytest.fixture(autouse=True)
def peers_isolation(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)

    def refuse(*args, **kwargs):
        raise AssertionError('no real Supabase, MongoDB or AI client in the peers tests')

    monkeypatch.setattr(core_db, 'supabase_client', refuse)
    monkeypatch.setattr(core_mongo, '_client', refuse)
    monkeypatch.setattr(core_llm.LLMClient, '_openai_client', refuse)
    monkeypatch.setattr(core_llm.LLMClient, '_anthropic_client', refuse)
