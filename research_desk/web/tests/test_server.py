"""web.server: the ``web`` command (spec §5, §10 item 11).

The three old entry points (workspace, analytics, review_viewer) became ``web --view
reports|market|review``. ``uvicorn.run`` is replaced in every test: no server starts. The command
through ``python -m research_desk`` is tested in research_desk/tests/test_cli.py.
"""
from __future__ import annotations

import argparse
import os

import pytest
import uvicorn
from fastapi.testclient import TestClient

from research_desk.web import app as app_module
from research_desk.web import server
from research_desk.web.server import register, serve

from .fakes import INDEX_HTML


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog='research_desk')
    register(parser.add_subparsers(dest='command', required=True))
    return parser.parse_args(argv)


@pytest.fixture
def served(monkeypatch, capsys) -> list:
    """``uvicorn.run`` replaced: each call is kept with what had been printed before it."""
    calls = []

    def run(app, **options):
        calls.append((app, options, capsys.readouterr().out))

    monkeypatch.setattr(uvicorn, 'run', run)
    return calls


def test_register_adds_web_with_the_three_views():
    assert parse(['web']) == argparse.Namespace(command='web', view='reports', func=serve)
    for view in ('reports', 'market', 'review'):
        assert parse(['web', '--view', view]).view == view
    with pytest.raises(SystemExit) as excinfo:
        parse(['web', '--view', 'analytics'])
    assert excinfo.value.code == 2


@pytest.mark.parametrize('view', ['reports', 'market', 'review'])
def test_serve_prints_the_address_then_serves_the_app_on_127_0_0_1_8520(served, view, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, 'DEFAULT_DIST', tmp_path / 'no-dist')
    assert serve(parse(['web', '--view', view])) == 0
    [(app, options, printed_before)] = served
    assert printed_before == f'Research Desk: http://127.0.0.1:8520/?view={view}\n'
    assert options == {'host': '127.0.0.1', 'port': 8520}
    assert (server.HOST, server.PORT) == ('127.0.0.1', 8520)


def test_serve_reads_dotenv_when_it_starts(env_file, monkeypatch, tmp_path):
    """spec §7: like every command, ``web`` re-reads ``.env`` when it starts, before serving."""
    monkeypatch.setattr(app_module, 'DEFAULT_DIST', tmp_path / 'no-dist')
    monkeypatch.delenv('RESEARCH_DESK_WEB_PROBE', raising=False)
    env_file.write_text('RESEARCH_DESK_WEB_PROBE=from-dotenv\n', encoding='utf-8')
    seen = []
    monkeypatch.setattr(uvicorn, 'run',
                        lambda app, **options: seen.append(os.environ.get('RESEARCH_DESK_WEB_PROBE')))
    assert serve(parse(['web'])) == 0
    assert seen == ['from-dotenv']


def test_serve_builds_the_app_with_the_default_screen_folder(served, monkeypatch, dist):
    monkeypatch.setattr(app_module, 'DEFAULT_DIST', dist)
    assert serve(parse(['web'])) == 0
    [(app, _, _)] = served
    with TestClient(app) as client:
        assert client.get('/').content == INDEX_HTML
        assert client.get('/api/health').json() == {'status': 'ok'}
