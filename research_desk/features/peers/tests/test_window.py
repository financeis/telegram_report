"""The peers window: what it binds and what importing it loads (spec §13)."""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import research_desk
import research_desk.features.peers as peers

REPOSITORY = Path(research_desk.__file__).resolve().parent.parent
HEAVY = ('fastapi', 'starlette', 'uvicorn', 'pymongo', 'bson', 'openai', 'anthropic', 'supabase',
         'asyncpg', 'numpy', 'research_desk.features.reports', 'research_desk.features.peers.build',
         'research_desk.core.llm', 'research_desk.core.db')


def test_the_window_binds_only_register_jobs():
    tree = ast.parse(Path(peers.__file__).read_text(encoding='utf-8'))
    bound = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            bound += [alias.asname or alias.name for alias in node.names]
        elif isinstance(node, (ast.Import, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Assign)):
            bound.append(type(node).__name__)
    assert bound == ['register_jobs']
    assert callable(peers.register_jobs)


def test_importing_the_window_loads_no_web_db_mongo_or_ai_package():
    """Every command imports each window when it starts; the heavy parts load when a command runs."""
    code = ("import sys, research_desk.features.peers\n"
            f"print(sorted(m for m in {HEAVY!r} if m in sys.modules))\n")
    result = subprocess.run([sys.executable, "-c", code], cwd=REPOSITORY, capture_output=True,
                            text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def test_registering_the_commands_loads_nothing_heavy_either():
    code = ("import argparse, sys\n"
            "from research_desk.features.peers import register_jobs\n"
            "parser = argparse.ArgumentParser(prog='research_desk')\n"
            "register_jobs(parser.add_subparsers(dest='command'))\n"
            f"print(sorted(m for m in {HEAVY!r} if m in sys.modules))\n")
    result = subprocess.run([sys.executable, "-c", code], cwd=REPOSITORY, capture_output=True,
                            text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"
