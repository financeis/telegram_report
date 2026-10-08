"""core.settings: .env reading, value helpers, shared values, NotReady."""
from __future__ import annotations

import ast
import os
import pickle
from pathlib import Path

import pytest

from research_desk.core import settings
# Imported by name on purpose: the switch must still apply (no function swap).
from research_desk.core.settings import MissingSetting, NotReady, find_env_file, load_env


def _never(*args, **kwargs):
    pytest.fail("load_env must not look for or read any .env file here")


# ── .env reading ─────────────────────────────────────────────────────────────

def test_env_reading_is_switched_off_for_every_test(tmp_path, monkeypatch):
    # Guard rails: even a broken switch must not reach the real .env.
    monkeypatch.setattr(settings, "find_env_file", _never)
    monkeypatch.setattr(settings, "load_dotenv", _never)
    monkeypatch.delenv("RD_CORE_SWITCH_OFF", raising=False)
    path = tmp_path / ".env"
    path.write_text("RD_CORE_SWITCH_OFF=1\n", encoding="utf-8")

    assert settings.ENV_FILE_ENABLED is False
    assert load_env() is None
    assert load_env(path) is None
    assert "RD_CORE_SWITCH_OFF" not in os.environ


def test_load_env_keeps_existing_values_and_picks_up_added_keys(env_file, monkeypatch):
    monkeypatch.setenv("RD_CORE_EXISTING", "from-process")
    monkeypatch.delenv("RD_CORE_ADDED", raising=False)
    env_file.write_text("RD_CORE_EXISTING=from-file\n", encoding="utf-8")

    assert load_env() == env_file
    assert os.environ["RD_CORE_EXISTING"] == "from-process"
    assert "RD_CORE_ADDED" not in os.environ

    # A key added to .env is picked up by the next call (no restart).
    env_file.write_text("RD_CORE_EXISTING=from-file\nRD_CORE_ADDED=added\n", encoding="utf-8")
    load_env()
    assert os.environ["RD_CORE_ADDED"] == "added"

    # A value already in the environment is never replaced (restart needed).
    env_file.write_text("RD_CORE_EXISTING=from-file\nRD_CORE_ADDED=changed\n", encoding="utf-8")
    load_env()
    assert os.environ["RD_CORE_ADDED"] == "added"
    assert os.environ["RD_CORE_EXISTING"] == "from-process"


def test_load_env_reads_an_explicit_path(env_file, tmp_path, monkeypatch):
    monkeypatch.delenv("RD_CORE_EXPLICIT", raising=False)
    other = tmp_path / "other.env"
    other.write_text("RD_CORE_EXPLICIT=yes\n", encoding="utf-8")

    assert load_env(other) == other
    assert os.environ["RD_CORE_EXPLICIT"] == "yes"


