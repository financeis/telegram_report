"""features.coverage: the market and company activity routes, the period cache and readiness.

Ported from langgraph_tagger/workspace/tests/test_migration.py:
- test_new_routes_validate_before_mutating, the /api/market 422 parts (the review parts are in
  features/review)
- test_stock_activity_includes_industry_reports, the aggregation part, here through the route

New (spec §6, §9.7, §9.9, §13 item 3):
- both routes' parameter ranges, defaults and 422s; the response keys
- the cache: the rows read for one (start day, include_oos) at a time, kept 180 s from when they
  arrived; a new key replaces the old one; level / unit / items are counted again from the cached
  rows on every request; one read at a time; ``invalidate()`` through the window makes the next
  request read again, without waiting for a read in flight and keeping none of it
- readiness: NotReady from the reports window passes through unchanged; an unreadable stock list
  is ``NotReady("커버리지", "종목표 파일을 읽을 수 없습니다")``, tried again on the next request
  with ``.env`` read again; a stock list version problem only logs a warning; the activity route
  needs no stock list

The reports window (``period_rows`` / ``stock_rows``) is replaced where coverage looks it up, so
no Supabase client is ever made. Every environment variable involved is set or deleted here.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from research_desk.core import db as core_db
from research_desk.core.settings import NotReady
from research_desk.domain.stocks import version_file, write_version
from research_desk.features import coverage, reports
from research_desk.features.coverage import router
from research_desk.features.coverage import service as service_module
from research_desk.features.coverage.logic import market_payload, period_start, stock_activity_payload
from research_desk.features.coverage.service import CoverageService, get_service
from research_desk.features.coverage.tests.rows import frame, wider_frame

ENV = ('SUPABASE_URL', 'SUPABASE_SERVICE_KEY', 'SUPABASE_DB_URL', 'KRX_CSV_PATH', 'STORAGE_BASE_DIR')

LOGGER = 'research_desk.features.coverage.service'
SINCE = '2026-05-01'
MARKET_KEYS = ['total', 'inscope', 'oos', 'publishers', 'latest', 'earliest', 'available_items',
               'coverage', 'ranking', 'types']
ACTIVITY_KEYS = ['timeline', 'publishers', 'total']

DB_REASON = 'DB 접속 설정(SUPABASE_URL, SUPABASE_SERVICE_KEY)이 없습니다'
REPORTS_NOT_READY = {'detail': f'리포트 기능을 지금 쓸 수 없습니다: {DB_REASON}'}
STOCKS_REASON = '종목표 파일을 읽을 수 없습니다'
STOCKS_NOT_READY = {'detail': f'커버리지 기능을 지금 쓸 수 없습니다: {STOCKS_REASON}'}

# The first header cell has a line break inside quotes, like the real file.
HEADER_LINE = '"종목\n코드",종목명,시장,산업명(대),산업명(중),주요제품\n'
ROWS = ('005930,삼성전자,KOSPI,반도체,메모리반도체,DRAM/NAND\n'
        '016360,삼성증권,KOSPI,금융,증권,증권\n'
        '000660,SK하이닉스,KOSPI,반도체,메모리반도체,DRAM\n')
# code → name as the stock list above gives them
NAMES = pd.DataFrame([{'code': '005930', 'name': '삼성전자'}, {'code': '016360', 'name': '삼성증권'},
                      {'code': '000660', 'name': 'SK하이닉스'}])


def write_stock_csv(path: Path, text: str = HEADER_LINE + ROWS) -> Path:
    path.write_bytes(text.encode('utf-8'))
    return path


def market(service, since=SINCE, level='sectors_major', items=(), unit='W', include_oos=False):
    return service.market(since, level, list(items), unit, include_oos)


def expected(rows=None, level='sectors_major', items=(), unit='W', include_oos=False):
    """The market body for these rows, counted directly."""
    return market_payload(frame() if rows is None else rows, NAMES, level, list(items), unit, include_oos)


class FakeClock:
    """Seconds that move only when a test moves them."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class RecordingService:
    """Stands in for the service to see what the routes pass on."""

    def __init__(self) -> None:
        self.calls = []

    def market(self, *args):
        self.calls.append(('market', args))
        return {'ok': True}

    def activity(self, *args):
        self.calls.append(('activity', args))
        return {'ok': True}


