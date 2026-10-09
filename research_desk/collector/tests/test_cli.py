"""The ``collect`` command (ported from the root tests/test_main.py).

The old ``parse_args(argv)`` is now the ``collect`` subparser that
``register(subparsers)`` adds; the old ``main(argv)`` is ``args.func(args)``.
The handler tests at the end run the whole command with fake Telegram/Storage
objects (the collector conftest fails any test that reaches a real one).
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

import pytest

import research_desk
from research_desk.collector import cli
from research_desk.collector.cli import _dry_run, compute_exit_code, register
from research_desk.collector.run import RunResult
from research_desk.collector.tests.fakes import FakeStorage, FakeTelegramClient, make_msg


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='research_desk')
    register(parser.add_subparsers(dest='command'))
    return parser


def parse(*argv: str) -> argparse.Namespace:
    """Parse ``research_desk collect <argv>`` (the old ``main.parse_args(argv)``)."""
    return _parser().parse_args(['collect', *argv])


def invoke(*argv: str) -> int:
    """Run ``research_desk collect <argv>`` the way the entry point does."""
    args = parse(*argv)
    return args.func(args)


# === parse_args ===

def test_parse_args_no_flags_defaults():
    args = parse()
    assert args.cutoff_days is None
    assert args.backfill_days is None
    assert args.dry_run is False
    assert args.verbose is False


def test_parse_args_cutoff_days():
    args = parse('--cutoff-days', '7')
    assert args.cutoff_days == 7


def test_parse_args_dry_run():
    args = parse('--dry-run')
    assert args.dry_run is True


def test_parse_args_verbose_short():
    args = parse('-v')
    assert args.verbose is True


def test_parse_args_verbose_long():
    args = parse('--verbose')
    assert args.verbose is True


# === compute_exit_code ===

def test_exit_code_success_with_zero_failures():
    assert compute_exit_code(RunResult(processed=5)) == 0


def test_exit_code_success_with_no_messages():
    assert compute_exit_code(RunResult()) == 0


def test_exit_code_partial_failure_in_stage_b():
    assert compute_exit_code(RunResult(processed=3, failed=1)) == 2


def test_exit_code_partial_failure_in_stage_a():
    assert compute_exit_code(RunResult(retried_fail=1)) == 2


def test_exit_code_partial_failure_both_stages():
    assert compute_exit_code(RunResult(processed=2, failed=1, retried_fail=1)) == 2


# === Backfill flag ===

def test_parse_args_backfill_days():
    args = parse('--backfill-days', '365')
    assert args.backfill_days == 365
    assert args.cutoff_days is None


def test_parse_args_mutually_exclusive_raises_systemexit(capsys):
    """argparse rejects --cutoff-days + --backfill-days combination."""
    with pytest.raises(SystemExit) as exc_info:
        parse('--cutoff-days', '30', '--backfill-days', '365')
    assert exc_info.value.code == 2
    assert 'not allowed with argument' in capsys.readouterr().err


# === Dry-run with backfill ===

def _make_dry_run_config(**overrides):
    """Build a config-shaped object with channel_ref() for _dry_run tests."""
    from types import SimpleNamespace

    def channel_ref(self):
        if getattr(self, 'telegram_channel_id', None) is not None:
            return self.telegram_channel_id
        return self.telegram_channel

    defaults = dict(
        telegram_channel='example_channel',
        telegram_channel_id=None,
        initial_cutoff_days=30,
    )
    defaults.update(overrides)
    ns = SimpleNamespace(**defaults)
    ns.channel_ref = channel_ref.__get__(ns, SimpleNamespace)
    return ns


@pytest.mark.asyncio
async def test_dry_run_with_backfill_uses_skip_set(monkeypatch, tmp_path, capsys):
    """_dry_run with backfill_days uses iter_since_date and skips IDs already
    in reports OR failed_attempts. The 'new' count should reflect dedupe."""
    storage = FakeStorage(
        base_dir=tmp_path,
        existing_ids={100, 101},
        failed_ids=[200],
    )
    client = FakeTelegramClient()
    client.new_messages = [make_msg(100), make_msg(101), make_msg(200), make_msg(300)]

    config = _make_dry_run_config()

    rc = await _dry_run(client, storage, config, backfill_days=90)

    assert rc == 0
    # iter_since_date called with backfill_days, not initial_cutoff_days
    assert ('iter_since_date', 'example_channel', 90) in client.calls

    captured = capsys.readouterr()
    log_output = captured.err  # logging defaults to stderr

    # Reports nothing was actually downloaded; nothing inserted; nothing saved
    assert storage.inserted == []
    assert storage.saved_files == []


@pytest.mark.asyncio
async def test_dry_run_uses_channel_id_when_set(tmp_path):
    """When telegram_channel_id is set on config, dry-run fetches via int id."""
    storage = FakeStorage(base_dir=tmp_path)
    client = FakeTelegramClient()
    client.new_messages = [make_msg(125165)]

    config = _make_dry_run_config(telegram_channel_id=1000000001)

    rc = await _dry_run(client, storage, config, backfill_days=None)

    assert rc == 0
    # First run (max_seen=0) — uses iter_since_date with the int id
    assert any(c[0] == 'iter_since_date' and c[1] == 1000000001 for c in client.calls)


def _messages(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == cli.__name__]


@pytest.mark.asyncio
async def test_dry_run_backfill_listing_counts_new_and_skipped(tmp_path, caplog):
    storage = FakeStorage(base_dir=tmp_path, existing_ids={100, 101}, failed_ids=[200])
    client = FakeTelegramClient()
    client.new_messages = [make_msg(100), make_msg(101), make_msg(200),
                           make_msg(300, file_name='new.pdf'), make_msg(301, has_pdf=False)]

    with caplog.at_level(logging.INFO, logger=cli.__name__):
        await _dry_run(client, storage, _make_dry_run_config(), backfill_days=90)

    assert _messages(caplog) == [
        'DRY RUN (backfill mode): 3 existing message_ids will be skipped, fetching from 90 days ago',
        'DRY RUN — would process the following:',
        '  msg_id=300 sent_at=2026-05-05T12:00:00+00:00 file=new.pdf',
        'DRY RUN — total 1 new PDF messages',
        'DRY RUN — also 3 already-known messages skipped',
    ]


@pytest.mark.asyncio
async def test_dry_run_normal_mode_continues_after_last_seen(tmp_path, caplog):
    storage = FakeStorage(base_dir=tmp_path, max_seen=500, existing_ids={501})
    client = FakeTelegramClient()
    client.new_messages = [make_msg(501, file_name='a.pdf')]

    with caplog.at_level(logging.INFO, logger=cli.__name__):
        rc = await _dry_run(client, storage, _make_dry_run_config(), backfill_days=None)

    assert rc == 0
    assert client.calls == [('iter_after_id', 'example_channel', 500)]
    # Normal mode has no skip set: 501 is listed, and there is no "skipped" line.
    assert _messages(caplog) == [
        'DRY RUN — would process the following:',
        '  msg_id=501 sent_at=2026-05-05T12:00:00+00:00 file=a.pdf',
        'DRY RUN — total 1 new PDF messages',
    ]


# === register ===

def test_register_adds_collect_with_func():
    parser = argparse.ArgumentParser(prog='research_desk')
    collect_parser = register(parser.add_subparsers(dest='command'))

    assert collect_parser.prog == 'research_desk collect'
    args = parser.parse_args(['collect'])
    assert args.func is cli.collect


def test_collect_help_exits_0(capsys):
    with pytest.raises(SystemExit) as exc_info:
        parse('--help')
    assert exc_info.value.code == 0
    out = ' '.join(capsys.readouterr().out.split())
    assert 'Collect PDF reports from a Telegram channel into Supabase + local FS.' in out
    for flag in ('--cutoff-days CUTOFF_DAYS', '--backfill-days BACKFILL_DAYS', '--dry-run', '-v, --verbose'):
        assert flag in out


@pytest.mark.parametrize('argv', [['--nope'], ['--cutoff-days', 'x'], ['--backfill-days']])
def test_collect_bad_arguments_exit_2(argv):
    with pytest.raises(SystemExit) as exc_info:
        parse(*argv)
    assert exc_info.value.code == 2


def test_registering_collect_does_not_import_telethon_or_supabase():
    """The entry point registers every command for every run: keep that light."""
    code = (
        'import argparse, sys\n'
        'from research_desk.collector.cli import register\n'
        'register(argparse.ArgumentParser().add_subparsers())\n'
        "print(sorted(m for m in ('telethon', 'supabase', 'asyncpg') if m in sys.modules))\n"
    )
    root = Path(research_desk.__file__).resolve().parents[1]
    done = subprocess.run([sys.executable, '-c', code], cwd=root,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == '[]'


# === setup_logging ===

def test_setup_logging_matches_the_old_main(monkeypatch):
    calls = []
    monkeypatch.setattr(logging, 'basicConfig', lambda **kwargs: calls.append(kwargs))

    cli.setup_logging(False)
    cli.setup_logging(False, level_str='warning')
    cli.setup_logging(False, level_str='nonsense')
    cli.setup_logging(True, level_str='ERROR')

    assert [c['level'] for c in calls] == [logging.INFO, logging.WARNING, logging.INFO, logging.DEBUG]
    for c in calls:
        assert c['format'] == '%(asctime)s %(levelname)-8s %(name)-10s %(message)s'
        assert c['datefmt'] == '%Y-%m-%d %H:%M:%S'
        assert c['stream'] is sys.stderr


# === the collect handler, end to end with fakes ===

@pytest.fixture
def wired(monkeypatch, fake_client, fake_storage):
    """Swap the real Telegram/Supabase factories (and logging setup) for recorders.

    Set ``wired['enter_error']`` to make connecting to Telegram fail.
    """
    state: dict = {'logging': []}

    class _Telegram:
        def __init__(self, api_id, api_hash, session_path):
            state['telegram'] = dict(api_id=api_id, api_hash=api_hash, session_path=session_path)

        async def __aenter__(self):
            if state.get('enter_error') is not None:
                raise state['enter_error']
            state['connected'] = True
            return fake_client

        async def __aexit__(self, exc_type, exc, tb):
            state['disconnected'] = True

    def _build_storage(supabase_url, supabase_service_key, base_dir):
        state['storage'] = dict(supabase_url=supabase_url,
                                supabase_service_key=supabase_service_key, base_dir=base_dir)
        return fake_storage

    monkeypatch.setattr(cli, 'TelegramClient', _Telegram)
    monkeypatch.setattr(cli, 'build_storage', _build_storage)
    monkeypatch.setattr(cli, 'setup_logging',
                        lambda verbose, level_str='INFO': state['logging'].append((verbose, level_str)))
    return state


def test_collect_success_returns_0(required_env, clean_env, wired, fake_client, fake_storage, tmp_path):
    clean_env.setenv('STORAGE_BASE_DIR', str(tmp_path))
    fake_client.new_messages = [make_msg(101, file_name='q1.pdf')]

    assert invoke() == 0

    assert wired['telegram'] == dict(api_id=12345, api_hash='abcdef0123456789',
                                     session_path=Path('sessions') / 'samstudy')
    assert wired['storage'] == dict(supabase_url='https://test.supabase.co',
                                    supabase_service_key='eyJtest', base_dir=tmp_path)
    assert wired['connected'] and wired['disconnected']
    assert wired['logging'] == [(False, 'INFO')]
    # First run: INITIAL_CUTOFF_DAYS default (30) days back, DB label = TELEGRAM_CHANNEL
    assert ('iter_since_date', 'example_channel', 30) in fake_client.calls
    assert [(r['message_id'], r['chat_username'], r['file_path']) for r in fake_storage.inserted] == [
        (101, 'example_channel', '101_q1.pdf')]


def test_collect_uses_session_name_and_channel_id(required_env, clean_env, wired, fake_client, fake_storage):
    clean_env.setenv('TELEGRAM_SESSION_NAME', 'mysess')
    clean_env.setenv('TELEGRAM_CHANNEL_ID', '1000000001')
    fake_storage._max_seen = 125164
    fake_client.new_messages = [make_msg(125165)]

    assert invoke() == 0

    assert wired['telegram']['session_path'] == Path('sessions') / 'mysess'
    assert fake_client.calls[0] == ('iter_after_id', 1000000001, 125164)
    assert fake_storage.inserted[0]['chat_username'] == 'example_channel'


def test_collect_cutoff_days_overrides_initial_cutoff(required_env, clean_env, wired, fake_client):
    clean_env.setenv('INITIAL_CUTOFF_DAYS', '45')

    assert invoke('--cutoff-days', '7') == 0

    assert ('iter_since_date', 'example_channel', 7) in fake_client.calls


def test_collect_backfill_days_skips_known_messages(required_env, wired, fake_client, fake_storage):
    fake_storage._max_seen = 101
    fake_storage._existing_ids = {101}
    fake_client.new_messages = [make_msg(101), make_msg(102)]

    assert invoke('--backfill-days', '90') == 0

    assert ('iter_since_date', 'example_channel', 90) in fake_client.calls
    assert [r['message_id'] for r in fake_storage.inserted] == [102]


def test_collect_partial_failure_returns_2(required_env, wired, fake_client, fake_storage):
    fake_client.new_messages = [make_msg(101), make_msg(102)]
    fake_client.download_errors[102] = RuntimeError('boom')

    assert invoke() == 2

    assert [r['message_id'] for r in fake_storage.inserted] == [101]
    assert [mid for _, mid, _ in fake_storage.failed_upserts] == [102]


def test_collect_stage_a_failure_returns_2(required_env, wired, fake_client, fake_storage):
    fake_storage._failed_ids = [100]
    fake_storage._max_seen = 100
    fake_client.failed_lookups[100] = make_msg(100)
    fake_client.download_errors[100] = RuntimeError('still broken')

    assert invoke() == 2


def test_collect_dry_run_writes_nothing(required_env, wired, fake_client, fake_storage, caplog):
    fake_client.new_messages = [make_msg(101, file_name='a.pdf'), make_msg(102, has_pdf=False)]

    with caplog.at_level(logging.INFO, logger=cli.__name__):
        assert invoke('--dry-run', '--cutoff-days', '7') == 0

    assert fake_client.calls == [('iter_since_date', 'example_channel', 7)]
    assert fake_storage.inserted == []
    assert fake_storage.saved_files == []
    assert fake_storage.failed_upserts == []
    assert '  msg_id=101 sent_at=2026-05-05T12:00:00+00:00 file=a.pdf' in _messages(caplog)
    assert 'DRY RUN — total 1 new PDF messages' in _messages(caplog)


def test_collect_verbose_and_log_level(required_env, clean_env, wired):
    clean_env.setenv('LOG_LEVEL', 'WARNING')

    assert invoke('-v') == 0

    assert wired['logging'] == [(True, 'WARNING')]


@pytest.mark.parametrize('name', ['TELEGRAM_API_ID', 'TELEGRAM_API_HASH', 'TELEGRAM_CHANNEL',
                                  'SUPABASE_URL', 'SUPABASE_SERVICE_KEY'])
def test_collect_missing_required_var_prints_config_error_and_exits_1(
    required_env, clean_env, wired, capsys, name
):
    clean_env.delenv(name)

    assert invoke() == 1

    captured = capsys.readouterr()
    assert captured.err == f'Config error: Missing required env var: {name}\n'
    assert captured.out == ''
    assert 'telegram' not in wired          # never connected
    assert wired['logging'] == []           # logging is set up only after config loads


def test_collect_keyboard_interrupt_returns_1(required_env, monkeypatch, wired, caplog):
    def interrupted(args, config):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, '_amain', interrupted)

    with caplog.at_level(logging.INFO, logger=cli.__name__):
        assert invoke() == 1

    assert [(r.levelno, r.getMessage()) for r in caplog.records if r.name == cli.__name__] == [
        (logging.WARNING, 'Interrupted by user')]


def test_collect_unhandled_error_returns_1(required_env, wired, fake_storage, caplog):
    wired['enter_error'] = RuntimeError('auth failed')

    with caplog.at_level(logging.INFO, logger=cli.__name__):
        assert invoke() == 1

    (record,) = [r for r in caplog.records if r.name == cli.__name__]
    assert (record.levelno, record.getMessage()) == (logging.ERROR, 'Fatal error')
    assert 'auth failed' in str(record.exc_info[1])
    assert fake_storage.inserted == []
