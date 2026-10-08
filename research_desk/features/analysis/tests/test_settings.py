"""Analysis settings (spec §7).

Ported from langgraph_tagger/analytics/llm_summary/tests/test_config.py:
- the SUPABASE_DB_URL requirement became "not required" (spec §10 item 6);
- the max_concurrent (PHASE2_MAX_CONCURRENT) check is gone (spec §10 item 6).
Every variable read here is set or deleted by the test itself.
"""
import dataclasses

import pytest

from research_desk.features.analysis.settings import AnalysisSettings, load_settings

PHASE2_VARS = ('LLM_MODEL_PHASE2', 'OPENAI_MODEL_PHASE2', 'PHASE2_MAX_CONCURRENT',
               'PHASE2_PER_REPORT_TIMEOUT_S', 'PHASE2_MAX_INPUT_TOKENS',
               'PHASE2_SUMMARY_VERSION', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY',
               'SUPABASE_DB_URL')


@pytest.fixture
def clean(monkeypatch):
    for k in PHASE2_VARS:
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def test_load_happy_path(clean):
    cfg = load_settings()

    assert isinstance(cfg, AnalysisSettings)
    assert cfg.llm_model == 'gpt-6-luna'
    assert cfg.per_report_timeout_s == 180
    assert cfg.max_input_tokens == 30000
    assert cfg.summary_version == 'llm-summary@1.0'
    # No key, DB URL or concurrency setting is part of the analysis settings:
    # keys are checked right before an AI call (phase2_llm), the AI concurrency
    # is fixed at 2 (ai_slot), and analysis does not use SUPABASE_DB_URL.
    assert [f.name for f in dataclasses.fields(cfg)] == [
        'llm_model', 'per_report_timeout_s', 'max_input_tokens', 'summary_version']


def test_load_overrides(clean):
    clean.setenv('OPENAI_MODEL_PHASE2', 'gpt-5.4')  # legacy name still honored
    clean.setenv('PHASE2_MAX_CONCURRENT', '3')      # removed setting: ignored
    clean.setenv('PHASE2_PER_REPORT_TIMEOUT_S', '90')
    clean.setenv('PHASE2_MAX_INPUT_TOKENS', '15000')
    clean.setenv('PHASE2_SUMMARY_VERSION', 'llm-summary@2.0')

    cfg = load_settings()
    assert cfg.llm_model == 'gpt-5.4'
    assert cfg.per_report_timeout_s == 90
    assert cfg.max_input_tokens == 15000
    assert cfg.summary_version == 'llm-summary@2.0'
    assert not hasattr(cfg, 'max_concurrent')


def test_db_url_not_required(clean):
    # Was test_missing_db_url_exits (SystemExit): analysis no longer needs it.
    cfg = load_settings()
    assert cfg.llm_model == 'gpt-6-luna'


def test_llm_model_phase2_wins_over_legacy_name(clean):
    clean.setenv('LLM_MODEL_PHASE2', 'claude-haiku-5-5')
    clean.setenv('OPENAI_MODEL_PHASE2', 'gpt-5.4')
    assert load_settings().llm_model == 'claude-haiku-5-5'


def test_empty_model_name_falls_back(clean):
    clean.setenv('LLM_MODEL_PHASE2', '')
    clean.setenv('OPENAI_MODEL_PHASE2', '')
    assert load_settings().llm_model == 'gpt-6-luna'


def test_bad_number_is_an_error(clean):
    clean.setenv('PHASE2_MAX_INPUT_TOKENS', 'lots')
    with pytest.raises(ValueError):
        load_settings()
