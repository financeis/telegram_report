"""The ``tag`` command: argparse layer, start-up checks, reports (spec §5, §7, §8, §10).

Ported from langgraph_tagger/tests/test_cli.py: the Issue #1 regression
(inspect / escalate must parse without --batch-size) and escalate's required
--since. Changed per spec §10 item 3: a missing setting is no longer an
exception (exit 1) but a message on stderr and exit code 4. The top-level
``research_desk --help`` test belongs to research_desk/cli.py.

No test reaches a DB, an AI provider or the codex CLI: the DB pool, run_batch
and the codex lookup are replaced, and the ``tagger_env`` fixture unsets every
variable the command reads before each test sets its own.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from research_desk.core import db, llm
from research_desk.domain import stocks
from research_desk.domain.stocks import StockList, StockListError
from research_desk.tagger import cli
from research_desk.tagger.cli import register
from research_desk.tagger.sql import ESCALATION_PICK_SQL, INSPECT_SUMMARY_SQL, RESET_WORKER_SQL
from research_desk.tagger.tests.conftest import BUNDLED_CSV

BUNDLED_VERSION = "KRX@2026-05-08"

# spec §8, word for word.
VERSION_GUIDANCE = (
    "종목표 파일 내용이 버전 정보와 다릅니다. 종목표를 바꿨다면 "
    "python -m research_desk stocks set-version --as-of <자료 기준일, YYYY-MM-DD>를 "
    "실행한 뒤 다시 시작하세요."
)

SMALL_CSV = (
    "종목코드,종목명,시장,산업명(대),산업명(중),주요제품\n"
    "005930,삼성전자,KOSPI,반도체,메모리반도체,DRAM/NAND\n"
    "000660,SK하이닉스,KOSPI,반도체,메모리반도체,DRAM/NAND\n"
)

SINCE = "2026-05-08T09:00"

# The commands that tag rows: they check the model key, the DB URL and the stock list.
TAGGING = {
    "run": ["tag", "run"],
    "run-dry-run": ["tag", "run", "--dry-run"],
    "run-row-ids": ["tag", "run", "--row-ids", "1,2"],
    "escalate": ["tag", "escalate", "--since", SINCE],
}
# The commands that only read or reset the queue: they check the DB URL only.
QUEUE_ONLY = {
    "inspect": ["tag", "inspect"],
    "reset-worker": ["tag", "reset-worker", "--worker-id", "w-1"],
}


def main(argv: list[str]) -> int:
    """What ``python -m research_desk`` does with the ``tag`` command."""
    parser = argparse.ArgumentParser(prog="research_desk")
    register(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(argv)
    return args.func(args)


class FakeDB:
    """Stands in for core.db.SupabaseSQL; every call lands in ``backend.calls``."""

    def __init__(self, backend):
        self._backend = backend

    async def fetch(self, sql, args=()):
        self._backend.calls.append(("fetch", sql, list(args)))
        return self._backend.fetch_rows.pop(0) if self._backend.fetch_rows else []

    async def execute(self, sql, args=()):
        self._backend.calls.append(("execute", sql, list(args)))

    async def close(self):
        self._backend.calls.append(("close",))


class RecordingClient(llm.LLMClient):
    """The real LLMClient (it makes no call until parse()); remembers its arguments."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.init_kwargs = kwargs


@pytest.fixture
def backend(monkeypatch):
    """Replaces every way to the DB and the batch runner.

    ``calls`` records each DB function and run_batch call; ``fetch_rows`` is
    what the next fetch() calls return; ``run_kwargs`` is what run_batch got.
    """
    state = SimpleNamespace(
        calls=[], fetch_rows=[], run_kwargs=None,
        report={"model": "m", "processed": 1, "auto": 1, "review_needed": 0},
    )

    async def from_env(*, max_size):
        state.calls.append(("from_env", max_size))
        return FakeDB(state)

    async def no_pool(*args, **kwargs):
        state.calls.append(("create_pool", args, kwargs))
        raise AssertionError("tests never open a real DB pool")

    async def fake_run_batch(**kwargs):
        state.calls.append(("run_batch",))
        state.run_kwargs = kwargs
        return dict(state.report)

    monkeypatch.setattr(db.SupabaseSQL, "from_env", staticmethod(from_env))
    monkeypatch.setattr(db, "create_pool", no_pool)
    monkeypatch.setattr(db.asyncpg, "create_pool", no_pool)
    monkeypatch.setattr(cli, "run_batch", fake_run_batch)
    monkeypatch.setattr(llm, "LLMClient", RecordingClient)
    return state


