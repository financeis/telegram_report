"""core.kis: KIS Open API client (an httpx MockTransport is the KIS server, no network).

Waits use a fake clock, so pacing and retry backoff are checked without sleeping.
The base URL is under ``.invalid`` (never resolves): no request can leave this machine.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

import research_desk
from research_desk.core import kis
from research_desk.core.kis import KisClient, KisError

REPOSITORY = Path(research_desk.__file__).resolve().parent.parent
BASE_URL = "https://kis.example.invalid"
APP_KEY = "PSfakeAppKey0123456789abcdefghijklm"
APP_SECRET = "fakeAppSecret/0123456789+abcdefghijklmnopqrstuvwxyz=="
TOKEN = "eyJfake.access-token.0123456789"
OK = {"rt_cd": "0", "msg_cd": "MCA00000", "msg1": "정상처리 되었습니다."}


class FakeClock:
    """time.monotonic and time.sleep in one: sleeping moves the clock, nothing waits."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeKis:
    """The KIS server: issues TOKEN, serves ``daily`` rows and ``quote``, records every request.

    ``answers`` (data calls) and ``token_answers`` are played first, one per request:
    an httpx.Response to return or an exception to raise.
    """

    def __init__(self, clock: FakeClock):
        self.clock = clock
        self.requests: list[httpx.Request] = []
        self.times: list[float] = []
        self.answers: list = []
        self.token_answers: list = []
        self.daily: list[dict] = []
        self.quote: dict = {}

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.times.append(self.clock.now)
        if request.url.path == kis.TOKEN_PATH:
            if self.token_answers:
                return self._play(self.token_answers.pop(0))
            return httpx.Response(200, json={"access_token": TOKEN, "token_type": "Bearer",
                                             "expires_in": 86400})
        if self.answers:
            return self._play(self.answers.pop(0))
        if request.url.path == kis.DAILY_PATH:
            low, high = request.url.params["FID_INPUT_DATE_1"], request.url.params["FID_INPUT_DATE_2"]
            rows = sorted((row for row in self.daily if low <= row["stck_bsop_date"] <= high),
                          key=lambda row: row["stck_bsop_date"], reverse=True)  # newest first, like KIS
            return httpx.Response(200, json={**OK, "output1": {}, "output2": rows[:100]})
        if request.url.path == kis.QUOTE_PATH:
            return httpx.Response(200, json={**OK, "output": self.quote})
        return httpx.Response(404, text="Not Found")

    @staticmethod
    def _play(answer):
        if isinstance(answer, Exception):
            raise answer
        return answer

    def data_requests(self) -> list[httpx.Request]:
        return [request for request in self.requests if request.url.path != kis.TOKEN_PATH]


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def server(clock):
    return FakeKis(clock)


def make_client(server: FakeKis, clock: FakeClock, *, rate: float = 10) -> KisClient:
    return KisClient(APP_KEY, APP_SECRET, max_calls_per_sec=rate, base_url=BASE_URL,
                     transport=httpx.MockTransport(server.handle), sleep=clock.sleep, clock=clock)


def trading_days(first: date, count: int) -> list[date]:
    days, day = [], first
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def kis_row(day: date, close: int) -> dict:
    """One output2 row as KIS sends it: every value a string."""
    return {"stck_bsop_date": day.strftime("%Y%m%d"), "stck_clpr": str(close), "stck_oprc": str(close),
            "stck_hgpr": str(close), "stck_lwpr": str(close), "acml_vol": str(close * 10),
            "acml_tr_pbmn": str(close * close * 10), "flng_cls_code": "00", "prtt_rate": "0.00",
            "mod_yn": "N", "prdy_vrss_sign": "3", "prdy_vrss": "0", "revl_issu_reas": ""}


def ymd(day: date) -> str:
    return day.strftime("%Y%m%d")


# ── access token ─────────────────────────────────────────────────────────────

