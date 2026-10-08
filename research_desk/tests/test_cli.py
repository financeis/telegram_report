"""The command entry: ``python -m research_desk <command>`` (spec §5, §8, §10 item 1, §13 item 4).

Ported from langgraph_tagger/tests/test_cli.py:
- test_main_help_runs_without_error: ``--help`` exits 0 and the help names the program, now
  ``research_desk`` instead of ``langgraph_tagger`` (spec §10 item 1)

New:
- ``--help`` exits 0 on every command; no command, an unknown command and bad arguments are
  argparse's usage error: exit 2 with the usage, no command run;
- a command's exit code comes back from ``main`` (collect without settings: 1, tag inspect without
  SUPABASE_DB_URL: 4) and, through ``python -m research_desk`` in a fresh process, becomes the
  process's exit code; the commands load neither FastAPI nor uvicorn (only ``web`` does, when it
  runs);
- ``web``: the address line of the view, then the app of ``create_app()`` served on
  127.0.0.1:8520 (``uvicorn.run`` replaced: no server starts), exit 0;
- ``stocks set-version``: the new version on stdout and exit 0, the version file with that version
  and the CSV's content hash, the same date again, a changed stock list recorded afresh; a bad
  date, a wrong header, a missing or unreadable CSV: a Korean line with the reason on stderr,
  exit 4, the version file byte for byte as it was (or still absent); ``.env`` is read when it
  starts; without KRX_CSV_PATH the stock list is the one under the current folder.

Every setting the commands could read is deleted first, the current folder is a temp folder (so
no relative default reaches the real files), and the DB and the Telegram client refuse.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI

import research_desk
from research_desk.cli import main
from research_desk.collector import cli as collector_cli
from research_desk.core import db as core_db
from research_desk.domain.stocks import StockList, content_hash, version_file
from research_desk.web import app as web_app

REPOSITORY = Path(research_desk.__file__).resolve().parent.parent

ENV = ('TELEGRAM_API_ID', 'TELEGRAM_API_HASH', 'TELEGRAM_CHANNEL', 'TELEGRAM_CHANNEL_ID',
       'TELEGRAM_SESSION_NAME', 'SUPABASE_URL', 'SUPABASE_SERVICE_KEY', 'SUPABASE_DB_URL',
       'STORAGE_BASE_DIR', 'KRX_CSV_PATH', 'INITIAL_CUTOFF_DAYS', 'MAX_CONCURRENT_DOWNLOADS',
       'LOG_LEVEL', 'LLM_MODEL_DEFAULT', 'OPENAI_MODEL_DEFAULT', 'LLM_MODEL_ESCALATION',
       'OPENAI_MODEL_ESCALATION', 'LLM_MODEL_PHASE2', 'OPENAI_MODEL_PHASE2', 'ANTHROPIC_API_KEY',
       'OPENAI_API_KEY', 'CODEX_BIN', 'MAX_CONCURRENT_LLM', 'TAGGER_BATCH_SIZE_DEFAULT',
       'LOCK_TTL_MINUTES', 'PER_ROW_DEADLINE_S')

# The first header cell has a line break inside quotes, like the real file.
HEADER_LINE = '"종목\n코드",종목명,시장,산업명(대),산업명(중),주요제품\n'
ROWS = ('005930,삼성전자,KOSPI,반도체,메모리반도체,DRAM/NAND\n'
        '016360,삼성증권,KOSPI,금융,증권,증권\n')
FAILED = '종목표 버전 정보를 쓰지 못했습니다. 버전 정보 파일은 그대로입니다. 이유: '


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)

    async def no_pool(*args, **kwargs):
        raise AssertionError('no DB pool in the entry tests')

    def no_client(*args, **kwargs):
        raise AssertionError('no Supabase or Telegram client in the entry tests')

    monkeypatch.setattr(core_db, 'create_pool', no_pool)
    monkeypatch.setattr(core_db.SupabaseSQL, 'from_env', staticmethod(no_pool))
    monkeypatch.setattr(core_db, 'supabase_client', no_client)
    monkeypatch.setattr(collector_cli, 'TelegramClient', no_client)


def write_csv(path: Path, text: str = HEADER_LINE + ROWS) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode('utf-8'))
    return path


@pytest.fixture
def stock_csv(tmp_path, monkeypatch) -> Path:
    path = write_csv(tmp_path / 'stocks' / 'krx.csv')
    monkeypatch.setenv('KRX_CSV_PATH', str(path))
    return path


def set_version(as_of: str) -> int:
    return main(['stocks', 'set-version', '--as-of', as_of])


def recorded(csv_path: Path) -> dict:
    return json.loads(version_file(csv_path).read_text(encoding='utf-8'))


def folder_names(csv_path: Path) -> list[str]:
    return sorted(path.name for path in csv_path.parent.iterdir())


def run_python(*arguments: str) -> subprocess.CompletedProcess:
    """``python <arguments>`` in a fresh process, from the repository folder."""
    environment = dict(os.environ, PYTHONIOENCODING='utf-8')
    return subprocess.run([sys.executable, *arguments], cwd=REPOSITORY, env=environment,
                          capture_output=True, text=True, encoding='utf-8', timeout=300)


# ── help and usage errors ────────────────────────────────────────────────────

def test_main_help_runs_without_error(capsys):
    """`--help` exits cleanly via argparse SystemExit (code 0)."""
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "research_desk" in out


def test_the_help_lists_every_command(capsys):
    with pytest.raises(SystemExit):
        main(['--help'])
    out = capsys.readouterr().out
    assert out.startswith('usage: research_desk ')
    assert '{collect,tag,web,stocks}' in out


COMMANDS = [[], ['collect'], ['tag'], ['tag', 'run'], ['tag', 'inspect'], ['tag', 'escalate'],
            ['tag', 'reset-worker'], ['web'], ['stocks'], ['stocks', 'set-version']]


@pytest.mark.parametrize('command', COMMANDS, ids=lambda command: ' '.join(command) or 'research_desk')
def test_help_exits_0_on_every_command(command, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main([*command, '--help'])
    assert excinfo.value.code == 0
    out, err = capsys.readouterr()
    assert out.startswith(' '.join(['usage: research_desk', *command]))
    assert err == ''


def test_the_web_and_stocks_help_show_their_arguments(capsys):
    with pytest.raises(SystemExit):
        main(['web', '--help'])
    assert '--view {reports,market,review}' in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(['stocks', '--help'])
    assert 'set-version' in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(['stocks', 'set-version', '--help'])
    assert '--as-of YYYY-MM-DD' in capsys.readouterr().out


USAGE_ERRORS = {
    'no command': [],
    'unknown command': ['bogus'],
    'unknown option': ['--bogus'],
    'web: unknown view': ['web', '--view', 'bad'],
    'web: unknown option': ['web', '--port', '9000'],
    'stocks: no subcommand': ['stocks'],
    'stocks: unknown subcommand': ['stocks', 'bogus'],
    'set-version: no --as-of': ['stocks', 'set-version'],
    'set-version: --as-of without a value': ['stocks', 'set-version', '--as-of'],
    'tag: no subcommand': ['tag'],
    'tag escalate: no --since': ['tag', 'escalate'],
    'collect: both modes': ['collect', '--cutoff-days', '1', '--backfill-days', '2'],
    'collect: not a number': ['collect', '--cutoff-days', 'x'],
}


@pytest.mark.parametrize('argv', list(USAGE_ERRORS.values()), ids=list(USAGE_ERRORS))
def test_usage_errors_exit_2_with_the_usage(argv, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(argv)
    assert excinfo.value.code == 2
    out, err = capsys.readouterr()
    assert out == ''
    assert err.startswith('usage: research_desk')


# ── exit codes ───────────────────────────────────────────────────────────────

def test_a_commands_exit_code_comes_back_from_main(capsys):
    assert main(['collect']) == 1
    assert capsys.readouterr().err == 'Config error: Missing required env var: TELEGRAM_API_ID\n'
    assert main(['tag', 'inspect']) == 4
    assert capsys.readouterr().err == 'SUPABASE_DB_URL is required\n'


def test_python_m_research_desk_help_exits_0():
    result = run_python('-m', 'research_desk', '--help')
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith('usage: research_desk')


def test_python_m_research_desk_without_a_command_exits_2_and_loads_no_web_package():
    # With import times on, stderr names every module the process imported.
    result = run_python('-X', 'importtime', '-m', 'research_desk')
    assert result.returncode == 2
    assert 'usage: research_desk' in result.stderr
    imported = {line.rsplit('|', 1)[-1].strip() for line in result.stderr.splitlines()
                if line.startswith('import time:')}
    assert {'research_desk.cli', 'research_desk.collector.cli', 'research_desk.tagger.cli',
            'research_desk.web.server'} <= imported
    assert not imported & {'fastapi', 'uvicorn', 'research_desk.web.app'}


# ── web ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('argv,view', [
    (['web'], 'reports'),
    (['web', '--view', 'market'], 'market'),
    (['web', '--view', 'review'], 'review'),
])
def test_web_prints_the_address_then_serves_the_app_on_127_0_0_1_8520(argv, view, monkeypatch, capsys,
                                                                     tmp_path):
    monkeypatch.setattr(web_app, 'DEFAULT_DIST', tmp_path / 'no-dist')   # never the real frontend/dist
    served = []

    def run(app, **options):   # stands in for uvicorn.run: no server starts
        served.append((app, options, capsys.readouterr().out))

    monkeypatch.setattr(uvicorn, 'run', run)
    assert main(argv) == 0
    [(app, options, printed_before)] = served
    assert printed_before == f'Research Desk: http://127.0.0.1:8520/?view={view}\n'
    assert isinstance(app, FastAPI)
    assert options == {'host': '127.0.0.1', 'port': 8520}
    assert {'/api/health', '/api/workspace', '/api/review', '/'} <= set(app.openapi()['paths'])
    assert capsys.readouterr().out == ''


# ── stocks set-version ───────────────────────────────────────────────────────

def test_set_version_prints_the_new_version_and_records_the_content_hash(stock_csv, capsys):
    assert set_version('2026-06-30') == 0
    assert capsys.readouterr() == ('KRX@2026-06-30\n', '')
    assert recorded(stock_csv) == {'version': 'KRX@2026-06-30', 'content_hash': content_hash(stock_csv)}
    check = StockList.load(stock_csv).verify()
    assert (check.ok, check.version) == (True, 'KRX@2026-06-30')
    assert folder_names(stock_csv) == ['krx.csv', 'krx.version.json']


def test_set_version_may_write_the_same_date_again(stock_csv, capsys):
    assert set_version('2026-06-30') == 0
    first = version_file(stock_csv).read_bytes()
    assert set_version('2026-06-30') == 0
    assert version_file(stock_csv).read_bytes() == first
    assert capsys.readouterr() == ('KRX@2026-06-30\nKRX@2026-06-30\n', '')


def test_set_version_records_a_changed_stock_list_afresh(stock_csv):
    assert set_version('2026-05-08') == 0
    write_csv(stock_csv, HEADER_LINE + ROWS + '000660,SK하이닉스,KOSPI,반도체,메모리반도체,DRAM/NAND\n')
    assert StockList.load(stock_csv).verify().reason == 'mismatch'   # tag run / escalate would stop
    assert set_version('2026-06-30') == 0
    check = StockList.load(stock_csv).verify()
    assert (check.ok, check.version) == (True, 'KRX@2026-06-30')


@pytest.mark.parametrize('as_of', ['2026-13-01', '20260630', '2026-02-30', '2026-6-30', '30-06-2026', ''])
def test_a_bad_date_exits_4_and_leaves_the_version_file(stock_csv, capsys, as_of):
    assert set_version('2026-05-08') == 0
    before = version_file(stock_csv).read_bytes()
    capsys.readouterr()
    assert set_version(as_of) == 4
    out, err = capsys.readouterr()
    assert out == ''
    assert err.startswith(FAILED) and 'YYYY-MM-DD' in err
    assert err.endswith('\n') and err.count('\n') == 1   # one line
    assert version_file(stock_csv).read_bytes() == before
    assert folder_names(stock_csv) == ['krx.csv', 'krx.version.json']


@pytest.mark.parametrize('case,reason', [
    ('wrong header', '머리줄이 다릅니다'),
    ('not UTF-8', 'UTF-8로 읽을 수 없습니다'),
    ('missing CSV', '파일이 없습니다'),
])
def test_a_stock_list_that_cannot_be_used_exits_4_and_leaves_the_version_file(stock_csv, capsys, case,
                                                                             reason):
    assert set_version('2026-05-08') == 0
    before = version_file(stock_csv).read_bytes()
    if case == 'wrong header':
        stock_csv.write_bytes(('code,name,market,major,minor,products\n' + ROWS).encode('utf-8'))
    elif case == 'not UTF-8':
        stock_csv.write_bytes((HEADER_LINE + ROWS).encode('cp949'))
    else:
        stock_csv.unlink()
    capsys.readouterr()
    assert set_version('2026-06-30') == 4
    out, err = capsys.readouterr()
    assert out == ''
    assert err.startswith(FAILED) and reason in err
    assert version_file(stock_csv).read_bytes() == before


def test_a_missing_stock_list_exits_4_and_writes_no_version_file(tmp_path, monkeypatch, capsys):
    missing = tmp_path / 'stocks' / 'krx.csv'
    monkeypatch.setenv('KRX_CSV_PATH', str(missing))
    assert set_version('2026-06-30') == 4
    out, err = capsys.readouterr()
    assert out == ''
    assert err.startswith(FAILED) and '파일이 없습니다' in err
    assert not version_file(missing).exists()


def test_set_version_reads_dotenv_when_it_starts(env_file, tmp_path, capsys):
    csv_path = write_csv(tmp_path / 'from-env' / 'krx.csv')
    env_file.write_text(f'KRX_CSV_PATH={csv_path.as_posix()}\n', encoding='utf-8')
    assert 'KRX_CSV_PATH' not in os.environ   # only the temp .env has it
    assert set_version('2026-06-30') == 0
    assert recorded(csv_path) == {'version': 'KRX@2026-06-30', 'content_hash': content_hash(csv_path)}


def test_without_krx_csv_path_the_stock_list_is_the_one_under_the_current_folder(tmp_path, capsys):
    # clean_env made tmp_path the current folder
    csv_path = write_csv(tmp_path / 'docs' / 'stock_data' / 'KRX_stocks_data.csv')
    assert set_version('2026-06-30') == 0
    assert recorded(csv_path) == {'version': 'KRX@2026-06-30', 'content_hash': content_hash(csv_path)}
