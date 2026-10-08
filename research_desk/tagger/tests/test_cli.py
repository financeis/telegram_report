"""Smoke tests for the ``tag`` command's argparse layer.

Ported from langgraph_tagger/tests/test_cli.py. Covers regression class for
Issue #1 (cli.py post-parse AttributeError when inspect/escalate subparsers
don't declare --batch-size). The top-level ``research_desk --help`` test
belongs to the command entry point (research_desk/cli.py).
"""
from __future__ import annotations

import argparse

import pytest

from research_desk.tagger.cli import register


def main(argv: list[str]) -> int:
    """What ``python -m research_desk`` does with the ``tag`` command."""
    parser = argparse.ArgumentParser(prog="research_desk")
    register(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(argv)
    return args.func(args)


def test_tag_help_runs_without_error(capsys):
    """`tag --help` exits cleanly via argparse SystemExit (code 0)."""
    with pytest.raises(SystemExit) as excinfo:
        main(["tag", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    for command in ("run", "inspect", "escalate", "reset-worker"):
        assert command in out


def test_main_inspect_runs_past_argparse_to_load_config(monkeypatch):
    """`inspect` subcommand must reach load_config (which raises RuntimeError
    when env is missing). This catches regression of Issue #1: a previous
    `args.batch_size = ...` LHS access without getattr() raised AttributeError
    BEFORE load_config was reached.
    """
    # Wipe the env vars load_config requires, so it raises RuntimeError.
    # (.env reading is off for every test: research_desk/conftest.py.)
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(RuntimeError) as excinfo:
        main(["tag", "inspect"])
    # load_config raises "<NAME> is required" — confirms we're past argparse.
    assert "is required" in str(excinfo.value)


def test_main_escalate_requires_since(capsys):
    """`escalate` without --since must fail at argparse (SystemExit code 2)."""
    with pytest.raises(SystemExit) as excinfo:
        main(["tag", "escalate"])
    # argparse exits with code 2 on usage errors.
    assert excinfo.value.code == 2