def build_app(service=None) -> FastAPI:
    """This feature's router plus the NotReady → 503 answer the web app adds (spec §6). Without a
    service, the routes use the process-wide one."""
    app = FastAPI()
    app.include_router(router)
    if service is not None:
        app.dependency_overrides[get_service] = lambda: service

    @app.exception_handler(NotReady)
    async def not_ready(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=503)

    return app


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """No settings at all unless a test sets them, and no real Supabase client, ever."""
    for name in ENV:
        monkeypatch.delenv(name, raising=False)

    def refuse(url, key):
        raise AssertionError('no real Supabase client in these tests')

    monkeypatch.setattr(core_db, 'supabase_client', refuse)


@pytest.fixture(autouse=True)
def reads(monkeypatch):
    """reports.period_rows / stock_rows, replaced where coverage looks them up; records each read.

    ``rows`` is what a read returns, ``error`` what it raises, ``during`` runs inside a period read.
    """
    state = SimpleNamespace(period=[], stock=[], rows=frame(), error=None, during=None)

    def period_rows(since, include_oos):
        state.period.append((since, include_oos))
        if state.during is not None:
            state.during()
        if state.error is not None:
            raise state.error
        return state.rows

    def stock_rows(code, since):
        state.stock.append((code, since))
        if state.error is not None:
            raise state.error
        return state.rows

    monkeypatch.setattr(reports, 'period_rows', period_rows)
    monkeypatch.setattr(reports, 'stock_rows', stock_rows)
    return state


@pytest.fixture
def stock_csv(tmp_path, monkeypatch) -> Path:
    """A small stock list with a matching version file, at KRX_CSV_PATH."""
    path = write_stock_csv(tmp_path / 'krx.csv')
    write_version(path, '2026-05-08')
    monkeypatch.setenv('KRX_CSV_PATH', str(path))
    return path


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def service(clock) -> CoverageService:
    return CoverageService(clock=clock)


@pytest.fixture
def client(service):
    with TestClient(build_app(service)) as test_client:
        yield test_client


@pytest.fixture
def recorder() -> RecordingService:
    return RecordingService()


@pytest.fixture
def recording_client(recorder):
    with TestClient(build_app(recorder)) as test_client:
        yield test_client


@pytest.fixture
def window(monkeypatch, clock) -> CoverageService:
    """The process-wide service starts fresh (on the fake clock) and is put back after the test."""
    fresh = CoverageService(clock=clock)
    monkeypatch.setattr(service_module, '_service', fresh)
    return fresh


# ── the window and the addresses ─────────────────────────────────────────────

def test_window_exposes_the_router_and_invalidate():
    assert isinstance(coverage.router, APIRouter)
    assert callable(coverage.invalidate)


def test_the_router_serves_exactly_the_two_addresses():
    assert sorted((sorted(r.methods), r.path) for r in router.routes) == [
        (['GET'], '/api/market'),
        (['GET'], '/api/stocks/{code}/activity'),
    ]
    # the old handler names, so these /openapi.json entries stay the same
    assert sorted(r.name for r in router.routes) == ['activity', 'market']


# ── parameters (spec §6) ─────────────────────────────────────────────────────

def test_market_defaults(recording_client, recorder):
    before = period_start(36500)
    assert recording_client.get('/api/market').json() == {'ok': True}
    after = period_start(36500)
    ((name, (since, *rest)),) = recorder.calls
    assert name == 'market'
    assert since in {before, after}
    assert rest == ['sectors_major', [], 'W', False]


def test_market_passes_every_query_value(recording_client, recorder):
    before = period_start(7)
    response = recording_client.get(
        '/api/market?days=7&unit=M&level=products&items=DRAM&items=NAND&include_oos=true')
    after = period_start(7)
    assert response.status_code == 200
    ((name, (since, *rest)),) = recorder.calls
    assert since in {before, after}
    assert rest == ['products', ['DRAM', 'NAND'], 'M', True]


@pytest.mark.parametrize('query', [
    'unit=bad', 'unit=w', 'days=-1', 'days=0', 'days=36501', 'days=abc', 'days=1.5',
    'level=bad', 'level=stock_codes', 'include_oos=maybe',
])
def test_market_rejects_bad_values_with_422(recording_client, recorder, query):
    assert recording_client.get(f'/api/market?{query}').status_code == 422
    assert recorder.calls == []


@pytest.mark.parametrize('days', [1, 36500])
def test_market_accepts_both_ends_of_the_day_range(recording_client, recorder, days):
    assert recording_client.get(f'/api/market?days={days}').status_code == 200
    assert len(recorder.calls) == 1


