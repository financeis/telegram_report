"""Peers settings: defaults, .env names, the legacy model names and the synonym table file."""
from __future__ import annotations

import pytest

from research_desk.features.peers import settings
from research_desk.features.peers.logic import Synonyms


def test_defaults_when_nothing_is_set():
    cfg = settings.load_settings()
    assert cfg.profile_model == 'claude-sonnet-5-5'
    assert cfg.escalation_model == 'gpt-5.4'
    assert cfg.profile_version == 'peer-profile@1.2'
    assert cfg.embed_model == 'text-embedding-3-large'
    assert cfg.fiscal_year == 2025
    assert cfg.max_concurrent_llm == 2          # the operating ceiling
    assert cfg.per_company_timeout_s == 120
    assert cfg.mongo_url == 'mongodb://localhost:27017/'
    assert cfg.mongo_db == 'FS'
    assert cfg.mongo_collection == 'A001_v2'


def test_every_value_comes_from_its_variable(monkeypatch):
    values = {
        'LLM_MODEL_PEERS': 'gpt-5.4-mini', 'LLM_MODEL_PEERS_ESCALATION': 'claude-sonnet-5',
        'PEERS_PROFILE_VERSION': 'peer-profile@2.0-try', 'PEERS_EMBED_MODEL': 'text-embedding-3-small',
        'PEERS_FISCAL_YEAR': '2026', 'PEERS_MAX_CONCURRENT_LLM': '1',
        'PEERS_PER_COMPANY_TIMEOUT_S': '90', 'DART_MONGO_URL': 'mongodb://db.local:27018/',
        'DART_MONGO_DB': 'DART', 'DART_MONGO_COLLECTION': 'A001_v3',
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    cfg = settings.load_settings()
    assert (cfg.profile_model, cfg.escalation_model) == ('gpt-5.4-mini', 'claude-sonnet-5')
    assert (cfg.profile_version, cfg.embed_model) == ('peer-profile@2.0-try', 'text-embedding-3-small')
    assert (cfg.fiscal_year, cfg.max_concurrent_llm, cfg.per_company_timeout_s) == (2026, 1, 90)
    assert (cfg.mongo_url, cfg.mongo_db, cfg.mongo_collection) == (
        'mongodb://db.local:27018/', 'DART', 'A001_v3')


def test_model_names_follow_the_llm_model_convention_with_the_legacy_names(monkeypatch):
    monkeypatch.setenv('OPENAI_MODEL_PEERS', 'gpt-legacy')
    monkeypatch.setenv('OPENAI_MODEL_PEERS_ESCALATION', 'gpt-legacy-big')
    assert settings.profile_model() == 'gpt-legacy'
    assert settings.escalation_model() == 'gpt-legacy-big'
    monkeypatch.setenv('LLM_MODEL_PEERS', 'claude-haiku-5-5')
    assert settings.profile_model() == 'claude-haiku-5-5'


@pytest.mark.parametrize('name', ['PEERS_PROFILE_VERSION', 'PEERS_EMBED_MODEL', 'DART_MONGO_URL',
                                  'DART_MONGO_DB', 'DART_MONGO_COLLECTION'])
def test_an_empty_text_value_counts_as_unset(monkeypatch, name):
    monkeypatch.setenv(name, '')
    cfg = settings.load_settings()
    assert getattr(cfg, {'PEERS_PROFILE_VERSION': 'profile_version', 'PEERS_EMBED_MODEL': 'embed_model',
                         'DART_MONGO_URL': 'mongo_url', 'DART_MONGO_DB': 'mongo_db',
                         'DART_MONGO_COLLECTION': 'mongo_collection'}[name])


@pytest.mark.parametrize('name', ['PEERS_FISCAL_YEAR', 'PEERS_MAX_CONCURRENT_LLM',
                                  'PEERS_PER_COMPANY_TIMEOUT_S'])
def test_a_malformed_number_names_its_variable(monkeypatch, name):
    monkeypatch.setenv(name, 'two')
    with pytest.raises(ValueError, match=name):
        settings.load_settings()


@pytest.mark.parametrize('value', ['0', '-1'])
def test_concurrency_below_one_is_refused(monkeypatch, value):
    monkeypatch.setenv('PEERS_MAX_CONCURRENT_LLM', value)
    with pytest.raises(ValueError, match='PEERS_MAX_CONCURRENT_LLM'):
        settings.load_settings()


def test_nothing_is_read_at_import(monkeypatch):
    """The values are read when asked: a change after import is seen."""
    monkeypatch.setenv('PEERS_FISCAL_YEAR', '2031')
    assert settings.load_settings().fiscal_year == 2031


def test_the_shipped_synonym_table_loads_with_the_spec_examples():
    table = settings.synonyms()
    assert isinstance(table, Synonyms)
    assert table.key('디램') == table.key('D램') == table.key('DRAM') == 'dram'
    assert table.key('2차전지') == table.key('이차전지')
    assert table.key('양극활물질') == table.key('양극재')
    assert table.display('dram') == 'DRAM'
    assert len(table.fingerprint) == 64


def test_the_synonym_fingerprint_follows_the_content(tmp_path):
    first = tmp_path / 'a.yaml'
    first.write_text('DRAM: [디램, D램]\n', encoding='utf-8')
    same = tmp_path / 'b.yaml'
    same.write_text('# 주석만 다름\nDRAM:\n  - 디램\n  - D램\n', encoding='utf-8')
    other = tmp_path / 'c.yaml'
    other.write_text('DRAM: [디램]\n', encoding='utf-8')
    assert settings.synonyms(first).fingerprint == settings.synonyms(same).fingerprint
    assert settings.synonyms(first).fingerprint != settings.synonyms(other).fingerprint
