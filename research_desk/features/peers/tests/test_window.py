"""The peers window: what it binds and what importing it loads (spec §13)."""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from fastapi.routing import APIRoute

import research_desk
import research_desk.features.peers as peers

REPOSITORY = Path(research_desk.__file__).resolve().parent.parent
HEAVY = ('fastapi', 'starlette', 'uvicorn', 'pymongo', 'bson', 'openai', 'anthropic', 'supabase',
         'asyncpg', 'numpy', 'research_desk.features.reports', 'research_desk.features.coverage',
         'research_desk.features.prices', 'research_desk.features.peers.build',
         'research_desk.features.peers.router', 'research_desk.features.peers.service',
         'research_desk.core.llm', 'research_desk.core.db')


def test_the_window_binds_only_register_jobs_and_web_router():
    tree = ast.parse(Path(peers.__file__).read_text(encoding='utf-8'))
    bound = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            bound += [alias.asname or alias.name for alias in node.names]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.append(node.name)
        elif isinstance(node, (ast.Import, ast.Assign)):
            bound.append(type(node).__name__)
    assert bound == ['register_jobs', 'web_router']
    assert callable(peers.register_jobs)


def test_web_router_imports_the_router_only_when_called():
    tree = ast.parse(Path(peers.__file__).read_text(encoding='utf-8'))
    [function] = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'web_router']
    imports = [node for node in ast.walk(function) if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert [(node.level, node.module, [a.name for a in node.names]) for node in imports] == [(1, 'router', ['router'])]


def test_web_router_gives_the_two_addresses():
    router = peers.web_router()
    routes = sorted((route.path, tuple(sorted(route.methods)), route.name)
                    for route in router.routes if isinstance(route, APIRoute))
    assert routes == [('/api/peers/search', ('POST',), 'peers_search'),
                      ('/api/stocks/{code}/peers', ('GET',), 'stock_peers')]
    assert peers.web_router() is router


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


def test_calling_web_router_is_what_loads_fastapi_and_the_service():
    code = ("import sys\n"
            "from research_desk.features.peers import web_router\n"
            "before = 'fastapi' in sys.modules\n"
            "web_router()\n"
            "print(before, 'fastapi' in sys.modules, 'research_desk.features.peers.service' in sys.modules)\n")
    result = subprocess.run([sys.executable, "-c", code], cwd=REPOSITORY, capture_output=True,
                            text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False True True"