def test_new_routes_validate_before_mutating(client, reads):
    # ported: the /api/market part, with the real service: nothing is read for a bad request
    assert client.get('/api/market?unit=bad').status_code == 422
    assert client.get('/api/market?days=-1').status_code == 422
    assert reads.period == []


def test_activity_defaults_and_values(recording_client, recorder):
    before = period_start(36500), period_start(30)
    assert recording_client.get('/api/stocks/016360/activity').json() == {'ok': True}
    assert recording_client.get('/api/stocks/16360/activity?days=30&unit=D').json() == {'ok': True}
    after = period_start(36500), period_start(30)
    (first_name, (code1, since1, unit1)), (second_name, (code2, since2, unit2)) = recorder.calls
    assert first_name == second_name == 'activity'
    assert (code1, unit1) == ('016360', 'W') and since1 in {before[0], after[0]}
    assert (code2, unit2) == ('16360', 'D') and since2 in {before[1], after[1]}   # the code as received


@pytest.mark.parametrize('query', ['unit=bad', 'unit=Y', 'days=-1', 'days=0', 'days=36501', 'days=abc'])
def test_activity_rejects_bad_values_with_422(recording_client, recorder, query):
    assert recording_client.get(f'/api/stocks/016360/activity?{query}').status_code == 422
    assert recorder.calls == []


# ── the answers ──────────────────────────────────────────────────────────────

def test_market_answers_with_the_counts_of_the_period_rows(client, reads, stock_csv):
    before = period_start(36500)
    response = client.get('/api/market?unit=D&include_oos=true')
    after = period_start(36500)
    body = response.json()
    assert list(body) == MARKET_KEYS
    assert body == expected(unit='D', include_oos=True)
    ((since, include_oos),) = reads.period
    assert since in {before, after} and include_oos is True


def test_stock_activity_includes_industry_reports(client, reads):
    # ported: the company's 산업 report counts too, here through the route
    reads.rows = frame().iloc[:2]
    body = client.get('/api/stocks/016360/activity').json()
    assert list(body) == ACTIVITY_KEYS
    assert body['total'] == 2
    assert sum(r['count'] for r in body['timeline']) == 2
    assert body == stock_activity_payload(frame().iloc[:2], '016360', 'W')


def test_activity_reads_the_company_rows_with_the_code_as_received(client, reads):
    before = period_start(30)
    client.get('/api/stocks/16360/activity?days=30')
    after = period_start(30)
    ((code, since),) = reads.stock
    assert code == '16360' and since in {before, after}   # DB reads use the code as received (spec §6)
    assert reads.period == []


def test_ranking_names_come_from_the_stock_list(service, reads, stock_csv):
    reads.rows = wider_frame()
    assert market(service, level='products')['ranking'] == [
        {'code': '005930', 'count': 3, 'name': '삼성전자'},
        {'code': '000660', 'count': 2, 'name': 'SK하이닉스'},
        {'code': '999999', 'count': 1, 'name': ''},   # not in the stock list
    ]


# ── the period cache (spec §9.7) ─────────────────────────────────────────────

def test_rows_are_kept_for_180_seconds_from_when_they_arrived(service, reads, clock, stock_csv):
    # steps are exact binary fractions, so the 180 s edge is hit exactly
    reads.during = lambda: clock.advance(30)   # the read itself takes 30 s
    first = market(service)
    reads.during = None
    clock.advance(179.75)
    assert market(service) == first
    assert len(reads.period) == 1
    clock.advance(0.25)                        # 180 s after the rows arrived: read again
    assert market(service) == first
    assert len(reads.period) == 2
    clock.advance(179.75)
    market(service)
    assert len(reads.period) == 2


def test_the_cache_holds_one_period_at_a_time(service, reads, stock_csv):
    market(service, since='2026-05-01', include_oos=False)
    market(service, since='2026-05-01', include_oos=False)
    market(service, since='2026-05-01', include_oos=True)    # a new key replaces the old one
    market(service, since='2026-05-01', include_oos=False)   # so this one is read again
    market(service, since='2026-04-01', include_oos=False)
    market(service, since='2026-04-01', include_oos=False)
    assert reads.period == [('2026-05-01', False), ('2026-05-01', True),
                            ('2026-05-01', False), ('2026-04-01', False)]


CHOICES = [('sectors_major', [], 'W'), ('sectors_minor', [], 'D'), ('products', ['NAND'], 'M'),
           ('products', ['DRAM', 'LNG선'], 'W'), ('sectors_major', ['조선'], 'D'), ('products', [], 'X')]