def test_load_env_without_a_file_reads_nothing(env_file, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ENV_FILE_PATH", tmp_path / "missing.env")
    assert load_env() is None
    assert load_env(tmp_path / "also-missing.env") is None


def test_load_env_searches_upward_from_the_core_folder(env_file, monkeypatch):
    monkeypatch.setattr(settings, "ENV_FILE_PATH", None)
    starts = []

    def fake_find(start, name=".env"):
        starts.append(Path(start))
        return env_file

    monkeypatch.setattr(settings, "find_env_file", fake_find)
    monkeypatch.delenv("RD_CORE_FOUND", raising=False)
    env_file.write_text("RD_CORE_FOUND=1\n", encoding="utf-8")

    assert load_env() == env_file
    assert starts == [Path(settings.__file__).resolve().parent]
    assert os.environ["RD_CORE_FOUND"] == "1"


# These two run in file order: the second checks the env_file fixture removed
# what load_env() loaded in the first (run alone, it passes trivially).
def test_env_file_fixture_loads_variables(env_file):
    env_file.write_text("RD_CORE_LEAK_CHECK=1\n", encoding="utf-8")
    load_env()
    assert os.environ["RD_CORE_LEAK_CHECK"] == "1"


def test_env_file_fixture_cleans_up_after_the_test():
    assert "RD_CORE_LEAK_CHECK" not in os.environ


def test_find_env_file_returns_the_nearest_one(tmp_path):
    top = tmp_path / "top"
    leaf = top / "mid" / "leaf"
    leaf.mkdir(parents=True)
    (top / ".env").write_text("", encoding="utf-8")
    assert find_env_file(leaf) == (top / ".env").resolve()

    (top / "mid" / ".env").write_text("", encoding="utf-8")
    assert find_env_file(leaf) == (top / "mid" / ".env").resolve()

    (leaf / ".env").mkdir()  # a folder named .env is not a .env file
    assert find_env_file(leaf) == (top / "mid" / ".env").resolve()


# ── value helpers ────────────────────────────────────────────────────────────

def test_required_returns_the_value_or_names_the_missing_variable(monkeypatch):
    monkeypatch.setenv("RD_CORE_REQ", "value")
    assert settings.required("RD_CORE_REQ") == "value"

    for state in ("unset", "empty"):
        if state == "unset":
            monkeypatch.delenv("RD_CORE_REQ", raising=False)
        else:
            monkeypatch.setenv("RD_CORE_REQ", "")
        with pytest.raises(MissingSetting) as exc:
            settings.required("RD_CORE_REQ")
        assert str(exc.value) == "RD_CORE_REQ is required"
        assert exc.value.name == "RD_CORE_REQ"
        assert isinstance(exc.value, RuntimeError)
        assert str(pickle.loads(pickle.dumps(exc.value))) == "RD_CORE_REQ is required"


def test_optional_uses_the_default_only_when_unset(monkeypatch):
    monkeypatch.delenv("RD_CORE_OPT", raising=False)
    assert settings.optional("RD_CORE_OPT") is None
    assert settings.optional("RD_CORE_OPT", "fallback") == "fallback"
    monkeypatch.setenv("RD_CORE_OPT", "")
    assert settings.optional("RD_CORE_OPT", "fallback") == ""
    monkeypatch.setenv("RD_CORE_OPT", "set")
    assert settings.optional("RD_CORE_OPT", "fallback") == "set"


def test_get_int_and_get_float(monkeypatch):
    monkeypatch.delenv("RD_CORE_NUM", raising=False)
    assert settings.get_int("RD_CORE_NUM", 10) == 10
    assert settings.get_float("RD_CORE_NUM", 90.0) == 90.0

    monkeypatch.setenv("RD_CORE_NUM", "2")
    assert settings.get_int("RD_CORE_NUM", 10) == 2
    assert settings.get_float("RD_CORE_NUM", 90.0) == 2.0
    monkeypatch.setenv("RD_CORE_NUM", "1.5")
    assert settings.get_float("RD_CORE_NUM", 90.0) == 1.5

    for bad in ("abc", ""):
        monkeypatch.setenv("RD_CORE_NUM", bad)
        with pytest.raises(ValueError, match="RD_CORE_NUM"):
            settings.get_int("RD_CORE_NUM", 10)
        with pytest.raises(ValueError, match="RD_CORE_NUM"):
            settings.get_float("RD_CORE_NUM", 90.0)


def test_get_bool_is_true_only_for_true(monkeypatch):
    monkeypatch.delenv("RD_CORE_FLAG", raising=False)
    assert settings.get_bool("RD_CORE_FLAG") is False
    assert settings.get_bool("RD_CORE_FLAG", True) is True
    for value in ("true", "TRUE", "True"):
        monkeypatch.setenv("RD_CORE_FLAG", value)
        assert settings.get_bool("RD_CORE_FLAG") is True
    for value in ("", "1", "yes", "false", " true"):
        monkeypatch.setenv("RD_CORE_FLAG", value)
        assert settings.get_bool("RD_CORE_FLAG", True) is False


def test_model_name_prefers_new_name_then_legacy_then_default(monkeypatch):
    for name in ("LLM_MODEL_PHASE2", "OPENAI_MODEL_PHASE2"):
        monkeypatch.delenv(name, raising=False)
    assert settings.model_name("PHASE2", "gpt-6-luna") == "gpt-6-luna"

    monkeypatch.setenv("OPENAI_MODEL_PHASE2", "gpt-5.4")  # legacy name still honored
    assert settings.model_name("PHASE2", "gpt-6-luna") == "gpt-5.4"

    monkeypatch.setenv("LLM_MODEL_PHASE2", "")  # empty counts as unset
    assert settings.model_name("PHASE2", "gpt-6-luna") == "gpt-5.4"

    monkeypatch.setenv("LLM_MODEL_PHASE2", "claude-haiku-5-5")
    assert settings.model_name("PHASE2", "gpt-6-luna") == "claude-haiku-5-5"
    assert settings.model_name("phase2", "gpt-6-luna") == "claude-haiku-5-5"


# ── shared values ────────────────────────────────────────────────────────────

_SHARED = ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "SUPABASE_DB_URL", "STORAGE_BASE_DIR",
           "KRX_CSV_PATH", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")


def test_shared_values_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "eyJtest")
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://user@host:6543/postgres")
    monkeypatch.setenv("STORAGE_BASE_DIR", "/tmp/reports")
    monkeypatch.setenv("KRX_CSV_PATH", "data/krx.csv")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    assert settings.supabase_url() == "https://test.supabase.co"
    assert settings.supabase_service_key() == "eyJtest"
    assert settings.supabase_db_url() == "postgresql://user@host:6543/postgres"
    assert settings.storage_base_dir() == Path("/tmp/reports")
    assert settings.krx_csv_path() == Path("data/krx.csv")
    assert settings.openai_api_key() == "sk-test"
    assert settings.anthropic_api_key() == "sk-ant-test"