def test_the_token_is_issued_once_and_sent_with_every_call(server, clock):
    server.quote = {"hts_avls": "100", "lstn_stcn": "1000"}
    server.daily = [kis_row(day, 100) for day in trading_days(date(2026, 9, 1), 5)]
    with make_client(server, clock) as client:
        client.quote("005930")
        client.quote("000660")
        client.daily_prices("005930", date(2026, 9, 1), date(2026, 9, 30))

    issue, *data = server.requests
    assert [request.url.path for request in data].count(kis.TOKEN_PATH) == 0
    assert (issue.method, str(issue.url)) == ("POST", f"{BASE_URL}/oauth2/tokenP")
    assert json.loads(issue.content) == {"grant_type": "client_credentials", "appkey": APP_KEY,
                                         "appsecret": APP_SECRET}
    assert [request.headers["tr_id"] for request in data] == ["FHKST01010100", "FHKST01010100",
                                                             "FHKST03010100"]
    for request in data:
        assert request.method == "GET"
        assert str(request.url).startswith(f"{BASE_URL}/uapi/domestic-stock/v1/quotations/")
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert (request.headers["appkey"], request.headers["appsecret"]) == (APP_KEY, APP_SECRET)
        assert request.headers["custtype"] == "P"


@pytest.mark.parametrize("refusal,code,status", [
    (httpx.Response(403, json={"error_code": "EGW00133",
                               "error_description": "접근토큰 발급 잠시 후 다시 시도하세요(1분당 1회)"}),
     "EGW00133", 403),
    (httpx.Response(200, json={"token_type": "Bearer"}), None, 200),
], ids=["refused", "no token in the answer"])
def test_a_failed_token_issue_raises_and_is_never_asked_again(server, clock, refusal, code, status):
    """KIS issues about one token a minute and the app key is shared with another project: a
    client asks once (spec §8), so after a failed token request every call fails at once."""
    server.token_answers = [refusal]   # a second token request would get a token
    server.quote = {"hts_avls": "1", "lstn_stcn": "7"}
    client = make_client(server, clock)

    with pytest.raises(KisError) as exc:
        client.quote("005930")
    assert (exc.value.code, exc.value.status) == (code, status)
    assert [request.url.path for request in server.requests] == [kis.TOKEN_PATH]  # no retry wait

    later_calls = (lambda: client.quote("005930"), lambda: client.ensure_token(),
                   lambda: client.daily_prices("005930", date(2026, 9, 1), date(2026, 9, 30)))
    for call in later_calls:
        with pytest.raises(KisError) as again:
            call()
        assert (again.value.code, again.value.status) == (code, status)
        assert str(exc.value) in str(again.value)   # the first failure's reason
    assert [request.url.path for request in server.requests] == [kis.TOKEN_PATH]  # KIS not asked again
    assert clock.sleeps == []


def test_ensure_token_gets_the_token_once_and_later_calls_reuse_it(server, clock):
    server.quote = {"hts_avls": "1", "lstn_stcn": "1"}
    server.daily = [kis_row(day, 100) for day in trading_days(date(2026, 9, 1), 5)]
    with make_client(server, clock) as client:
        assert client.ensure_token() is None   # the token itself is never handed out
        assert [request.url.path for request in server.requests] == [kis.TOKEN_PATH]
        client.ensure_token()
        client.quote("005930")
        client.daily_prices("005930", date(2026, 9, 1), date(2026, 9, 30))
        client.ensure_token()

    assert [request.url.path for request in server.requests] == [kis.TOKEN_PATH, kis.QUOTE_PATH,
                                                                 kis.DAILY_PATH]
    assert all(request.headers["authorization"] == f"Bearer {TOKEN}" for request in server.data_requests())


