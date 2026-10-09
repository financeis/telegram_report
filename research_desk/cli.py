"""Command list (명령어 목록): ``python -m research_desk <command>`` (spec §5).

Every command is registered here, in one place, by its area's registration function:

- ``collect``: ``research_desk.collector.cli.register``
- ``tag`` (run / inspect / escalate / reset-worker): ``research_desk.tagger.cli.register``
- ``web``: ``research_desk.web.server.register``
- ``stocks set-version --as-of YYYY-MM-DD``: ``register_stocks`` below (core settings and
  ``domain.stocks``)
- ``prices update [--codes …]``: the prices window's ``register_jobs``
- ``peers build [--fiscal-year N] [--codes …] [--limit n] [--pilot]`` / ``peers inspect``: the
  peers window's ``register_jobs``

The help lists them in that order: ``{collect,tag,web,stocks,prices,peers}``.

A new background command is its area's ``register`` function plus one line in ``build_parser``.
A feature's command comes through its window (``from research_desk.features.<feature> import
register_jobs``) plus that one line (spec §13). Such a window binds only names that load no
FastAPI; a web router it has is handed out by its ``web_router()`` when the web app is made.

Every command, ``--help`` and a run without a command import this module, and with it every
registration module and those windows. What a command needs when it runs is imported inside the
command function: ``python -m research_desk`` without a command loads neither FastAPI, uvicorn,
``research_desk.web.app``, LangGraph, pymongo nor the peers web side
(``research_desk.features.peers.router`` / ``.service``) — ``tests/test_cli.py`` checks it.

``main(argv)`` parses the arguments and returns the exit code of the command's ``func(args)``.
No command, an unknown command or bad arguments: argparse's usage message and exit code 2.
``--help`` on any command: 0. Exit code 4 means "not ready", a state that running again does not
fix (a missing setting, a stock list that does not match its version); ``tag``, ``stocks``,
``prices`` and ``peers`` use it.

``stocks set-version`` (spec §8) records the stock list's content hash under the version
``KRX@<as-of>``, the as-of date of the stock data. It re-reads ``.env``, takes ``KRX_CSV_PATH``
and calls ``domain.stocks.write_version``, which checks the date, then the header, hashes the
content and replaces the version file atomically. Success prints the new version on stdout and
returns 0. Any failure (a bad date such as ``2026-13-01`` or ``20260630``, a missing or unreadable
file, a wrong header) prints one Korean line with the reason on stderr and returns 4; the version
file stays as it was. Writing the same date again is allowed. The reason may name the file path:
command output is seen only in this PC's terminal.
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

from research_desk.collector.cli import register as register_collect
from research_desk.core import settings
from research_desk.domain.stocks import StockListError, write_version
from research_desk.features.peers import register_jobs as register_peers
from research_desk.features.prices import register_jobs as register_prices
from research_desk.tagger.cli import register as register_tag
from research_desk.web.server import register as register_web

PROG = 'research_desk'
EXIT_NOT_READY = 4
SET_VERSION_FAILED = '종목표 버전 정보를 쓰지 못했습니다. 버전 정보 파일은 그대로입니다. 이유: {reason}'


def register_stocks(subparsers) -> argparse.ArgumentParser:
    """Add ``stocks`` and its subcommand ``set-version``."""
    stocks = subparsers.add_parser('stocks', help='종목표 관리: set-version',
                                   description='종목표(KRX CSV) 관리')
    sub = stocks.add_subparsers(dest='stocks_command', required=True)
    description = ('종목표 파일 내용의 지문을 버전 KRX@<자료 기준일>로 버전 정보 파일에 기록합니다. '
                   '종목표를 바꾼 뒤 실행하세요.')
    set_version_parser = sub.add_parser('set-version', help=description, description=description)
    set_version_parser.add_argument('--as-of', required=True, metavar='YYYY-MM-DD',
                                    help='종목표 자료 기준일')
    set_version_parser.set_defaults(func=set_version)
    return stocks


def set_version(args: argparse.Namespace) -> int:
    """``stocks set-version``: write the version file of KRX_CSV_PATH; 0, or 4 with the reason."""
    settings.load_env()
    try:
        version = write_version(settings.krx_csv_path(), args.as_of)
    except StockListError as exc:
        print(SET_VERSION_FAILED.format(reason=exc), file=sys.stderr)
        return EXIT_NOT_READY
    print(version)
    return 0


def build_parser() -> argparse.ArgumentParser:
    """The ``research_desk`` parser with every command registered."""
    parser = argparse.ArgumentParser(prog=PROG, description='Research Desk 명령어')
    commands = parser.add_subparsers(dest='command', required=True)
    register_collect(commands)
    register_tag(commands)
    register_web(commands)
    register_stocks(commands)
    register_prices(commands)
    register_peers(commands)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the command in ``argv`` (default: the process arguments); return its exit code."""
    args = build_parser().parse_args(argv)
    return args.func(args)
