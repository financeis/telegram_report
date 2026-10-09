"""Tagger settings (spec §7; §10 items 4-6): defaults, legacy names, removed values."""
from __future__ import annotations

from pathlib import Path

import pytest

from research_desk.tagger import settings as tagger_settings

TAGGER_DIR = Path(tagger_settings.__file__).resolve().parent


def test_defaults(tagger_env):
    assert tagger_settings.default_model() == "claude-haiku-5-5"
    assert tagger_settings.escalation_model() == "gpt-5.4"
    assert tagger_settings.max_concurrent_llm() == 2          # spec §10 item 4 (was 10)
    assert tagger_settings.batch_size_default() == 10
    assert tagger_settings.lock_ttl_minutes() == 30
    assert tagger_settings.per_row_deadline_s() == 90.0


def test_values_from_the_environment(tagger_env):
    tagger_env.setenv("LLM_MODEL_DEFAULT", "gpt-5.4-mini")
    tagger_env.setenv("LLM_MODEL_ESCALATION", "claude-opus-5")
    tagger_env.setenv("MAX_CONCURRENT_LLM", "3")
    tagger_env.setenv("TAGGER_BATCH_SIZE_DEFAULT", "20")
    tagger_env.setenv("LOCK_TTL_MINUTES", "15")
    tagger_env.setenv("PER_ROW_DEADLINE_S", "45.5")
    assert tagger_settings.default_model() == "gpt-5.4-mini"
    assert tagger_settings.escalation_model() == "claude-opus-5"
    assert tagger_settings.max_concurrent_llm() == 3
    assert tagger_settings.batch_size_default() == 20
    assert tagger_settings.lock_ttl_minutes() == 15
    assert tagger_settings.per_row_deadline_s() == 45.5


def test_legacy_model_names_still_work(tagger_env):
    tagger_env.setenv("OPENAI_MODEL_DEFAULT", "gpt-5.4-mini")
    tagger_env.setenv("OPENAI_MODEL_ESCALATION", "gpt-5.4")
    assert tagger_settings.default_model() == "gpt-5.4-mini"
    assert tagger_settings.escalation_model() == "gpt-5.4"
    tagger_env.setenv("LLM_MODEL_DEFAULT", "claude-haiku-5-5")
    assert tagger_settings.default_model() == "claude-haiku-5-5"   # new name wins


def test_settings_are_read_when_asked_not_at_import(tagger_env):
    assert tagger_settings.max_concurrent_llm() == 2
    tagger_env.setenv("MAX_CONCURRENT_LLM", "4")
    assert tagger_settings.max_concurrent_llm() == 4


@pytest.mark.parametrize("name,getter", [
    ("MAX_CONCURRENT_LLM", tagger_settings.max_concurrent_llm),
    ("TAGGER_BATCH_SIZE_DEFAULT", tagger_settings.batch_size_default),
    ("LOCK_TTL_MINUTES", tagger_settings.lock_ttl_minutes),
    ("PER_ROW_DEADLINE_S", tagger_settings.per_row_deadline_s),
])
def test_malformed_numbers_raise(tagger_env, name, getter):
    tagger_env.setenv(name, "lots")
    with pytest.raises(ValueError, match=name):
        getter()


def test_fixed_values():
    """Not settings: pool size and the AI call's SDK retries / timeout (spec §9.3)."""
    assert tagger_settings.DB_POOL_MAX_SIZE == 10
    assert tagger_settings.LLM_MAX_RETRIES == 2
    assert tagger_settings.LLM_TIMEOUT_S == 60.0


def test_heartbeat_settings_are_gone():
    """spec §7 / §10 item 6: HEARTBEAT_* was never used and is no longer read."""
    sources = [p for p in TAGGER_DIR.rglob("*.py") if "tests" not in p.relative_to(TAGGER_DIR).parts]
    assert sources
    for path in sources:
        assert "HEARTBEAT" not in path.read_text(encoding="utf-8").upper(), path