def test_a_token_request_that_breaks_another_way_is_not_asked_again_either(server, clock):
    server.token_answers = [httpx.DecodingError("broken gzip body")]   # not a KisError
    client = make_client(server, clock)

    with pytest.raises(httpx.DecodingError):
        client.quote("005930")
    with pytest.raises(KisError, match="access token failed: DecodingError: broken gzip body"):
        client.quote("005930")
    with pytest.raises(KisError):
        client.ensure_token()
    assert [request.url.path for request in server.requests] == [kis.TOKEN_PATH]


def test_threads_sharing_a_client_get_one_token(server, clock):
    server.quote = {"hts_avls": "1", "lstn_stcn": "1"}

    def slow_token(request):
        if request.url.path == kis.TOKEN_PATH:
            time.sleep(0.05)  # a window in which another thread could also ask for a token
        return server.handle(request)

    client = KisClient(APP_KEY, APP_SECRET, max_calls_per_sec=1000, base_url=BASE_URL,
                       transport=httpx.MockTransport(slow_token), sleep=clock.sleep, clock=clock)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(client.quote, ["000010", "000020", "000030", "000040"]))
    client.close()

    assert [request.url.path for request in server.requests].count(kis.TOKEN_PATH) == 1
    assert len(server.data_requests()) == 4


def test_threads_sharing_a_client_do_not_ask_again_after_a_failed_token(server, clock):
    server.token_answers = [httpx.Response(403, json={"error_code": "EGW00133",
                                                      "error_description": "1분당 1회"})]

    def slow_token(request):
        if request.url.path == kis.TOKEN_PATH:
            time.sleep(0.05)  # the other threads wait for the token meanwhile
        return server.handle(request)

    client = KisClient(APP_KEY, APP_SECRET, max_calls_per_sec=1000, base_url=BASE_URL,
                       transport=httpx.MockTransport(slow_token), sleep=clock.sleep, clock=clock)
    with ThreadPoolExecutor(max_workers=4) as pool:
        calls = [pool.submit(client.quote, code) for code in ("000010", "000020", "000030", "000040")]
    client.close()

    assert all(isinstance(call.exception(), KisError) for call in calls)
    assert [request.url.path for request in server.requests] == [kis.TOKEN_PATH]


# ── daily prices ─────────────────────────────────────────────────────────────

def test_daily_prices_fetches_100_rows_a_call_and_merges_the_pages(server, clock):
    days = trading_days(date(2025, 11, 3), 230)
    server.daily = [kis_row(day, 1000 + i) for i, day in enumerate(days)]

    rows = make_client(server, clock).daily_prices("005930", days[0], days[-1])

    assert rows == [{"date": day, "close": 1000 + i, "volume": (1000 + i) * 10,
                     "trading_value": (1000 + i) ** 2 * 10} for i, day in enumerate(days)]
    params = [dict(request.url.params) for request in server.data_requests()]
    assert len(params) == 3  # 100 + 100 + 30 rows
    # Each next call ends the day before the oldest row so far; all start at the first day.
    assert [(p["FID_INPUT_DATE_1"], p["FID_INPUT_DATE_2"]) for p in params] == [
        (ymd(days[0]), ymd(days[-1])),
        (ymd(days[0]), ymd(days[130] - timedelta(days=1))),
        (ymd(days[0]), ymd(days[30] - timedelta(days=1))),
    ]
    for p in params:
        assert {name: p[name] for name in ("FID_COND_MRKT_DIV_CODE", "FID_INPUT_ISCD",
                                           "FID_PERIOD_DIV_CODE", "FID_ORG_ADJ_PRC")} == {
            "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930",
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "0",  # 수정주가 (adjusted prices)
        }


@pytest.mark.parametrize("count,calls", [(0, 1), (37, 1), (100, 2), (200, 3)])
def test_daily_prices_stops_after_a_short_page(server, clock, count, calls):
    days = trading_days(date(2026, 1, 5), count)
    server.daily = [kis_row(day, 500) for day in days]
    rows = make_client(server, clock).daily_prices("000660", date(2026, 1, 1), date(2026, 12, 31))
    assert [row["date"] for row in rows] == days
    assert len(server.data_requests()) == calls


