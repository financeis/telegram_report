"""``peers build`` and ``peers inspect`` (spec §11): start-up checks (exit 4, nothing done), the
tagging refusal (exit 1), exit codes by status, arguments, and the inspect report."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from research_desk.core import llm as core_llm
from research_desk.core.settings import NotReady
from research_desk.domain.stocks import write_version
from research_desk.features import reports
from research_desk.features.peers import jobs
from research_desk.features.peers import settings as peers_settings
from research_desk.features.peers.schemas import CompanyProfile, Segment

from .fakes import FakeCollection, FakeLLM, FakeSupabase

HEADER = '"종목\n코드",종목명,시장,산업명(대),산업명(중),주요제품\n'
COMPANIES = {'000010': ('가나반도체', 'Legacy DRAM'), '000020': ('다라메모리', 'NOR Flash')}


def doc(code, section='020100', *, fy=2025, parser='0.2.0'):
    name, term = COMPANIES[code]
    return {'stock_code': code, 'corp_code': f'C{code}', 'corp_name': name, 'corp_cls': 'K',
            'rcept_no': f'R{code}{fy}', 'rcept_dt': '20260310', 'report_name': '사업보고서',
            'fiscal_end': f'{fy}-12-31', 'fiscal_year': fy, 'is_final': True, 'section_code': section,
            'section_title': '', 'segment_label': None, 'prose_text': f'{name}는 {term}를 판매합니다.',
            'table_text': '', 'text': '', 'parser_version': parser}


def reply(model, user):
    code, (name, term) = next((c, v) for c, v in COMPANIES.items() if f'회사명: {v[0]}\n' in user)
    return CompanyProfile(niche_industry=f'{name} 틈새', summary='요약', roles=['부품'],
                          segments=[Segment(name='주력', products=[term], keywords=[], revenue_share_pct=90)],
                          products=[term], keywords=[term], applications=[], customers=[], competitors=[],
                          is_holding=False, is_financial=False, info_quality='충분')


@pytest.fixture
def ready(monkeypatch, tmp_path):
    """Everything a build needs, all of it fake; each test takes one piece away."""
    monkeypatch.chdir(tmp_path)
    csv = tmp_path / 'stocks.csv'
    rows = ''.join(f'{code},{name},KOSDAQ,반도체,메모리,\n' for code, (name, _) in COMPANIES.items())
    csv.write_text(HEADER + rows, encoding='utf-8')
    write_version(csv, '2026-05-08')
    for name, value in {'KRX_CSV_PATH': str(csv), 'SUPABASE_URL': 'https://db.example',
                        'SUPABASE_SERVICE_KEY': 'service-key', 'ANTHROPIC_API_KEY': 'anthropic-key',
                        'OPENAI_API_KEY': 'openai-key'}.items():
        monkeypatch.setenv(name, value)
    world = SimpleNamespace(db=FakeSupabase(), llm=FakeLLM(reply), pinged=[], opened=[], made=[],
                            docs=[doc('000010'), doc('000020'), doc('000010', fy=2024)],
                            tagging=False, csv=csv)

    def supabase(url, key):
        world.made.append((url, key))
        return world.db

    def collection(url, db_name, name):
        world.opened.append((url, db_name, name))
        world.collection = FakeCollection(world.docs)
        return world.collection

    def ping(url):
        world.pinged.append(url)
        return True

    monkeypatch.setattr(jobs, '_supabase', supabase)
    monkeypatch.setattr(jobs, '_mongo_collection', collection)
    monkeypatch.setattr(jobs, '_mongo_ping', ping)
    monkeypatch.setattr(jobs, '_llm_client', lambda timeout_s: world.llm)
    monkeypatch.setattr(reports, 'tagging_in_progress', lambda minutes=30: world.tagging)
    return world


def run(*argv) -> int:
    parser = argparse.ArgumentParser(prog='research_desk')
    jobs.register(parser.add_subparsers(dest='command', required=True))
    args = parser.parse_args(['peers', *argv])
    return args.func(args)


def nothing_done(world):
    assert world.db.log == [] and world.llm.parse_calls == [] and world.llm.embed_calls == []


# ── a build ──────────────────────────────────────────────────────────────────

def test_a_build_runs_with_the_settings_and_reports_json(ready, capsys):
    assert run('build') == 0
    out, err = capsys.readouterr()
    summary = json.loads(out[out.rindex('\n{\n') + 1:] if '\n{\n' in out else out)
    assert summary['status'] == 'done' and summary['eligible'] == 2 and summary['fiscal_year'] == 2025
    assert ready.made == [('https://db.example', 'service-key')]
    assert ready.pinged == ['mongodb://localhost:27017/']
    assert ready.opened[0] == ('mongodb://localhost:27017/', 'FS', 'A001_v2')
    assert ready.collection.closed and ready.llm.closed
    row = ready.db.rows('peer_builds')[0]
    assert (row['status'], row['stock_list_version']) == ('done', 'KRX@2026-05-08')
    assert 'service-key' not in out + err and 'openai-key' not in out + err


def test_the_fiscal_year_flag_overrides_the_setting(ready, monkeypatch, capsys):
    monkeypatch.setenv('PEERS_FISCAL_YEAR', '2030')
    assert run('build', '--fiscal-year', '2024') == 0
    row = ready.db.rows('peer_builds')[0]
    assert row['fiscal_year'] == 2024 and row['eligible'] == 1
    assert ready.db.rows('company_profiles')[0]['rcept_no'] == 'R0000102024'


def test_codes_and_limit_reach_the_build(ready):
    assert run('build', '--codes', '000020, 000010', '--limit', '1') == 0
    assert ready.db.rows('peer_builds')[0]['eligible'] == 1
    assert [r['stock_code'] for r in ready.db.rows('company_profiles')] == ['000010']


def test_a_pilot_exits_0_and_is_recorded_as_pilot(ready):
    assert run('build', '--pilot') == 0
    assert ready.db.rows('peer_builds')[0]['status'] == 'pilot'


def test_an_incomplete_build_exits_1(ready):
    ready.llm.reply = lambda model, user: RuntimeError('AI down')
    assert run('build') == 1
    assert ready.db.rows('peer_builds')[0]['status'] == 'incomplete'


def test_ctrl_c_during_the_build_exits_1_with_a_failed_build(ready):
    ready.llm.reply = lambda model, user: KeyboardInterrupt()
    assert run('build') == 1
    assert ready.db.rows('peer_builds')[0]['status'] == 'failed'


@pytest.mark.parametrize('argv', [['--codes', 'abc'], ['--codes', '00001'], ['--codes', ','],
                                  ['--limit', '0'], ['--limit', 'x'], ['--fiscal-year', 'soon']])
def test_bad_arguments_are_usage_errors(ready, argv, capsys):
    with pytest.raises(SystemExit) as excinfo:
        run('build', *argv)
    assert excinfo.value.code == 2
    nothing_done(ready)


def test_codes_are_read_in_upper_case_and_any_separator():
    assert jobs.parse_codes('0030r0 000010,000020') == ['0030R0', '000010', '000020']


@pytest.mark.parametrize('argv', [['build', '--help'], ['inspect', '--help'], ['--help']])
def test_help_exits_0(argv, capsys):
    with pytest.raises(SystemExit) as excinfo:
        run(*argv)
    assert excinfo.value.code == 0
    assert capsys.readouterr().out.startswith('usage: research_desk peers')


def test_peers_without_a_subcommand_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as excinfo:
        run()
    assert excinfo.value.code == 2


# ── start-up checks: exit 4, nothing done ────────────────────────────────────

def drop(*names):
    def apply(monkeypatch, world):
        for name in names:
            monkeypatch.delenv(name)
    return apply


def escalation_key_missing(monkeypatch, world):
    monkeypatch.setenv('LLM_MODEL_PEERS', 'gpt-5.4-mini')
    monkeypatch.setenv('LLM_MODEL_PEERS_ESCALATION', 'claude-sonnet-5')
    monkeypatch.delenv('ANTHROPIC_API_KEY')


def no_ping(monkeypatch, world):
    monkeypatch.setattr(jobs, '_mongo_ping', lambda url: False)


def no_codex(monkeypatch, world):
    monkeypatch.setenv('LLM_MODEL_PEERS', 'codex:gpt-6-luna')
    monkeypatch.setattr(core_llm, '_codex_bin', lambda: None)


def old_parser_only(monkeypatch, world):
    world.docs = [doc('000010', parser='0.1.0'), doc('000020', fy=2024)]


def missing_stock_list(monkeypatch, world):
    world.csv.unlink()


def changed_stock_list(monkeypatch, world):
    world.csv.write_text(world.csv.read_text(encoding='utf-8') + '000030,새회사,KOSDAQ,a,b,\n', encoding='utf-8')


def broken_synonyms(monkeypatch, world):
    bad = world.csv.with_name('synonyms.yaml')
    bad.write_text('DRAM: [디램]\nNAND: [디램]\n', encoding='utf-8')     # one spelling, two standards
    monkeypatch.setattr(peers_settings, 'SYNONYMS_PATH', bad)


NOT_READY = [
    (drop('SUPABASE_URL'), 'DB 접속 설정(SUPABASE_URL, SUPABASE_SERVICE_KEY)이 없습니다'),
    (drop('SUPABASE_SERVICE_KEY'), 'DB 접속 설정(SUPABASE_URL, SUPABASE_SERVICE_KEY)이 없습니다'),
    (drop('ANTHROPIC_API_KEY'), 'ANTHROPIC_API_KEY가 설정되지 않았습니다'),
    (escalation_key_missing, 'ANTHROPIC_API_KEY가 설정되지 않았습니다'),
    (drop('OPENAI_API_KEY'), 'OPENAI_API_KEY가 설정되지 않았습니다'),
    (no_codex, 'codex CLI'),
    (no_ping, 'MongoDB'),
    (missing_stock_list, '종목표 파일을 읽을 수 없습니다'),
    (changed_stock_list, '종목표 파일 내용이 버전 정보와 다릅니다'),
    (broken_synonyms, '동의어표(features/peers/synonyms.yaml)를 읽을 수 없습니다'),
    (old_parser_only, '2025 회계연도 사업보고서 중 parser_version 0.2.0 이상인 문서가 없습니다'),
]


@pytest.mark.parametrize('breaks, message', NOT_READY,
                         ids=['url', 'service-key', 'profile-key', 'escalation-key', 'openai-key', 'codex',
                              'mongo', 'stock-list', 'stock-version', 'synonyms', 'no-v2-data'])
def test_each_readiness_problem_exits_4_with_one_line_and_does_nothing(ready, monkeypatch, capsys,
                                                                        breaks, message):
    breaks(monkeypatch, ready)
    assert run('build') == 4
    out, err = capsys.readouterr()
    assert out == ''
    assert message in err and len(err.strip().splitlines()) == 1
    nothing_done(ready)


def test_the_db_check_comes_first(ready, monkeypatch, capsys):
    for name in ('SUPABASE_URL', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'KRX_CSV_PATH'):
        monkeypatch.delenv(name)
    monkeypatch.setattr(jobs, '_mongo_ping', lambda url: False)
    assert run('build') == 4
    assert 'DB 접속 설정' in capsys.readouterr().err
    assert ready.pinged == []


def test_the_stock_list_version_problem_is_the_sentence_the_tagger_uses(ready, capsys):
    changed_stock_list(None, ready)
    assert run('build') == 4
    assert capsys.readouterr().err.strip() == (
        '종목표 파일 내용이 버전 정보와 다릅니다. 종목표를 바꿨다면 python -m research_desk stocks '
        'set-version --as-of <자료 기준일, YYYY-MM-DD>를 실행한 뒤 다시 시작하세요.')


# ── tagging in progress, another build running ───────────────────────────────

def test_a_running_tagger_stops_the_build_with_exit_1(ready, capsys):
    ready.tagging = True
    assert run('build') == 1
    assert capsys.readouterr().err.strip() == (
        '분류 작업이 진행 중이라 유사도 계산을 시작하지 않았습니다. 분류가 끝난 뒤 다시 실행하세요.')
    nothing_done(ready)


def test_a_reports_window_that_is_not_ready_stops_the_build_with_exit_4(ready, monkeypatch, capsys):
    def not_ready(minutes=30):
        raise NotReady('리포트', 'DB 접속 설정(SUPABASE_URL, SUPABASE_SERVICE_KEY)이 없습니다')

    monkeypatch.setattr(reports, 'tagging_in_progress', not_ready)
    assert run('build') == 4
    assert '리포트 기능을 지금 쓸 수 없습니다' in capsys.readouterr().err
    nothing_done(ready)


def test_another_running_build_stops_this_one_with_exit_1(ready, capsys):
    now = datetime.now(timezone.utc).isoformat()
    ready.db = FakeSupabase(peer_builds=[{'fiscal_year': 2025, 'profile_version': 'peer-profile@1.0',
                                          'embed_model': 'text-embedding-3-large', 'status': 'running',
                                          'started_at': now, 'heartbeat_at': now}])
    assert run('build') == 1
    assert '진행 중' in capsys.readouterr().err
    assert [b['status'] for b in ready.db.rows('peer_builds')] == ['running']
    assert ready.llm.parse_calls == []


# ── inspect ──────────────────────────────────────────────────────────────────

def test_inspect_without_db_settings_exits_4(ready, monkeypatch, capsys):
    monkeypatch.delenv('SUPABASE_SERVICE_KEY')
    assert run('inspect') == 4
    assert 'DB 접속 설정(SUPABASE_URL, SUPABASE_SERVICE_KEY)이 없습니다' in capsys.readouterr().err
    assert ready.made == []


def test_inspect_reports_profile_counts_tokens_and_recent_builds(ready, capsys):
    ready.llm.reply = lambda model, user: (RuntimeError('AI down') if '다라메모리' in user
                                           else reply(model, user))
    assert run('build') == 1
    capsys.readouterr()
    assert run('inspect') == 0
    report = json.loads(capsys.readouterr().out)
    assert report['profiles'] == [
        {'fiscal_year': 2025, 'profile_version': 'peer-profile@1.0', 'status': 'failed', 'count': 1,
         'input_tokens': 0, 'output_tokens': 0},
        {'fiscal_year': 2025, 'profile_version': 'peer-profile@1.0', 'status': 'ok', 'count': 1,
         'input_tokens': 100, 'output_tokens': 10},
    ]
    assert report['tokens'] == {'input': 100, 'output': 10}
    build = report['builds'][0]
    assert (build['build_id'], build['status'], build['eligible'], build['profiled'], build['failed']) == (
        1, 'incomplete', 2, 1, 1)
    assert build['started_at'] and build['finished_at']
    assert 'term_table' not in build and 'company_quantiles' not in build
