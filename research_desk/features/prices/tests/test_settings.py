"""Prices settings (spec §8): KIS_APP_KEY, KIS_APP_SECRET, KIS_BASE_URL, PRICES_MAX_CALLS_PER_SEC,
KIS_TOKEN_CACHE.

Read when called (never at import); every variable read here is set or deleted by the test.
"""
from __future__ import annotations

import pytest

from research_desk.core import kis as core_kis
from research_desk.core import settings as core_settings
from research_desk.features.prices import settings

VARIABLES = ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_BASE_URL", "PRICES_MAX_CALLS_PER_SEC", "KIS_TOKEN_CACHE")
APP_KEY, APP_SECRET = "PSfakeAppKey0123456789", "fakeAppSecret/0123456789+abc=="


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in VARIABLES:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_defaults_without_any_setting():
    cfg = settings.load_settings()
    assert cfg.app_key is None and cfg.app_secret is None
    assert cfg.base_url == "https://openapi.koreainvestment.com:9443"
    assert cfg.max_calls_per_sec == 10.0
    assert cfg.token_cache is None
    assert not cfg.has_kis_keys


def test_the_default_address_is_the_kis_clients_real_service():
    assert settings.DEFAULT_KIS_BASE_URL == core_kis.DEFAULT_BASE_URL


def test_values_from_the_environment(clean):
    clean.setenv("KIS_APP_KEY", APP_KEY)
    clean.setenv("KIS_APP_SECRET", APP_SECRET)
    clean.setenv("KIS_BASE_URL", "https://openapivts.koreainvestment.com:29443")
    clean.setenv("PRICES_MAX_CALLS_PER_SEC", "2.5")
    clean.setenv("KIS_TOKEN_CACHE", "C:/shared/state/kis_token.json")
    cfg = settings.load_settings()
    assert (cfg.app_key, cfg.app_secret) == (APP_KEY, APP_SECRET)
    assert cfg.base_url == "https://openapivts.koreainvestment.com:29443"
    assert cfg.max_calls_per_sec == 2.5
    assert cfg.token_cache == "C:/shared/state/kis_token.json"
    assert cfg.has_kis_keys


def test_empty_values_count_as_unset(clean):
    for name in ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_BASE_URL", "KIS_TOKEN_CACHE"):
        clean.setenv(name, "")
    cfg = settings.load_settings()
    assert cfg.app_key is None and cfg.app_secret is None
    assert cfg.base_url == settings.DEFAULT_KIS_BASE_URL
    assert cfg.token_cache is None


@pytest.mark.parametrize("name", ["KIS_APP_KEY", "KIS_APP_SECRET"])
def test_one_key_alone_is_not_enough(clean, name):
    clean.setenv(name, "something")
    assert not settings.load_settings().has_kis_keys


@pytest.mark.parametrize("raw", ["fast", "", "0", "-1", "nan", "inf"])
def test_a_call_rate_that_is_not_a_number_above_0_is_an_error(clean, raw):
    clean.setenv("PRICES_MAX_CALLS_PER_SEC", raw)
    with pytest.raises(ValueError, match="PRICES_MAX_CALLS_PER_SEC"):
        settings.load_settings()


def test_the_keys_never_show_in_the_settings_text(clean):
    clean.setenv("KIS_APP_KEY", APP_KEY)
    clean.setenv("KIS_APP_SECRET", APP_SECRET)
    text = repr(settings.load_settings()) + str(settings.load_settings())
    assert APP_KEY not in text and APP_SECRET not in text


def test_values_added_to_env_are_read_after_load_env(env_file):
    env_file.write_text(f"KIS_APP_KEY={APP_KEY}\nKIS_APP_SECRET={APP_SECRET}\n"
                        "PRICES_MAX_CALLS_PER_SEC=4\n", encoding="utf-8")
    assert not settings.load_settings().has_kis_keys   # .env is read by load_env only
    core_settings.load_env()
    cfg = settings.load_settings()
    assert (cfg.app_key, cfg.app_secret, cfg.max_calls_per_sec) == (APP_KEY, APP_SECRET, 4.0)
