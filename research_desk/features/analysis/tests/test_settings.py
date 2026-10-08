"""Analysis settings (spec §7) and the model readiness check ``phase2_llm()``.

Ported from langgraph_tagger/analytics/llm_summary/tests/test_config.py:
- the SUPABASE_DB_URL requirement became "not required" (spec §10 item 6);
- the max_concurrent (PHASE2_MAX_CONCURRENT) check is gone (spec §10 item 6);
- make_llm_client's RuntimeError became NotReady("분석", <same Korean text>)
  raised by phase2_llm() (spec §9.9).
New: phase2_llm() re-reads .env before checking the key (env_file fixture).
Every variable read here is set or deleted by the test itself.
"""
import dataclasses

import pytest

from research_desk.core import llm as core_llm
from research_desk.core.llm import LLMClient
from research_desk.core.settings import NotReady
from research_desk.features.analysis import phase2_llm
from research_desk.features.analysis.settings import AnalysisSettings, load_settings

PHASE2_VARS = ('LLM_MODEL_PHASE2', 'OPENAI_MODEL_PHASE2', 'PHASE2_MAX_CONCURRENT',
               'PHASE2_PER_REPORT_TIMEOUT_S', 'PHASE2_MAX_INPUT_TOKENS',
               'PHASE2_SUMMARY_VERSION', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY',
               'SUPABASE_DB_URL', 'SUPABASE_URL', 'SUPABASE_SERVICE_KEY', 'CODEX_BIN',
               'ANTHROPIC_EFFORT', 'CODEX_REASONING_EFFORT')

KEY_MISSING = '{}가 설정되지 않았습니다. .env에 추가 후 분석 다시 시도하세요.'
CODEX_MISSING = 'codex CLI를 찾을 수 없습니다. 설치 후 `codex login`으로 로그인하세요.'


@pytest.fixture
def clean(monkeypatch):
    for k in PHASE2_VARS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(core_llm, '_codex_bin', lambda: None)  # no codex CLI unless set
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


# ── phase2_llm(): model key / codex CLI, checked only when called ────────────

def test_phase2_llm_lazy_success(clean):
    """Checked when phase2_llm() is called (right before an AI call), not at load."""
    clean.setenv('LLM_MODEL_PHASE2', 'claude-haiku-5-5')
    clean.setenv('ANTHROPIC_API_KEY', 'sk-ant-test')
    client, model, timeout_s = phase2_llm()
    assert isinstance(client, LLMClient)
    assert (model, timeout_s) == ('claude-haiku-5-5', 180)
    assert client.require_key('claude-haiku-5-5') == 'sk-ant-test'


def test_phase2_llm_returns_the_configured_model_and_timeout(clean):
    clean.setenv('OPENAI_MODEL_PHASE2', 'gpt-5.4')  # legacy name
    clean.setenv('PHASE2_PER_REPORT_TIMEOUT_S', '42')
    clean.setenv('OPENAI_API_KEY', 'sk-test')
    client, model, timeout_s = phase2_llm()
    assert (model, timeout_s) == ('gpt-5.4', 42)
    assert client.require_key('gpt-5.4') == 'sk-test'


def test_phase2_llm_keeps_sdk_default_retries_and_no_client_timeout(clean):
    # spec §9.3: analysis/compare use the SDK's default retries; the per-call
    # limit is PHASE2_PER_REPORT_TIMEOUT_S around each call (extract_one).
    clean.setenv('OPENAI_API_KEY', 'sk-test')
    client, _, _ = phase2_llm()
    assert client._sdk_kwargs == {'max_retries': 2}


@pytest.mark.parametrize('model,env', [
    ('claude-haiku-5-5', 'ANTHROPIC_API_KEY'),
    ('gpt-5.4', 'OPENAI_API_KEY'),
])
def test_phase2_llm_missing_key_is_not_ready(clean, model, env):
    clean.setenv('LLM_MODEL_PHASE2', model)
    with pytest.raises(NotReady) as e:
        phase2_llm()
    assert e.value.area == '분석'
    assert e.value.reason == KEY_MISSING.format(env)
    assert str(e.value) == f'분석 기능을 지금 쓸 수 없습니다: {KEY_MISSING.format(env)}'


def test_phase2_llm_default_model_needs_the_openai_key(clean):
    with pytest.raises(NotReady) as e:
        phase2_llm()
    assert e.value.reason == KEY_MISSING.format('OPENAI_API_KEY')


def test_phase2_llm_codex_missing_cli(clean):
    clean.setenv('LLM_MODEL_PHASE2', 'codex:gpt-6-luna')
    clean.setenv('OPENAI_API_KEY', 'sk-test')      # an API key does not stand in for codex
    with pytest.raises(NotReady) as e:
        phase2_llm()
    assert (e.value.area, e.value.reason) == ('분석', CODEX_MISSING)


def test_phase2_llm_codex_needs_no_api_key(clean):
    clean.setenv('LLM_MODEL_PHASE2', 'codex:gpt-6-luna')
    clean.setattr(core_llm, '_codex_bin', lambda: 'codex')
    client, model, timeout_s = phase2_llm()
    assert (model, timeout_s) == ('codex:gpt-6-luna', 180)


def test_phase2_llm_does_not_need_db_settings(clean):
    # SUPABASE_* are deleted by `clean`: the AI connection needs only its key.
    clean.setenv('OPENAI_API_KEY', 'sk-test')
    assert phase2_llm()[1] == 'gpt-6-luna'


def test_phase2_llm_rereads_env_file(clean, env_file):
    """A key added to .env counts on the next attempt, without a restart."""
    env_file.write_text('LLM_MODEL_PHASE2=claude-haiku-5-5\n', encoding='utf-8')
    with pytest.raises(NotReady) as e:
        phase2_llm()
    assert e.value.reason == KEY_MISSING.format('ANTHROPIC_API_KEY')

    env_file.write_text('LLM_MODEL_PHASE2=claude-haiku-5-5\n'
                        'ANTHROPIC_API_KEY=sk-ant-from-env-file\n'
                        'PHASE2_PER_REPORT_TIMEOUT_S=77\n', encoding='utf-8')
    client, model, timeout_s = phase2_llm()
    assert (model, timeout_s) == ('claude-haiku-5-5', 77)
    assert client.require_key(model) == 'sk-ant-from-env-file'
