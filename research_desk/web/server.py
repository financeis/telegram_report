"""The ``web`` command: ``python -m research_desk web [--view reports|market|review]`` (spec §5).

``register(subparsers)`` adds the command; parsing sets ``func=serve``. ``serve(args)`` re-reads
``.env`` when it starts, like every command (spec §7; each feature reads it again when it prepares),
prints ``Research Desk: http://127.0.0.1:8520/?view=<view>``, then serves the app of
``app.create_app()`` on 127.0.0.1:8520 until it is stopped, and returns 0. The view is the
screen's own ``?view=`` (default ``reports``); it replaces the old entry points
``python -m langgraph_tagger.workspace`` / ``.analytics`` / ``.review_viewer``.

FastAPI, uvicorn and the app with its features (``app.py``) load only when the command runs:
``cli.py`` imports this module for every command, and ``python -m research_desk`` without a
command loads none of them (``tests/test_cli.py`` checks it). Running ``web`` needs the web
packages (``requirements-workspace.txt``), and so does running ``peers build``: it reads through
the reports feature's window, which loads FastAPI when the command runs.
"""
from __future__ import annotations

import argparse

from research_desk.core import settings

HOST = '127.0.0.1'
PORT = 8520
VIEWS = ('reports', 'market', 'review')
DEFAULT_VIEW = 'reports'
DESCRIPTION = 'Research Desk 웹앱을 127.0.0.1:8520에서 실행합니다.'


def register(subparsers) -> argparse.ArgumentParser:
    """Add the ``web`` command to ``subparsers``; its namespace carries ``func=serve``."""
    parser = subparsers.add_parser('web', help=DESCRIPTION, description=DESCRIPTION)
    parser.add_argument('--view', choices=VIEWS, default=DEFAULT_VIEW,
                        help='처음 보여 줄 화면 (기본: reports)')
    parser.set_defaults(func=serve)
    return parser


def serve(args: argparse.Namespace) -> int:
    """Re-read ``.env``, print the address, then serve the app on 127.0.0.1:8520 until stopped.
    Returns 0."""
    import uvicorn

    from .app import create_app

    settings.load_env()
    print(f'Research Desk: http://{HOST}:{PORT}/?view={args.view}', flush=True)
    uvicorn.run(create_app(), host=HOST, port=PORT)
    return 0