def test_level_unit_and_items_are_counted_again_from_the_cached_rows(service, reads, stock_csv):
    reads.rows = wider_frame()
    market(service)
    reads.rows = frame()   # a new read would give other numbers
    for level, items, unit in CHOICES:
        assert market(service, level=level, items=items, unit=unit) == \
            expected(wider_frame(), level, items, unit)
    assert len(reads.period) == 1


def test_the_route_counts_other_choices_from_the_cached_rows(client, reads, stock_csv):
    reads.rows = wider_frame()
    for query, (level, items, unit) in [('', ('sectors_major', [], 'W')),
                                        ('?level=products&items=NAND', ('products', ['NAND'], 'W')),
                                        ('?level=sectors_minor&unit=M', ('sectors_minor', [], 'M'))]:
        assert client.get('/api/market' + query).json() == expected(wider_frame(), level, items, unit)
    assert len(reads.period) == 1


def test_a_failed_read_keeps_nothing(service, reads, stock_csv):
    reads.error = RuntimeError('DB 일시 장애')
    with pytest.raises(RuntimeError):
        market(service)
    reads.error = None
    assert market(service) == expected()
    assert len(reads.period) == 2


def test_invalidate_makes_the_next_request_read_again(window, reads, stock_csv):
    with TestClient(build_app()) as client:   # no override: the process-wide service
        first = client.get('/api/market').json()
        assert client.get('/api/market').json() == first
        assert len(reads.period) == 1
        reads.rows = wider_frame()             # e.g. a review action changed the reports
        assert client.get('/api/market').json() == first   # still the cached rows
        coverage.invalidate()
        assert client.get('/api/market').json() == expected(wider_frame())
        assert len(reads.period) == 2
        assert client.get('/api/market').json() == expected(wider_frame())
        assert len(reads.period) == 2


def test_invalidate_before_any_request_is_harmless(window, reads):
    coverage.invalidate()
    assert reads.period == [] and reads.stock == []


def test_creating_the_service_reads_nothing(window, reads, tmp_path, monkeypatch):
    monkeypatch.setenv('KRX_CSV_PATH', str(tmp_path / 'missing.csv'))
    created = CoverageService()
    assert isinstance(created, CoverageService)
    assert get_service() is get_service() is window
    assert reads.period == [] and reads.stock == []


def _blocking_read(reads):
    """Make the next period read wait until ``release`` is set; ``started`` is set inside it."""
    started, release = threading.Event(), threading.Event()

    def during():
        started.set()
        assert release.wait(5)

    reads.during = during
    return started, release


def test_concurrent_requests_for_one_period_read_once(service, reads, stock_csv):
    started, release = _blocking_read(reads)
    answers = []
    first = threading.Thread(target=lambda: answers.append(market(service)))
    second = threading.Thread(target=lambda: answers.append(market(service)))
    first.start()
    try:
        assert started.wait(5)
        reads.during = None
        second.start()
        time.sleep(0.2)   # let the second request reach the read
    finally:
        release.set()
        first.join(5)
        if second.ident is not None:
            second.join(5)
    assert not first.is_alive() and not second.is_alive()
    assert len(reads.period) == 1
    assert answers == [expected(), expected()]


def test_invalidate_does_not_wait_for_a_read_in_flight_and_keeps_none_of_it(service, reads, stock_csv):
    started, release = _blocking_read(reads)
    answers = []
    reader = threading.Thread(target=lambda: answers.append(market(service)))
    reader.start()
    try:
        assert started.wait(5)
        invalidator = threading.Thread(target=service.invalidate)
        invalidator.start()
        invalidator.join(5)
        assert not invalidator.is_alive()   # it returned while the read was still running
    finally:
        release.set()
        reader.join(5)
    assert not reader.is_alive()
    assert answers == [expected()]   # the request in flight still answers
    reads.during = None
    market(service)
    assert len(reads.period) == 2    # the rows read before invalidate() returned were not kept


# ── readiness: the reports window (spec §9.9) ────────────────────────────────

def test_not_ready_from_the_reports_window_passes_through_unchanged(service, client, reads, stock_csv):
    error = NotReady('리포트', DB_REASON)
    reads.error = error
    with pytest.raises(NotReady) as exc:
        market(service)
    assert exc.value is error
    with pytest.raises(NotReady) as exc:
        service.activity('016360', SINCE, 'W')
    assert exc.value is error
    for path in ('/api/market', '/api/stocks/016360/activity'):
        response = client.get(path)
        assert (response.status_code, response.json()) == (503, REPORTS_NOT_READY)
    reads.error = None   # the DB settings were fixed: nothing from the failed tries was kept
    assert client.get('/api/market').json() == expected()


