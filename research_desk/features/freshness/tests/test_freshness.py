"""features.freshness: the ``GET /api/freshness`` route, its service and readiness (spec §12.3, §12.4).

Checked:
- the window (``router``) and its one address with the handler name;
- the answer: both windows' values judged at the clock's now (spec §12.3's example), the key order,
  stale values with their note, nothing known yet as an answer and not a failure; both windows
  asked again on every request (nothing cached) and now taken on every request; the default clock
  is the time in Korea; the windows looked up when called;
- readiness (spec §12.4): ``NotReady`` from the prices window (``주가``) or the reports window
  (``리포트``) passes through unchanged — the route answers 503 with that feature's sentence while
  another address still answers; nothing of the failure is kept, so the next request recovers; an
  unexpected error is not turned into NotReady;
- creating the service reads nothing (no window, no clock, no .env); one process-wide service,
  which the route uses when nothing overrides it;
- end to end through the real prices and reports windows on their tests' in-memory DBs: the
  windows' values (an aware UTC time, a date) come out in Korean time; without DB settings the
  prices window's 503; DB settings added to ``.env`` count on the next request.

The route takes no request values, so there is no 422 to check before the windows are asked.

The windows (``prices.latest_run`` / ``reports.latest_report_sent_at``) are replaced where the
service looks them up, so no Supabase client is made — but in the end-to-end tests, which hand out
in-memory ones. Every environment variable involved is set or deleted here.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from research_desk.core import db as core_db
from research_desk.core import settings
from research_desk.core.settings import NotReady
from research_desk.features import freshness, prices, reports
from research_desk.features.freshness import router
from research_desk.features.freshness import service as service_module
from research_desk.features.freshness.logic import KST
from research_desk.features.freshness.service import FreshnessService, get_service

ENV = ('SUPABASE_URL', 'SUPABASE_SERVICE_KEY', 'SUPABASE_DB_URL')

DB_REASON = 'DB 접속 설정(SUPABASE_URL, SUPABASE_SERVICE_KEY)이 없습니다'
PRICES_NOT_READY = {'detail': f'주가 기능을 지금 쓸 수 없습니다: {DB_REASON}'}
REPORTS_NOT_READY = {'detail': f'리포트 기능을 지금 쓸 수 없습니다: {DB_REASON}'}
NEVER = '주가가 아직 한 번도 갱신되지 않았습니다'
LAST_RUN_FAILED = '마지막 주가 갱신이 실패했습니다'

NOW = datetime(2026, 10, 8, 20, 0, tzinfo=KST)   # Thursday evening: Thursday's prices are expected
# The windows' answers behind spec §12.3's example.
RUN = {'last_run_at': datetime(2026, 10, 8, 9, 30, tzinfo=timezone.utc), 'last_run_status': 'ok',
       'as_of': date(2026, 10, 8)}
LATEST_AT = datetime(2026, 10, 8, 0, 12, tzinfo=timezone.utc)
SPEC_EXAMPLE = {
    'prices': {'as_of': '2026-10-08', 'last_run_at': '2026-10-08T18:30:00+09:00', 'last_run_status': 'ok',
               'stale': False, 'note': None},
    'reports': {'latest_at': '2026-10-08T09:12:00+09:00', 'stale': False},
    'checked_at': '2026-10-08T20:00:00+09:00',
}


class FakeClock:
    """A now that moves only when a test moves it; counts how often it is read."""

    def __init__(self, now: datetime = NOW) -> None:
        self.now = now
        self.reads = 0

    def __call__(self) -> datetime:
        self.reads += 1
        return self.now


def health():
    return {'status': 'ok'}


def build_app(service=None) -> FastAPI:
    """This feature's router, one other address, and the NotReady → 503 answer the web app adds.
    Without a service, the route uses the process-wide one."""
    app = FastAPI()
    app.include_router(router)
    app.get('/api/health')(health)
    if service is not None:
        app.dependency_overrides[get_service] = lambda: service

    @app.exception_handler(NotReady)
    async def not_ready(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=503)

    return app


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """No DB settings unless a test sets them, no real Supabase client ever, and a fresh
    process-wide service that is put back afterwards."""
    for name in ENV:
        monkeypatch.delenv(name, raising=False)

    def refuse(url, key):
        raise AssertionError('no real Supabase client in these tests')

    monkeypatch.setattr(core_db, 'supabase_client', refuse)
    monkeypatch.setattr(service_module, '_service', None)


@pytest.fixture(autouse=True)
def windows(monkeypatch):
    """``prices.latest_run`` / ``reports.latest_report_sent_at`` replaced where the service looks
    them up; each call is recorded in ``calls``. ``run`` / ``latest_at`` are their answers,
    ``prices_error`` / ``reports_error`` what they raise."""
    state = SimpleNamespace(run=dict(RUN), latest_at=LATEST_AT, prices_error=None, reports_error=None,
                            calls=[])

    def latest_run():
        state.calls.append('prices')
        if state.prices_error is not None:
            raise state.prices_error
        return state.run

    def latest_report_sent_at():
        state.calls.append('reports')
        if state.reports_error is not None:
            raise state.reports_error
        return state.latest_at

    monkeypatch.setattr(prices, 'latest_run', latest_run)
    monkeypatch.setattr(reports, 'latest_report_sent_at', latest_report_sent_at)
    return state


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def service(clock) -> FreshnessService:
    return FreshnessService(clock=clock)


@pytest.fixture
def client(service):
    with TestClient(build_app(service)) as test_client:
        yield test_client


# ── the window and the address ───────────────────────────────────────────────

def test_window_exposes_the_router():
    assert isinstance(freshness.router, APIRouter)
    assert freshness.router is router


def test_the_router_serves_exactly_the_one_address():
    assert [(sorted(r.methods), r.path) for r in router.routes] == [(['GET'], '/api/freshness')]
    assert [r.name for r in router.routes] == ['freshness']


def test_the_feature_name():
    assert service_module.AREA == '자료 기준일'


# ── the answer (spec §12.3) ──────────────────────────────────────────────────

def test_the_route_answers_with_both_windows_judged_at_the_clock_time(client, windows):
    response = client.get('/api/freshness')
    assert response.status_code == 200
    body = response.json()
    assert body == SPEC_EXAMPLE
    assert list(body) == ['prices', 'reports', 'checked_at']
    assert list(body['prices']) == ['as_of', 'last_run_at', 'last_run_status', 'stale', 'note']
    assert list(body['reports']) == ['latest_at', 'stale']
    assert sorted(windows.calls) == ['prices', 'reports']


def test_the_route_answers_stale_values_with_their_note(client, windows):
    windows.run = dict(RUN, last_run_status='failed')
    windows.latest_at = NOW - timedelta(days=3, seconds=1)
    body = client.get('/api/freshness').json()
    assert body['prices'] == {'as_of': '2026-10-08', 'last_run_at': '2026-10-08T18:30:00+09:00',
                              'last_run_status': 'failed', 'stale': True, 'note': LAST_RUN_FAILED}
    assert body['reports'] == {'latest_at': '2026-10-05T19:59:59+09:00', 'stale': True}


def test_an_old_as_of_is_answered_with_the_holiday_hint(client, windows):
    windows.run = dict(RUN, as_of=date(2026, 10, 7))
    assert client.get('/api/freshness').json()['prices'] == {
        'as_of': '2026-10-07', 'last_run_at': '2026-10-08T18:30:00+09:00', 'last_run_status': 'ok',
        'stale': True, 'note': '주가가 10월 7일 기준으로 밀려 있습니다. 휴장일이면 정상입니다'}


def test_nothing_known_yet_is_an_answer_not_a_failure(client, windows):
    windows.run, windows.latest_at = None, None
    response = client.get('/api/freshness')
    assert (response.status_code, response.json()) == (200, {
        'prices': {'as_of': None, 'last_run_at': None, 'last_run_status': None, 'stale': True,
                   'note': NEVER},
        'reports': {'latest_at': None, 'stale': True},
        'checked_at': '2026-10-08T20:00:00+09:00',
    })


def test_every_request_asks_both_windows_again(client, windows):
    assert client.get('/api/freshness').json() == SPEC_EXAMPLE
    windows.run = dict(RUN, as_of=date(2026, 10, 7))
    windows.latest_at = datetime(2026, 10, 8, 2, 0, tzinfo=timezone.utc)
    body = client.get('/api/freshness').json()
    assert (body['prices']['as_of'], body['prices']['stale']) == ('2026-10-07', True)
    assert body['reports']['latest_at'] == '2026-10-08T11:00:00+09:00'
    assert sorted(windows.calls) == ['prices', 'prices', 'reports', 'reports']


def test_now_is_taken_on_every_request(client, clock):
    assert client.get('/api/freshness').json()['reports']['stale'] is False
    clock.now = LATEST_AT + timedelta(days=3, seconds=1)
    body = client.get('/api/freshness').json()
    assert (body['reports']['stale'], body['checked_at']) == (True, '2026-10-11T09:12:01+09:00')
    assert clock.reads == 2


def test_the_default_clock_is_the_time_in_korea(windows):
    before = datetime.now(timezone.utc).replace(microsecond=0)
    payload = FreshnessService().freshness()
    after = datetime.now(timezone.utc)
    checked = datetime.fromisoformat(payload['checked_at'])
    assert checked.utcoffset() == timedelta(hours=9)
    assert before <= checked <= after


def test_the_windows_are_looked_up_when_called(service, monkeypatch):
    # replaced after the service was made: the service still reaches the new one
    monkeypatch.setattr(prices, 'latest_run', lambda: None)
    monkeypatch.setattr(reports, 'latest_report_sent_at', lambda: None)
    payload = service.freshness()
    assert (payload['prices']['note'], payload['reports']['latest_at']) == (NEVER, None)


# ── readiness (spec §12.4) ───────────────────────────────────────────────────

@pytest.mark.parametrize('area, failing, body', [
    ('주가', 'prices_error', PRICES_NOT_READY),
    ('리포트', 'reports_error', REPORTS_NOT_READY),
], ids=['prices window', 'reports window'])
def test_not_ready_from_a_window_passes_through_unchanged(service, client, windows, area, failing, body):
    error = NotReady(area, DB_REASON)
    setattr(windows, failing, error)
    with pytest.raises(NotReady) as exc:
        service.freshness()
    assert exc.value is error
    response = client.get('/api/freshness')
    assert (response.status_code, response.json()) == (503, body)   # that feature's sentence, unchanged
    other = client.get('/api/health')
    assert (other.status_code, other.json()) == (200, {'status': 'ok'})   # only this address stops
    setattr(windows, failing, None)   # the cause was fixed: nothing of the failed tries was kept
    response = client.get('/api/freshness')
    assert (response.status_code, response.json()) == (200, SPEC_EXAMPLE)


def test_an_unexpected_error_is_not_turned_into_not_ready(service, windows):
    windows.reports_error = RuntimeError('the DB did not answer')
    with pytest.raises(RuntimeError):
        service.freshness()
    windows.reports_error = None
    assert service.freshness() == SPEC_EXAMPLE


# ── creating the service ─────────────────────────────────────────────────────

def test_creating_the_service_reads_nothing(windows, monkeypatch):
    def no_reading(*args, **kwargs):
        raise AssertionError('creating the service must not read .env')

    def no_clock():
        raise AssertionError('creating the service must not take the time')

    monkeypatch.setattr(settings, 'load_env', no_reading)
    created = FreshnessService(clock=no_clock)
    assert isinstance(created, FreshnessService)
    assert service_module._service is None
    made = get_service()
    assert isinstance(made, FreshnessService)
    assert get_service() is made and service_module._service is made   # one for the process
    assert windows.calls == []


def test_the_route_uses_the_process_wide_service(windows, monkeypatch):
    monkeypatch.setattr(service_module, '_service', FreshnessService(clock=FakeClock()))
    with TestClient(build_app()) as test_client:
        assert test_client.get('/api/freshness').json() == SPEC_EXAMPLE


# ── end to end through the real prices and reports windows ───────────────────

class BothTables:
    """One stand-in Supabase client for both windows: the price tables are the prices tests'
    in-memory DB, every other table the reports tests' one. ``made`` records each hand-out."""

    def __init__(self, prices_db, reports_db, price_tables) -> None:
        self.prices_db, self.reports_db, self.price_tables = prices_db, reports_db, price_tables
        self.made: list[tuple[str, str]] = []

    def table(self, name: str):
        return (self.prices_db if name in self.price_tables else self.reports_db).table(name)