def test_daily_prices_asks_nothing_before_the_start(server, clock):
    days = trading_days(date(2026, 1, 5), 100)
    server.daily = [kis_row(day, 500) for day in days]
    rows = make_client(server, clock).daily_prices("000660", days[0], days[-1])
    assert len(rows) == 100
    assert len(server.data_requests()) == 1


def test_daily_prices_skips_blank_rows_and_rows_outside_the_span(server, clock):
    blank = {"stck_bsop_date": "", "stck_clpr": "", "acml_vol": "", "acml_tr_pbmn": ""}
    server.answers = [httpx.Response(200, json={**OK, "output1": {}, "output2": [
        kis_row(date(2026, 3, 4), 700), blank, kis_row(date(2026, 2, 27), 650)]})]
    rows = make_client(server, clock).daily_prices("035720", date(2026, 3, 2), date(2026, 3, 6))
    assert rows == [{"date": date(2026, 3, 4), "close": 700, "volume": 7000, "trading_value": 4_900_000}]


def test_daily_prices_of_an_empty_span_makes_no_call(server, clock):
    assert make_client(server, clock).daily_prices("005930", date(2026, 3, 6), date(2026, 3, 2)) == []
    assert server.requests == []


# ── quote ────────────────────────────────────────────────────────────────────

