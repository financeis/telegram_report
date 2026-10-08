from langgraph_tagger.analytics.llm_summary.config import (
    LLMSummaryConfig, load_llm_summary_config, make_llm_client,
)
import pytest


def test_load_happy_path(monkeypatch):
    monkeypatch.setattr(
        'langgraph_tagger.analytics.llm_summary.config.load_dotenv',
        lambda *a, **k: False)
    monkeypatch.setenv('SUPABASE_DB_URL', 'postgres://x')
    for k in ('LLM_MODEL_PHASE2', 'OPENAI_MODEL_PHASE2', 'PHASE2_MAX_CONCURRENT',
              'PHASE2_PER_REPORT_TIMEOUT_S', 'PHASE2_MAX_INPUT_TOKENS',
              'PHASE2_SUMMARY_VERSION', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY'):
        monkeypatch.delenv(k, raising=False)

    cfg = load_llm_summary_config()

    assert isinstance(cfg, LLMSummaryConfig)
    assert cfg.llm_model == 'gpt-6-luna'
    assert cfg.max_concurrent == 2
    assert cfg.per_report_timeout_s == 180
    assert cfg.max_input_tokens == 30000
    assert cfg.summary_version == 'llm-summary@1.0'
    assert cfg.supabase_db_url == 'postgres://x'
    assert cfg.openai_api_key is None  # lazy: load 시점엔 안 채움
    assert cfg.anthropic_api_key is None


def test_load_overrides(monkeypatch):
    monkeypatch.setattr(
        'langgraph_tagger.analytics.llm_summary.config.load_dotenv',
        lambda *a, **k: False)
    monkeypatch.setenv('SUPABASE_DB_URL', 'postgres://x')
    monkeypatch.delenv('LLM_MODEL_PHASE2', raising=False)
    monkeypatch.setenv('OPENAI_MODEL_PHASE2', 'gpt-5.4')  # legacy name still honored
    monkeypatch.setenv('PHASE2_MAX_CONCURRENT', '3')
    monkeypatch.setenv('PHASE2_MAX_INPUT_TOKENS', '15000')
    monkeypatch.setenv('PHASE2_SUMMARY_VERSION', 'llm-summary@2.0')

    cfg = load_llm_summary_config()
    assert cfg.llm_model == 'gpt-5.4'
    assert cfg.max_concurrent == 3
    assert cfg.max_input_tokens == 15000
    assert cfg.summary_version == 'llm-summary@2.0'


def test_missing_db_url_exits(monkeypatch):
    monkeypatch.setattr(
        'langgraph_tagger.analytics.llm_summary.config.load_dotenv',
        lambda *a, **k: False)
    monkeypatch.delenv('SUPABASE_DB_URL', raising=False)
    with pytest.raises(SystemExit) as e:
        load_llm_summary_config()
    assert 'SUPABASE_DB_URL' in str(e.value)


def test_llm_model_phase2_wins_over_legacy_name(monkeypatch):
    monkeypatch.setattr(
        'langgraph_tagger.analytics.llm_summary.config.load_dotenv',
        lambda *a, **k: False)
    monkeypatch.setenv('SUPABASE_DB_URL', 'postgres://x')
    monkeypatch.setenv('LLM_MODEL_PHASE2', 'claude-haiku-5-5')
    monkeypatch.setenv('OPENAI_MODEL_PHASE2', 'gpt-5.4')
    assert load_llm_summary_config().llm_model == 'claude-haiku-5-5'


def _cfg(model):
    return LLMSummaryConfig(
        llm_model=model, max_concurrent=2, per_report_timeout_s=90,
        max_input_tokens=30000, summary_version='v', supabase_db_url='u',
        openai_api_key=None,
    )


def test_make_llm_client_lazy_success(monkeypatch):
    """load_config 시점이 아닌 make_llm_client() 시점에 검증."""
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-test')
    client = make_llm_client(_cfg('claude-haiku-5-5'))
    assert client.require_key('claude-haiku-5-5') == 'sk-ant-test'


@pytest.mark.parametrize('model,env', [
    ('claude-haiku-5-5', 'ANTHROPIC_API_KEY'),
    ('gpt-5.4', 'OPENAI_API_KEY'),
])
def test_make_llm_client_missing_key_raises(monkeypatch, model, env):
    monkeypatch.delenv(env, raising=False)
    with pytest.raises(RuntimeError) as e:
        make_llm_client(_cfg(model))
    assert env in str(e.value)


def test_make_llm_client_codex_missing_cli(monkeypatch):
    monkeypatch.setattr('langgraph_tagger.llm_provider._codex_bin', lambda: None)
    with pytest.raises(RuntimeError) as e:
        make_llm_client(_cfg('codex:gpt-6-luna'))
    assert 'codex' in str(e.value)