_SECRETS = ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "SUPABASE_DB_URL",
            "OPENAI_API_KEY", "ANTHROPIC_API_KEY")


def _secret_values():
    return [settings.supabase_url(), settings.supabase_service_key(), settings.supabase_db_url(),
            settings.openai_api_key(), settings.anthropic_api_key()]


def test_shared_value_defaults(monkeypatch):
    for name in _SHARED:
        monkeypatch.delenv(name, raising=False)
    assert settings.storage_base_dir() == Path("./reports")
    assert settings.krx_csv_path() == Path("docs/stock_data/KRX_stocks_data.csv")
    assert _secret_values() == [None] * len(_SECRETS)

    for name in _SECRETS:  # an empty value counts as not set
        monkeypatch.setenv(name, "")
    assert _secret_values() == [None] * len(_SECRETS)


def test_web_values_need_no_ai_key_telegram_or_db_url(monkeypatch):
    """The web reads its shared values without OPENAI_API_KEY / TELEGRAM_* / SUPABASE_DB_URL."""
    monkeypatch.setenv("SUPABASE_URL", "u")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k")
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "TELEGRAM_API_ID", "TELEGRAM_API_HASH",
                 "TELEGRAM_CHANNEL", "SUPABASE_DB_URL", "STORAGE_BASE_DIR", "KRX_CSV_PATH"):
        monkeypatch.delenv(name, raising=False)

    assert settings.supabase_url() == "u"
    assert settings.supabase_service_key() == "k"
    assert settings.storage_base_dir() == Path("./reports")
    assert settings.krx_csv_path() == Path("docs/stock_data/KRX_stocks_data.csv")


# ── NotReady ─────────────────────────────────────────────────────────────────

def test_not_ready_carries_area_and_reason():
    reason = "ANTHROPIC_API_KEY가 설정되지 않았습니다. .env에 추가 후 분석 다시 시도하세요."
    exc = NotReady("분석", reason)
    assert exc.area == "분석"
    assert exc.reason == reason
    assert str(exc) == f"분석 기능을 지금 쓸 수 없습니다: {reason}"
    assert str(pickle.loads(pickle.dumps(exc))) == str(exc)
    # Not a RuntimeError, so ``except RuntimeError`` around a call never hides it.
    assert not isinstance(exc, RuntimeError)
    with pytest.raises(NotReady):
        raise exc


# ── nothing is read at import time ───────────────────────────────────────────

_ENV_READS = {"getenv", "load_dotenv", "load_env", "required", "optional", "get_int",
              "get_float", "get_bool", "model_name", "supabase_url", "supabase_service_key",
              "supabase_db_url", "storage_base_dir", "krx_csv_path", "openai_api_key",
              "anthropic_api_key"}


def _import_time_nodes(tree: ast.Module):
    """Code that runs on import: module and class bodies, decorators, defaults."""
    pending = list(tree.body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            pending.extend(node.decorator_list)
            pending.extend(node.args.defaults + [d for d in node.args.kw_defaults if d])
        elif isinstance(node, ast.ClassDef):
            pending.extend(node.decorator_list + node.bases + node.body)
        else:
            yield node


def test_core_modules_read_no_settings_at_import():
    core = Path(settings.__file__).resolve().parent
    offenders = []
    for path in sorted(core.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in _import_time_nodes(tree):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Attribute) and sub.attr == "environ":
                    offenders.append(f"{path.name}:{sub.lineno}")
                if isinstance(sub, ast.Call):
                    func = sub.func
                    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                    if name in _ENV_READS:
                        offenders.append(f"{path.name}:{sub.lineno}")
    assert offenders == []
