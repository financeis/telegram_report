"""Collector settings (ported from the root tests/test_config.py).

The old tests blocked ``load_dotenv`` by hand; here research_desk/conftest.py
switches ``.env`` reading off for every test, and ``clean_env`` /
``required_env`` (collector conftest) unset every collector variable first.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from research_desk.collector.settings import Config, load_config
from research_desk.core.settings import MissingSetting


def test_load_config_happy_path(required_env):
    cfg = load_config()

    assert isinstance(cfg, Config)
    assert cfg.telegram_api_id == 12345
    assert cfg.telegram_api_hash == 'abcdef0123456789'
    assert cfg.telegram_channel == 'example_channel'
    assert cfg.supabase_url == 'https://test.supabase.co'
    assert cfg.supabase_service_key == 'eyJtest'
    # Defaults
    assert cfg.telegram_session_path == Path('sessions') / 'samstudy'
    assert cfg.storage_base_dir == Path('./reports')
    assert cfg.initial_cutoff_days == 30
    assert cfg.max_concurrent_downloads == 4
    assert cfg.log_level == 'INFO'
    assert cfg.telegram_channel_id is None


def test_load_config_missing_required_var_raises(clean_env):
    # Only set some of the required vars
    clean_env.setenv('TELEGRAM_API_ID', '12345')

    with pytest.raises(MissingSetting) as exc_info:
        load_config()
    # Message should mention which key is missing
    assert exc_info.value.name == 'TELEGRAM_API_HASH'
    assert 'TELEGRAM_API_HASH' in str(exc_info.value)


@pytest.mark.parametrize('name', ['TELEGRAM_API_ID', 'TELEGRAM_API_HASH', 'TELEGRAM_CHANNEL',
                                  'SUPABASE_URL', 'SUPABASE_SERVICE_KEY'])
@pytest.mark.parametrize('how', ['unset', 'empty'])
def test_load_config_each_required_var_is_required(required_env, clean_env, name, how):
    if how == 'unset':
        clean_env.delenv(name)
    else:
        clean_env.setenv(name, '')

    with pytest.raises(MissingSetting) as exc_info:
        load_config()
    assert exc_info.value.name == name


def test_load_config_optional_overrides(required_env, clean_env):
    clean_env.setenv('TELEGRAM_SESSION_NAME', 'mysess')
    clean_env.setenv('STORAGE_BASE_DIR', '/tmp/my_reports')
    clean_env.setenv('INITIAL_CUTOFF_DAYS', '7')
    clean_env.setenv('LOG_LEVEL', 'DEBUG')

    cfg = load_config()

    assert cfg.telegram_session_path == Path('sessions') / 'mysess'
    assert cfg.storage_base_dir == Path('/tmp/my_reports')
    assert cfg.initial_cutoff_days == 7
    assert cfg.log_level == 'DEBUG'


def test_load_config_max_concurrent_downloads_default(required_env):
    cfg = load_config()

    assert cfg.max_concurrent_downloads == 4


def test_load_config_max_concurrent_downloads_override(required_env, clean_env):
    clean_env.setenv('MAX_CONCURRENT_DOWNLOADS', '8')

    cfg = load_config()

    assert cfg.max_concurrent_downloads == 8


def test_load_config_telegram_channel_id_default_none(required_env):
    """CHANNEL_ID 환경변수 없으면 telegram_channel_id는 None."""
    cfg = load_config()
    assert cfg.telegram_channel_id is None


def test_load_config_telegram_channel_id_empty_is_none(required_env, clean_env):
    clean_env.setenv('TELEGRAM_CHANNEL_ID', '')

    cfg = load_config()
    assert cfg.telegram_channel_id is None


def test_load_config_telegram_channel_id_int_when_set(required_env, clean_env):
    """CHANNEL_ID 환경변수가 숫자 문자열이면 int로 캐스팅."""
    clean_env.setenv('TELEGRAM_CHANNEL_ID', '1000000001')

    cfg = load_config()
    assert cfg.telegram_channel_id == 1000000001
    assert isinstance(cfg.telegram_channel_id, int)


def test_channel_ref_returns_id_int_when_id_set(required_env, clean_env):
    clean_env.setenv('TELEGRAM_CHANNEL_ID', '1000000001')

    cfg = load_config()
    assert cfg.channel_ref() == 1000000001


def test_channel_ref_falls_back_to_username_when_id_unset(required_env):
    cfg = load_config()
    assert cfg.channel_ref() == 'example_channel'


@pytest.mark.parametrize('name, value', [
    ('TELEGRAM_API_ID', 'abc'),
    ('INITIAL_CUTOFF_DAYS', 'thirty'),
    ('MAX_CONCURRENT_DOWNLOADS', ''),
    ('TELEGRAM_CHANNEL_ID', '@channel'),
])
def test_load_config_malformed_number_raises_value_error(required_env, clean_env, name, value):
    clean_env.setenv(name, value)

    with pytest.raises(ValueError):
        load_config()


def test_load_config_reads_env_file_and_keeps_existing_values(clean_env, env_file):
    """The command start re-reads .env; variables already set are not replaced."""
    env_file.write_text(
        'TELEGRAM_API_ID=777\n'
        'TELEGRAM_API_HASH=hash-from-file\n'
        'TELEGRAM_CHANNEL=channel-from-file\n'
        'SUPABASE_URL=https://file.supabase.co\n'
        'SUPABASE_SERVICE_KEY=key-from-file\n'
        'MAX_CONCURRENT_DOWNLOADS=6\n',
        encoding='utf-8',
    )
    clean_env.setenv('TELEGRAM_CHANNEL', 'channel-from-process')

    cfg = load_config()

    assert cfg.telegram_api_id == 777
    assert cfg.telegram_api_hash == 'hash-from-file'
    assert cfg.telegram_channel == 'channel-from-process'
    assert cfg.supabase_url == 'https://file.supabase.co'
    assert cfg.max_concurrent_downloads == 6
