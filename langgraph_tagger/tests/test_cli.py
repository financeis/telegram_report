"""Smoke tests for `python -m langgraph_tagger` CLI argparse layer.

Covers regression class for Issue #1 (cli.py post-parse AttributeError when
inspect/escalate subparsers don't declare --batch-size).
"""
from __future__ import annotations

import pytest

from langgraph_tagger.cli import main


def test_main_help_runs_without_error(capsys):
    """`--help` exits cleanly via argparse SystemExit (code 0)."""
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "langgraph_tagger" in out


def test_main_inspect_runs_past_argparse_to_load_config(monkeypatch):
    """`inspect` subcommand must reach load_config (which raises RuntimeError
    when env is missing). This catches regression of Issue #1: a previous
    `args.batch_size = ...` LHS access without getattr() raised AttributeError
    BEFORE load_config was reached.
    """
    # Wipe the env vars load_config requires, so it raises RuntimeError.
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Disable .env discovery so any local .env doesn't satisfy _req().
    monkeypatch.setattr("langgraph_tagger.config.load_dotenv", lambda *a, **k: False)

    with pytest.raises(RuntimeError) as excinfo:
        main(["inspect"])
    # load_config raises "<NAME> is required" — confirms we're past argparse.
    assert "is required" in str(excinfo.value)


def test_main_escalate_requires_since(capsys):
    """`escalate` without --since must fail at argparse (SystemExit code 2)."""
    with pytest.raises(SystemExit) as excinfo:
        main(["escalate"])
    # argparse exits with code 2 on usage errors.
    assert excinfo.value.code == 2