def report_row(rid: int, sent_at: str, **extra) -> dict:
    """An in-scope reports row; ``extra`` sets any other column."""
    return {'id': rid, 'sent_at': sent_at, 'tagging_status': 'auto', 'out_of_scope_reason': None} | extra


@pytest.fixture
def real_windows(monkeypatch, windows):
    """The real prices and reports windows (fresh process-wide services, put back afterwards) on
    in-memory DBs that core.db.supabase_client hands out. No DB settings are set here."""
    from research_desk.features.prices import service as prices_service
    from research_desk.features.prices.tests import fakes as prices_fakes
    from research_desk.features.reports import service as reports_service
    from research_desk.features.reports.tests.fakes import FakeSupabase as ReportsDB

    prices_db = prices_fakes.FakeSupabase(runs=[
        {'run_id': 1, 'started_at': '2026-10-07T09:30:00+00:00', 'status': 'ok', 'as_of': '2026-10-07'},
        {'run_id': 2, 'started_at': '2026-10-08T09:30:00+00:00', 'status': 'ok', 'as_of': '2026-10-08'},
    ])
    reports_db = ReportsDB(reports=[
        report_row(1, '2026-10-07T23:00:00+00:00'),
        report_row(2, '2026-10-08T00:12:00+00:00'),                                     # the newest in scope
        report_row(3, '2026-10-08T05:00:00+00:00', tagging_status='review_needed'),      # not final
        report_row(4, '2026-10-08T06:00:00+00:00', out_of_scope_reason='foreign'),       # out of scope
        report_row(5, '2026-10-08T07:00:00+00:00', tagging_status='pending'),
    ])
    client = BothTables(prices_db, reports_db, (prices_fakes.SNAPSHOT, prices_fakes.RUNS))

    def supabase_client(url, key):
        client.made.append((url, key))
        return client

    monkeypatch.setattr(core_db, 'supabase_client', supabase_client)
    monkeypatch.setattr(prices_service, '_service', None)
    monkeypatch.setattr(reports_service, '_service', None)
    monkeypatch.setattr(prices, 'latest_run', prices_service.latest_run)
    monkeypatch.setattr(reports, 'latest_report_sent_at', reports_service.latest_report_sent_at)
    return SimpleNamespace(client=client, prices_db=prices_db, reports_db=reports_db,
                           price_runs=prices_fakes.RUNS, url=prices_fakes.URL, key=prices_fakes.KEY)