# ── readiness: the stock list (spec §9.9, §8) ────────────────────────────────

UNREADABLE = {
    'missing': lambda path: None,
    'wrong header': lambda path: write_stock_csv(path, HEADER_LINE.replace('종목명', '회사명') + ROWS),
    'not utf-8': lambda path: path.write_bytes((HEADER_LINE + ROWS).encode('cp949')),
    'empty': lambda path: path.write_bytes(b''),
    'a folder': lambda path: path.mkdir(),
}


@pytest.mark.parametrize('make', list(UNREADABLE.values()), ids=list(UNREADABLE))
def test_an_unreadable_stock_list_makes_coverage_not_ready(service, client, reads, tmp_path, monkeypatch,
                                                           make):
    path = tmp_path / 'krx.csv'
    make(path)
    monkeypatch.setenv('KRX_CSV_PATH', str(path))
    with pytest.raises(NotReady) as exc:
        market(service)
    assert (exc.value.area, exc.value.reason) == ('커버리지', STOCKS_REASON)
    response = client.get('/api/market')
    assert (response.status_code, response.json()) == (503, STOCKS_NOT_READY)   # no path, no cause
    assert reads.period == []   # the stock list is prepared before any rows are read
    # the activity address needs no stock list
    assert client.get('/api/stocks/016360/activity').json() == stock_activity_payload(frame(), '016360', 'W')


def test_the_cause_of_an_unreadable_stock_list_goes_to_the_local_log(service, tmp_path, monkeypatch, caplog):
    missing = tmp_path / 'missing.csv'
    monkeypatch.setenv('KRX_CSV_PATH', str(missing))
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        with pytest.raises(NotReady):
            market(service)
    assert any(str(missing) in r.getMessage() for r in caplog.records if r.name == LOGGER)


def test_the_stock_list_is_tried_again_on_the_next_request(client, reads, tmp_path, monkeypatch):
    path = tmp_path / 'krx.csv'
    monkeypatch.setenv('KRX_CSV_PATH', str(path))
    assert client.get('/api/market').json() == STOCKS_NOT_READY
    assert client.get('/api/market').json() == STOCKS_NOT_READY
    write_stock_csv(path)   # fill in the missing file: no restart needed
    assert client.get('/api/market').json() == expected()


def test_a_stock_list_path_added_to_env_is_used_on_the_next_request(env_file, client, reads, tmp_path,
                                                                     monkeypatch):
    monkeypatch.chdir(tmp_path)   # the default docs/stock_data/... path does not exist here
    assert client.get('/api/market').json() == STOCKS_NOT_READY
    listed = write_stock_csv(tmp_path / 'listed.csv')
    env_file.write_text(f'KRX_CSV_PATH={listed.as_posix()}\n', encoding='utf-8')
    assert client.get('/api/market').json() == expected()


def test_the_stock_list_is_read_once_and_kept(client, reads, stock_csv):
    first = client.get('/api/market').json()
    stock_csv.unlink()
    version_file(stock_csv).unlink()
    assert client.get('/api/market?level=products').json() == expected(level='products')
    assert first == expected()


def _mismatch(csv_path: Path) -> None:
    write_stock_csv(csv_path, HEADER_LINE + ROWS.replace('DRAM/NAND', 'DRAM'))


VERSION_PROBLEMS = {
    'missing': (lambda csv_path: version_file(csv_path).unlink(), '버전 정보 파일이 없습니다'),
    'mismatch': (_mismatch, '내용이 버전 정보와 다릅니다'),
    'invalid': (lambda csv_path: version_file(csv_path).write_text('not json', encoding='utf-8'),
                '버전 정보 파일을 읽을 수 없습니다'),
}


@pytest.mark.parametrize('break_version, text', list(VERSION_PROBLEMS.values()), ids=list(VERSION_PROBLEMS))
def test_a_stock_list_version_problem_only_logs_a_warning(client, reads, stock_csv, caplog, break_version,
                                                          text):
    break_version(stock_csv)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        response = client.get('/api/market')
    assert response.status_code == 200
    assert response.json() == expected()
    (record,) = [r for r in caplog.records if r.name == LOGGER]
    assert record.levelno == logging.WARNING
    assert text in record.getMessage() and '커버리지' in record.getMessage()


def test_a_matching_stock_list_version_logs_nothing(client, reads, stock_csv, caplog):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert client.get('/api/market').status_code == 200
    assert [r for r in caplog.records if r.name == LOGGER] == []