@pytest.fixture
def env(tagger_env):
    """Ready to tag: DB URL, both API keys, the bundled stock list (absolute path)."""
    tagger_env.setenv("SUPABASE_DB_URL", "postgresql://tester@127.0.0.1:1/never")
    tagger_env.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    tagger_env.setenv("OPENAI_API_KEY", "sk-openai-test")
    tagger_env.setenv("KRX_CSV_PATH", str(BUNDLED_CSV))
    return tagger_env


@pytest.fixture
def no_codex(monkeypatch):
    """No codex CLI on PATH (CODEX_BIN is unset by tagger_env)."""
    monkeypatch.setattr(shutil, "which", lambda *args, **kwargs: None)


def small_csv(tmp_path, text=SMALL_CSV):
    path = tmp_path / "stocks.csv"
    path.write_text(text, encoding="utf-8")
    return path


def not_ready(capsys, backend, expected_err):
    """Exit 4 happened before any DB function: only the message, on stderr."""
    out, err = capsys.readouterr()
    assert err.strip() == expected_err
    assert out == ""
    assert backend.calls == []


# ── argparse layer ───────────────────────────────────────────────────────────

def test_tag_help_runs_without_error(capsys):
    """`tag --help` exits cleanly via argparse SystemExit (code 0)."""
    with pytest.raises(SystemExit) as excinfo:
        main(["tag", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for command in ("run", "inspect", "escalate", "reset-worker"):
        assert command in out


@pytest.mark.parametrize("command", ["run", "inspect", "escalate", "reset-worker"])
def test_subcommand_help_exits_0(command, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["tag", command, "--help"])
    assert excinfo.value.code == 0
    assert command in capsys.readouterr().out


def test_run_flags_are_unchanged(capsys):
    with pytest.raises(SystemExit):
        main(["tag", "run", "--help"])
    out = capsys.readouterr().out
    for flag in ("--batch-size", "--model", "--dry-run", "--row-ids",
                 "--max-concurrent-llm", "--worker-id"):
        assert flag in out


@pytest.mark.parametrize("argv", [
    ["tag"],                                    # no subcommand
    ["tag", "run", "--bogus"],                  # unknown flag
    ["tag", "run", "--batch-size", "ten"],      # not an int
    ["tag", "reset-worker"],                    # --worker-id is required
    ["tag", "inspect", "--batch-size", "5"],    # inspect takes no flags
])
def test_usage_errors_exit_2(argv, backend):
    with pytest.raises(SystemExit) as excinfo:
        main(argv)
    assert excinfo.value.code == 2
    assert backend.calls == []


def test_main_escalate_requires_since(capsys):
    """`escalate` without --since must fail at argparse (SystemExit code 2)."""
    with pytest.raises(SystemExit) as excinfo:
        main(["tag", "escalate"])
    # argparse exits with code 2 on usage errors.
    assert excinfo.value.code == 2


def test_main_inspect_runs_past_argparse_to_the_settings_check(tagger_env, backend, capsys):
    """Was test_main_inspect_runs_past_argparse_to_load_config (RuntimeError).

    Issue #1: `inspect` must get past argparse (no --batch-size access) to the
    settings check. spec §10 item 3: the missing setting is now reported on
    stderr with exit code 4 instead of an exception.
    """
    assert main(["tag", "inspect"]) == 4
    not_ready(capsys, backend, "SUPABASE_DB_URL is required")


# ── start-up checks: exit 4 before any DB call (spec §5, §8, §10 items 2-3) ──

@pytest.mark.parametrize("argv,key", [
    (TAGGING["run"], "ANTHROPIC_API_KEY"),            # default model claude-haiku-5-5
    (TAGGING["run-dry-run"], "ANTHROPIC_API_KEY"),
    (TAGGING["run-row-ids"], "ANTHROPIC_API_KEY"),
    (TAGGING["escalate"], "OPENAI_API_KEY"),          # escalation model gpt-5.4
    (["tag", "run", "--model", "gpt-5.4-mini"], "OPENAI_API_KEY"),
    (["tag", "escalate", "--since", SINCE, "--model", "claude-haiku-5-5"], "ANTHROPIC_API_KEY"),
])
@pytest.mark.parametrize("value", [None, ""])
def test_missing_model_key_exits_4(env, backend, capsys, argv, key, value):
    if value is None:
        env.delenv(key)
    else:
        env.setenv(key, value)
    assert main(argv) == 4
    not_ready(capsys, backend, f"{key} is required")


@pytest.mark.parametrize("argv", [*TAGGING.values(), *QUEUE_ONLY.values()],
                         ids=[*TAGGING, *QUEUE_ONLY])
@pytest.mark.parametrize("value", [None, ""])
def test_missing_db_url_exits_4(env, backend, capsys, argv, value):
    if value is None:
        env.delenv("SUPABASE_DB_URL")
    else:
        env.setenv("SUPABASE_DB_URL", value)
    assert main(argv) == 4
    not_ready(capsys, backend, "SUPABASE_DB_URL is required")


@pytest.mark.parametrize("argv", [
    ["tag", "run", "--model", "codex:gpt-6-luna"],
    ["tag", "escalate", "--since", SINCE, "--model", "codex:gpt-6-luna"],
])
def test_codex_model_without_the_cli_exits_4(env, backend, capsys, no_codex, argv):
    assert main(argv) == 4
    not_ready(capsys, backend, "codex CLI not found for model codex:gpt-6-luna")


def test_codex_model_set_in_env_is_checked_too(env, backend, capsys, no_codex):
    env.setenv("LLM_MODEL_ESCALATION", "codex:gpt-6-luna")
    assert main(TAGGING["escalate"]) == 4
    not_ready(capsys, backend, "codex CLI not found for model codex:gpt-6-luna")


@pytest.mark.parametrize("argv", TAGGING.values(), ids=TAGGING)
def test_missing_stock_list_exits_4(env, backend, capsys, tmp_path, argv):
    missing = tmp_path / "missing.csv"
    env.setenv("KRX_CSV_PATH", str(missing))
    with pytest.raises(StockListError) as reason:
        StockList.load(missing)
    assert main(argv) == 4
    not_ready(capsys, backend, f"종목표 파일을 읽을 수 없습니다: {reason.value}")


@pytest.mark.parametrize("argv", TAGGING.values(), ids=TAGGING)
@pytest.mark.parametrize("case", ["bad-header", "folder", "not-utf8"])
def test_unreadable_stock_list_exits_4(env, backend, capsys, tmp_path, argv, case):
    if case == "bad-header":
        path = small_csv(tmp_path, SMALL_CSV.replace("종목코드", "코드"))
    elif case == "folder":
        path = tmp_path
    else:
        path = tmp_path / "cp949.csv"
        path.write_bytes(SMALL_CSV.encode("cp949"))
    env.setenv("KRX_CSV_PATH", str(path))
    with pytest.raises(StockListError) as reason:
        StockList.load(path)
    assert main(argv) == 4
    not_ready(capsys, backend, f"종목표 파일을 읽을 수 없습니다: {reason.value}")


@pytest.mark.parametrize("argv", TAGGING.values(), ids=TAGGING)
def test_missing_version_file_exits_4(env, backend, capsys, tmp_path, argv):
    env.setenv("KRX_CSV_PATH", str(small_csv(tmp_path)))
    assert main(argv) == 4
    not_ready(capsys, backend, VERSION_GUIDANCE)


@pytest.mark.parametrize("argv", TAGGING.values(), ids=TAGGING)
def test_changed_stock_list_exits_4(env, backend, capsys, tmp_path, argv):
    path = small_csv(tmp_path)
    stocks.write_version(path, "2026-05-08")
    path.write_text(SMALL_CSV.replace("SK하이닉스", "SK하이닉스2"), encoding="utf-8")
    env.setenv("KRX_CSV_PATH", str(path))
    assert main(argv) == 4
    not_ready(capsys, backend, VERSION_GUIDANCE)


@pytest.mark.parametrize("content", ["not json", '{"version": "KRX@2026-05-08"}'])
def test_invalid_version_file_exits_4(env, backend, capsys, tmp_path, content):
    path = small_csv(tmp_path)
    stocks.version_file(path).write_text(content, encoding="utf-8")
    env.setenv("KRX_CSV_PATH", str(path))
    assert main(TAGGING["run"]) == 4
    not_ready(capsys, backend, VERSION_GUIDANCE)


# ── which settings each command needs (spec §10 item 3) ─────────────────────

@pytest.mark.parametrize("argv", QUEUE_ONLY.values(), ids=QUEUE_ONLY)
def test_inspect_and_reset_worker_need_only_the_db_url(tagger_env, backend, no_codex, tmp_path, argv):
    tagger_env.setenv("SUPABASE_DB_URL", "postgresql://tester@127.0.0.1:1/never")
    # No API key, a codex model without the CLI, no stock list: none of it is read.
    tagger_env.setenv("LLM_MODEL_DEFAULT", "codex:gpt-6-luna")
    tagger_env.setenv("KRX_CSV_PATH", str(tmp_path / "missing.csv"))
    assert main(argv) == 0
    assert backend.calls[0] == ("from_env", 10)


def test_run_needs_only_the_key_of_the_model_it_uses(env, backend):
    env.delenv("OPENAI_API_KEY")
    assert main(TAGGING["run"]) == 0
    assert backend.run_kwargs["model"] == "claude-haiku-5-5"


def test_escalate_needs_only_the_key_of_the_escalation_model(env, backend):
    backend.fetch_rows = [[{"id": 1}]]
    env.delenv("ANTHROPIC_API_KEY")
    assert main(TAGGING["escalate"]) == 0
    assert backend.run_kwargs["model"] == "gpt-5.4"


def test_model_flag_decides_which_key_is_needed(env, backend):
    env.delenv("ANTHROPIC_API_KEY")
    assert main(["tag", "run", "--model", "gpt-5.4"]) == 0
    assert backend.run_kwargs["model"] == "gpt-5.4"


@pytest.mark.parametrize("name", ["LLM_MODEL_DEFAULT", "OPENAI_MODEL_DEFAULT"])
def test_model_setting_decides_which_key_is_needed(env, backend, name):
    env.setenv(name, "gpt-5.4-mini")
    env.delenv("ANTHROPIC_API_KEY")
    assert main(TAGGING["run"]) == 0
    assert backend.run_kwargs["model"] == "gpt-5.4-mini"


def test_codex_model_needs_the_cli_but_no_api_key(env, backend):
    env.delenv("ANTHROPIC_API_KEY")
    env.delenv("OPENAI_API_KEY")
    env.setenv("CODEX_BIN", "C:/tools/codex.cmd")
    assert main(["tag", "run", "--model", "codex:gpt-6-luna"]) == 0
    assert backend.run_kwargs["model"] == "codex:gpt-6-luna"


def test_commands_read_dotenv_when_they_start(env, backend, env_file):
    """spec §7: .env is (re)read when a command starts; existing values win."""
    env.delenv("SUPABASE_DB_URL")
    env_file.write_text("SUPABASE_DB_URL=postgresql://from-dotenv@127.0.0.1:1/never\n",
                        encoding="utf-8")
    assert main(["tag", "inspect"]) == 0
    assert os.environ["SUPABASE_DB_URL"] == "postgresql://from-dotenv@127.0.0.1:1/never"


# ── run ──────────────────────────────────────────────────────────────────────

def test_run_defaults(env, backend, capsys):
    assert main(TAGGING["run"]) == 0
    kwargs = backend.run_kwargs
    assert kwargs["max_concurrent_llm"] == 2          # spec §10 item 4 (was 10)
    assert kwargs["batch_size"] == 10
    assert kwargs["lock_ttl_minutes"] == 30
    assert kwargs["per_row_deadline_s"] == 90.0
    assert kwargs["model"] == "claude-haiku-5-5"
    assert kwargs["dry_run"] is False
    assert kwargs["row_ids"] == []
    host = kwargs["worker_id"].rsplit("-", 2)
    assert len(host) == 3 and host[1] == str(os.getpid()) and len(host[2]) == 4
    # One pool (max 10 connections), closed at the end.
    assert backend.calls[0] == ("from_env", 10)
    assert backend.calls[-1] == ("close",)


def test_run_uses_the_bundled_stock_list_and_its_version(env, backend):
    assert main(TAGGING["run"]) == 0
    krx = backend.run_kwargs["krx"]
    assert isinstance(krx, StockList)
    assert krx.path == BUNDLED_CSV
    assert backend.run_kwargs["taxonomy_version"] == BUNDLED_VERSION


@pytest.mark.parametrize("argv", [TAGGING["run"], TAGGING["escalate"]])
def test_taxonomy_version_is_the_version_file_value_not_the_file_date(env, backend, tmp_path, argv):
    """spec §8: taxonomy_version = the version file's "version"; the file date is not used."""
    backend.fetch_rows = [[{"id": 5}]]
    path = small_csv(tmp_path)
    assert stocks.write_version(path, "2031-02-03") == "KRX@2031-02-03"
    old = (datetime(2020, 1, 1) - datetime(1970, 1, 1)).total_seconds()
    os.utime(path, (old, old))
    env.setenv("KRX_CSV_PATH", str(path))
    assert main(argv) == 0
    assert backend.run_kwargs["taxonomy_version"] == "KRX@2031-02-03"


def test_run_settings_from_the_environment(env, backend):
    env.setenv("MAX_CONCURRENT_LLM", "3")
    env.setenv("TAGGER_BATCH_SIZE_DEFAULT", "7")
    env.setenv("LOCK_TTL_MINUTES", "45")
    env.setenv("PER_ROW_DEADLINE_S", "12.5")
    env.setenv("LLM_MODEL_DEFAULT", "claude-sonnet-5")
    assert main(TAGGING["run"]) == 0
    kwargs = backend.run_kwargs
    assert (kwargs["max_concurrent_llm"], kwargs["batch_size"]) == (3, 7)
    assert (kwargs["lock_ttl_minutes"], kwargs["per_row_deadline_s"]) == (45, 12.5)
    assert kwargs["model"] == "claude-sonnet-5"


def test_run_flags_win_over_settings(env, backend, capsys):
    env.setenv("MAX_CONCURRENT_LLM", "3")
    env.setenv("TAGGER_BATCH_SIZE_DEFAULT", "7")
    argv = ["tag", "run", "--batch-size", "4", "--max-concurrent-llm", "1", "--dry-run",
            "--row-ids", "5, 6,,7", "--worker-id", "w-1", "--model", "claude-x"]
    assert main(argv) == 0
    kwargs = backend.run_kwargs
    assert (kwargs["batch_size"], kwargs["max_concurrent_llm"]) == (4, 1)
    assert kwargs["dry_run"] is True
    assert kwargs["row_ids"] == [5, 6, 7]
    assert kwargs["worker_id"] == "w-1"
    assert kwargs["model"] == "claude-x"


def test_zero_flags_fall_back_to_settings(env, backend):
    """As before: 0 is not a usable batch size / concurrency, so the setting applies."""
    assert main(["tag", "run", "--batch-size", "0", "--max-concurrent-llm", "0"]) == 0
    assert (backend.run_kwargs["batch_size"], backend.run_kwargs["max_concurrent_llm"]) == (10, 2)


def test_run_prints_the_batch_report_with_the_worker_id(env, backend, capsys):
    backend.report = {"model": "m", "processed": 2, "note": "한글", "when": datetime(2026, 5, 8)}
    assert main(["tag", "run", "--worker-id", "w-7"]) == 0
    expected = {**backend.report, "worker_id": "w-7"}
    assert capsys.readouterr().out == json.dumps(expected, indent=2, ensure_ascii=False,
                                                 default=str) + "\n"


def test_run_ai_client_settings(env, backend):
    """spec §9.3: SDK retries 2, request timeout 60 s, keys from the settings."""
    assert main(TAGGING["run"]) == 0
    client = backend.run_kwargs["client"]
    assert isinstance(client, RecordingClient)
    assert client.init_kwargs == {
        "openai_api_key": "sk-openai-test", "anthropic_api_key": "sk-ant-test",
        "max_retries": 2, "timeout": 60.0,
    }


def test_run_batch_is_the_orchestrators_loaded_on_the_first_call(monkeypatch):
    """The entry imports cli for every command; the row graph loads only when rows are tagged."""
    from research_desk.tagger import orchestrator

    got = {}

    async def fake_run_batch(**kwargs):
        got.update(kwargs)
        return {"processed": 3}

    monkeypatch.setattr(orchestrator, "run_batch", fake_run_batch)
    assert asyncio.run(cli.run_batch(batch_size=3, dry_run=True)) == {"processed": 3}
    assert got == {"batch_size": 3, "dry_run": True}


@pytest.mark.parametrize("name,argv", [
    ("MAX_CONCURRENT_LLM", TAGGING["run"]),
    ("MAX_CONCURRENT_LLM", ["tag", "run", "--max-concurrent-llm", "2"]),   # read even when overridden
    ("TAGGER_BATCH_SIZE_DEFAULT", ["tag", "run", "--batch-size", "5"]),
    ("LOCK_TTL_MINUTES", TAGGING["escalate"]),
    ("PER_ROW_DEADLINE_S", TAGGING["run"]),
])
def test_malformed_number_setting_is_an_error_before_any_db_call(env, backend, name, argv):
    """Not a §5 start-up check: as before it raises (exit 1), but before the DB."""
    env.setenv(name, "two")
    with pytest.raises(ValueError, match=name):
        main(argv)
    assert backend.calls == []


# ── escalate ─────────────────────────────────────────────────────────────────

def test_escalate_without_rows_prints_zero(env, backend, capsys):
    assert main(TAGGING["escalate"]) == 0
    assert capsys.readouterr().out == '{"escalated": 0, "since": "2026-05-08T09:00"}\n'
    assert backend.calls == [
        ("from_env", 10),
        ("fetch", ESCALATION_PICK_SQL, [datetime(2026, 5, 8, 9, 0, tzinfo=timezone.utc)]),
        ("close",),
    ]


def test_escalate_retags_the_picked_rows(env, backend, capsys):
    backend.fetch_rows = [[{"id": 3}, {"id": 9}]]
    since = "2026-05-08T09:00+09:00"
    assert main(["tag", "escalate", "--since", since]) == 0
    assert backend.calls[1] == (
        "fetch", ESCALATION_PICK_SQL, [datetime(2026, 5, 8, 9, 0, tzinfo=timezone(timedelta(hours=9)))])
    kwargs = backend.run_kwargs
    assert kwargs["row_ids"] == [3, 9]
    assert kwargs["batch_size"] == 2
    assert kwargs["dry_run"] is False
    assert kwargs["model"] == "gpt-5.4"
    assert kwargs["max_concurrent_llm"] == 2
    assert kwargs["taxonomy_version"] == BUNDLED_VERSION
    assert kwargs["worker_id"]
    # escalate's report has no worker_id key (as before)
    out = capsys.readouterr().out
    assert json.loads(out) == backend.report
    assert out == json.dumps(backend.report, indent=2, ensure_ascii=False, default=str) + "\n"


def test_escalate_flags(env, backend):
    backend.fetch_rows = [[{"id": 1}], [{"id": 1}]]     # one pick per call
    env.setenv("LLM_MODEL_ESCALATION", "gpt-6")
    assert main(["tag", "escalate", "--since", SINCE, "--max-concurrent-llm", "1"]) == 0
    assert backend.run_kwargs["model"] == "gpt-6"
    assert backend.run_kwargs["max_concurrent_llm"] == 1
    assert main(["tag", "escalate", "--since", SINCE, "--model", "gpt-5.4-mini"]) == 0
    assert backend.run_kwargs["model"] == "gpt-5.4-mini"


# ── inspect / reset-worker ───────────────────────────────────────────────────

def test_inspect_prints_the_queue_summary(env, backend, capsys):
    summary = {"pending": 3, "processing": 0, "auto": 10, "review_needed": 1,
               "verified": 2, "oos_total": 4, "last_24h": 5}
    backend.fetch_rows = [[summary]]
    assert main(QUEUE_ONLY["inspect"]) == 0
    assert capsys.readouterr().out == json.dumps(summary, indent=2) + "\n"
    assert backend.calls == [("from_env", 10), ("fetch", INSPECT_SUMMARY_SQL, []), ("close",)]


def test_inspect_without_a_row_prints_an_empty_object(env, backend, capsys):
    assert main(QUEUE_ONLY["inspect"]) == 0
    assert capsys.readouterr().out == "{}\n"


def test_reset_worker_reports_the_reverted_ids(env, backend, capsys):
    backend.fetch_rows = [[{"id": 4}, {"id": 8}]]
    assert main(["tag", "reset-worker", "--worker-id", "host-1-ab12"]) == 0
    expected = {"worker_id": "host-1-ab12", "reset_count": 2, "ids": [4, 8]}
    assert capsys.readouterr().out == json.dumps(expected, indent=2) + "\n"
    assert backend.calls == [
        ("from_env", 10), ("fetch", RESET_WORKER_SQL, ["host-1-ab12"]), ("close",),
    ]