def test_end_to_end_through_the_real_windows(real_windows, client, monkeypatch):
    monkeypatch.setenv('SUPABASE_URL', real_windows.url)
    monkeypatch.setenv('SUPABASE_SERVICE_KEY', real_windows.key)
    response = client.get('/api/freshness')
    assert (response.status_code, response.json()) == (200, SPEC_EXAMPLE)
    # the answer came from the windows' own reads of their tables
    assert real_windows.prices_db.queries(real_windows.price_runs)
    assert real_windows.reports_db.queries('reports')


def test_end_to_end_without_db_settings_the_prices_window_is_not_ready(real_windows, client, windows):
    response = client.get('/api/freshness')
    assert (response.status_code, response.json()) == (503, PRICES_NOT_READY)
    assert real_windows.client.made == [] and windows.calls == []


def test_db_settings_added_to_env_count_on_the_next_request(env_file, real_windows, client, windows):
    response = client.get('/api/freshness')
    assert (response.status_code, response.json()) == (503, PRICES_NOT_READY)
    env_file.write_text(f'SUPABASE_URL={real_windows.url}\nSUPABASE_SERVICE_KEY={real_windows.key}\n',
                        encoding='utf-8')
    response = client.get('/api/freshness')
    assert (response.status_code, response.json()) == (200, SPEC_EXAMPLE)
    assert real_windows.reports_db.queries('reports') and windows.calls == []
