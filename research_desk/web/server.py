"""The ``web`` command: ``python -m research_desk web [--view reports|market|review]`` (spec §5).

``register(subparsers)`` adds the command; parsing sets ``func=serve``. ``serve(args)`` prints
``Research Desk: http://127.0.0.1:8520/?view=<view>``, then serves the app of
``app.create_app()`` on 127.0.0.1:8520 until it is stopped, and returns 0. The view is the
screen's own ``?view=`` (default ``reports``); it replaces the old entry points
``python -m langgraph_tagger.workspace`` / ``.analytics`` / ``.review_viewer``.

FastAPI, uvicorn and the features load only when the command runs, so the other commands never
need the web packages (``requirements-workspace.txt``).
"""
from __future__ import annotations

import argparse

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
    """Print the address, then serve the app on 127.0.0.1:8520 until stopped. Returns 0."""
    import uvicorn

    from .app import create_app

    print(f'Research Desk: http://{HOST}:{PORT}/?view={args.view}', flush=True)
    uvicorn.run(create_app(), host=HOST, port=PORT)
    return 0