def test_quote_gives_market_cap_in_won_listed_shares_and_status(server, clock):
    server.quote = {"stck_prpr": "73200", "hts_avls": "4371434", "lstn_stcn": "5969782550",
                    "temp_stop_yn": "N", "mang_issu_cls_code": "N", "iscd_stat_cls_code": "55"}

    assert make_client(server, clock).quote("005930") == {
        "market_cap": 437_143_400_000_000,  # hts_avls is in 억원 (100,000,000 won)
        "listed_shares": 5_969_782_550,
        "halted": False,
        "admin_issue": False,
    }
    (request,) = server.data_requests()
    assert request.url.path == "/uapi/domestic-stock/v1/quotations/inquire-price"
    assert dict(request.url.params) == {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930"}
    assert request.headers["tr_id"] == "FHKST01010100"


@pytest.mark.parametrize("fields,halted,admin_issue", [
    ({"temp_stop_yn": "Y"}, True, False),
    ({"iscd_stat_cls_code": "58"}, True, False),  # 58: 거래정지
    ({"mang_issu_cls_code": "Y"}, False, True),
    ({"iscd_stat_cls_code": "51"}, False, True),  # 51: 관리종목
    ({"temp_stop_yn": "Y", "mang_issu_cls_code": "Y"}, True, True),
])
def test_quote_flags_trading_halts_and_administrative_issues(server, clock, fields, halted, admin_issue):
    server.quote = {"hts_avls": "120", "lstn_stcn": "1000000", "temp_stop_yn": "N",
                    "mang_issu_cls_code": "N", "iscd_stat_cls_code": "00", **fields}
    result = make_client(server, clock).quote("123450")
    assert (result["halted"], result["admin_issue"]) == (halted, admin_issue)


def test_quote_without_figures_gives_none(server, clock):
    server.quote = {"hts_avls": "", "lstn_stcn": ""}
    assert make_client(server, clock).quote("123450") == {
        "market_cap": None, "listed_shares": None, "halted": False, "admin_issue": False}


# ── pace ─────────────────────────────────────────────────────────────────────

def test_call_starts_stay_under_the_per_second_cap(server, clock):
    server.quote = {"hts_avls": "1", "lstn_stcn": "1"}
    client = make_client(server, clock, rate=4)
    for code in ("000010", "000020", "000030", "000040", "000050", "000060", "000070"):
        client.quote(code)

    starts = server.times  # the token call counts too
    assert len(starts) == 8
    assert [b - a for a, b in zip(starts, starts[1:])] == pytest.approx([0.25] * 7)
    assert max(sum(1 for t in starts if s <= t < s + 1) for s in starts) == 4


def test_no_wait_when_calls_are_already_far_apart(server, clock):
    server.quote = {"hts_avls": "1", "lstn_stcn": "1"}
    client = make_client(server, clock, rate=2)
    client.quote("000010")  # token, then the quote half a second later
    clock.now += 10
    client.quote("000020")
    assert clock.sleeps == pytest.approx([0.5])


# ── retries ──────────────────────────────────────────────────────────────────

BUSY = {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."}

RETRYABLE = {
    "rate limit EGW00201 over HTTP 500": lambda: httpx.Response(500, json=BUSY),
    "rate limit EGW00201 over HTTP 200": lambda: httpx.Response(200, json=BUSY),
    "HTTP 429": lambda: httpx.Response(429, text="Too Many Requests"),
    "HTTP 500 page": lambda: httpx.Response(500, text="<html>Internal Server Error</html>"),
    "HTTP 502": lambda: httpx.Response(502, text="Bad Gateway"),
    "HTTP 503": lambda: httpx.Response(503, json={}),
    "read timeout": lambda: httpx.ReadTimeout("timed out"),
    "connect timeout": lambda: httpx.ConnectTimeout("timed out"),
    "connection reset": lambda: httpx.ReadError("connection reset by peer"),
}


@pytest.mark.parametrize("answer", list(RETRYABLE.values()), ids=list(RETRYABLE))
def test_a_busy_or_slow_kis_is_tried_again_with_growing_waits(server, clock, answer):
    server.quote = {"hts_avls": "5", "lstn_stcn": "10"}
    server.answers = [answer(), answer()]

    assert make_client(server, clock).quote("005930")["listed_shares"] == 10

    assert len(server.data_requests()) == 3
    # 0.1 s of pace after the token call, then 1 s and 2 s before the two retries
    assert clock.sleeps == pytest.approx([0.1, 1.0, 2.0])


@pytest.mark.parametrize("answer", list(RETRYABLE.values()), ids=list(RETRYABLE))
def test_kis_is_tried_at_most_three_more_times(server, clock, answer):
    server.answers = [answer() for _ in range(10)]

    with pytest.raises(KisError) as exc:
        make_client(server, clock).quote("005930")

    assert len(server.data_requests()) == 4  # the first try and 3 retries
    assert clock.sleeps == pytest.approx([0.1, 1.0, 2.0, 4.0])
    assert "after 3 retries" in str(exc.value)


@pytest.mark.parametrize("answer", list(RETRYABLE.values()), ids=list(RETRYABLE))
def test_a_busy_token_issue_is_one_attempt_with_its_retries_then_never_asked_again(server, clock, answer):
    server.token_answers = [answer() for _ in range(10)]
    client = make_client(server, clock)

    with pytest.raises(KisError) as exc:
        client.quote("005930")
    assert "access token" in str(exc.value) and "after 3 retries" in str(exc.value)
    assert [request.url.path for request in server.requests] == [kis.TOKEN_PATH] * 4  # 1 try, 3 retries
    assert clock.sleeps == pytest.approx([1.0, 2.0, 4.0])

    with pytest.raises(KisError):
        client.quote("005930")
    assert len(server.requests) == 4 and clock.sleeps == pytest.approx([1.0, 2.0, 4.0])
    with pytest.raises(KisError):
        client.ensure_token()
    assert len(server.requests) == 4


NOT_RETRYABLE = {
    "KIS refusal over HTTP 200": (
        lambda: httpx.Response(200, json={"rt_cd": "1", "msg_cd": "KIER2620", "msg1": "조회할 자료가 없습니다"}),
        "KIER2620", 200),
    "HTTP 403": (
        lambda: httpx.Response(403, json={"rt_cd": "1", "msg_cd": "EGW00103", "msg1": "유효하지 않은 AppKey입니다."}),
        "EGW00103", 403),
    "HTTP 404": (lambda: httpx.Response(404, text="Not Found"), None, 404),
    "a base URL httpx cannot use": (
        lambda: httpx.UnsupportedProtocol("Request URL has an unsupported protocol 'ftp://'."), None, None),
}


@pytest.mark.parametrize("answer,code,status", list(NOT_RETRYABLE.values()), ids=list(NOT_RETRYABLE))
def test_other_failures_raise_at_once(server, clock, answer, code, status):
    server.answers = [answer()]

    with pytest.raises(KisError) as exc:
        make_client(server, clock).quote("005930")

    assert (exc.value.code, exc.value.status) == (code, status)
    assert len(server.data_requests()) == 1
    assert clock.sleeps == pytest.approx([0.1])  # the pace only: no retry wait


# ── secrets ──────────────────────────────────────────────────────────────────

def test_errors_and_logs_never_hold_the_key_secret_or_token(server, clock, caplog):
    echo = f"appkey={APP_KEY} appsecret={APP_SECRET} token={TOKEN}"  # a server that repeats them
    server.answers = [httpx.Response(500, json={"rt_cd": "1", "msg_cd": "EGW00999", "msg1": echo}),
                      httpx.ReadTimeout(f"timed out: {echo}"),
                      httpx.Response(503, text=echo),
                      httpx.Response(200, json={**BUSY, "msg1": echo})]

    with caplog.at_level(logging.DEBUG, logger=kis.__name__):
        with pytest.raises(KisError) as exc:
            make_client(server, clock).quote("005930")

    shown = "".join(traceback.format_exception(exc.value)) + caplog.text
    assert caplog.text.count("retry") == 3  # each retry is logged ...
    for secret in (APP_KEY, APP_SECRET, TOKEN):
        assert secret not in shown  # ... and nothing shown holds a credential
    assert "EGW00201" in str(exc.value) and "***" in str(exc.value)


def test_a_refused_token_issue_does_not_repeat_the_credentials(server, clock):
    server.token_answers = [httpx.Response(403, json={
        "error_code": "EGW00103", "error_description": f"invalid appkey {APP_KEY} / {APP_SECRET}"})]
    client = make_client(server, clock)
    with pytest.raises(KisError) as exc:
        client.quote("005930")
    with pytest.raises(KisError) as again:  # the later calls repeat the first failure's text
        client.quote("005930")
    for error in (exc.value, again.value):
        shown = "".join(traceback.format_exception(error))
        assert APP_KEY not in shown and APP_SECRET not in shown
        assert "EGW00103" in shown


# ── setup ────────────────────────────────────────────────────────────────────

def test_the_client_needs_a_key_a_secret_and_a_positive_rate():
    with pytest.raises(ValueError):
        KisClient("", APP_SECRET, max_calls_per_sec=10)
    with pytest.raises(ValueError):
        KisClient(APP_KEY, "", max_calls_per_sec=10)
    for rate in (0, -1):
        with pytest.raises(ValueError):
            KisClient(APP_KEY, APP_SECRET, max_calls_per_sec=rate)


def test_the_default_server_is_the_real_service():
    assert kis.DEFAULT_BASE_URL == "https://openapi.koreainvestment.com:9443"


def test_closing_the_client_closes_its_connections(server, clock):
    with make_client(server, clock) as client:
        client.quote("005930")
        connections = client._http
    assert connections.is_closed


def test_importing_core_kis_loads_no_http_library():
    """Every command imports core when it starts; httpx loads only when the client calls KIS."""
    code = ("import sys, research_desk.core.kis\n"
            "print(sorted(m for m in ('httpx', 'httpcore', 'requests') if m in sys.modules))\n")
    result = subprocess.run([sys.executable, "-c", code], cwd=REPOSITORY, capture_output=True,
                            text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"
