"""``prices update [--codes]`` (spec §8, §11): the command registered by ``register_jobs``.

The DB is the in-memory FakeSupabase handed out by core.db.supabase_client, KIS is FakeKis handed
out by ``jobs.kis_client``, the time is ``jobs.utc_now``; without a test's setup each of them
refuses. Every setting the command reads is deleted first and the current folder is a temp folder.

Checked:
- the command shape: help 0, argument errors 2 (``--codes`` checked by argparse);
- readiness (exit 4, one Korean line on stderr, neither KIS nor the DB touched): DB settings,
  KIS keys, the stock list, in that order;
- a full run: the run record goes running → ok / partial / failed; every stock asked over the
  same date window, values stored with the same-run market medians (KOSDAQ GLOBAL as KOSDAQ);
  partial keeps the previous values of the stocks not received and flags them no_data; failed
  (more than 20 %) leaves every snapshot as it was and stops asking KIS once that is certain;
  a crash or an interrupt closes the run as failed; the exit code is 0 for ok / partial, 1 for
  failed; one summary line;
- the KIS access token (spec §8: once a run): asked once, up front — while the run record is
  running and before any stock; first in ``--codes``. Without it nothing else is asked: the run
  is failed at once (snapshot unchanged, the reason in the run record and the stderr line, exit
  1) and ``--codes`` writes nothing and exits 1. Also run with the real KIS client against a KIS
  stand-in whose token request is refused (403) or fails (5xx): one token attempt (with its
  retries), no stock request;
- ``--codes``: prints the results, writes nothing (no snapshot, no run record), excess from the
  stored snapshots' market medians or blank; exit 0 only when every code was received;
- the KIS client gets the keys, the address and the call rate from the settings; values added to
  ``.env`` are used.
"""
from __future__ import annotations

import argparse
import functools
import io
import json
import sys
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from research_desk.core import db as core_db
from research_desk.core import kis as core_kis
from research_desk.core.kis import KisError
from research_desk.features.prices import jobs, logic, register_jobs

from .fakes import AS_OF, KEY, RUNS, SNAPSHOT, URL, FakeDBError, FakeKis, FakeSupabase

ENV = ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "SUPABASE_DB_URL", "KIS_APP_KEY", "KIS_APP_SECRET",
       "KIS_BASE_URL", "PRICES_MAX_CALLS_PER_SEC", "KRX_CSV_PATH")
APP_KEY, APP_SECRET = "PSfakeAppKey0123456789", "fakeAppSecret/0123456789+abc=="
NOW = datetime(2026, 10, 8, 9, 40, tzinfo=timezone.utc)   # 18:40 KST
TODAY = date(2026, 10, 8)
START = TODAY - timedelta(days=jobs.LOOKBACK_DAYS)

HEADER_LINE = '"종목\n코드",종목명,시장,산업명(대),산업명(중),주요제품\n'
STOCKS = [("005930", "KOSPI"), ("080220", "KOSDAQ"), ("0126Z0", "KOSDAQ GLOBAL")]
NOT_READY = "주가 갱신을 시작하지 않았습니다. 이유: "
DB_REASON = "DB 접속 설정(SUPABASE_URL, SUPABASE_SERVICE_KEY)이 없습니다"
KIS_REASON = "KIS 접속 설정(KIS_APP_KEY, KIS_APP_SECRET)이 없습니다"

# The real factories, kept before the fixtures below replace them.
REAL_KIS_CLIENT = jobs.kis_client
REAL_UTC_NOW = jobs.utc_now


def write_stocks(path, stocks) -> None:
    lines = "".join(f"{code},회사{code},{market},산업,세부,제품\n" for code, market in stocks)
    path.write_bytes((HEADER_LINE + lines).encode("utf-8"))


def parse(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="research_desk")
    register_jobs(parser.add_subparsers(dest="command", required=True))
    return parser.parse_args(list(argv))


def update(*argv: str) -> int:
    args = parse("prices", "update", *argv)
    return args.func(args)


def refuse(*args, **kwargs):
    raise AssertionError("no real Supabase or KIS client in these tests")


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)   # the default stock list path never reaches the real file
    monkeypatch.setattr(core_db, "supabase_client", refuse)
    monkeypatch.setattr(jobs, "kis_client", refuse)
    monkeypatch.setattr(jobs, "utc_now", lambda: NOW)


@pytest.fixture
def world(monkeypatch, tmp_path):
    """Everything set: DB and KIS settings, a stock list, the DB and KIS stand-ins."""
    csv = tmp_path / "krx.csv"
    write_stocks(csv, STOCKS)
    for name, value in (("SUPABASE_URL", URL), ("SUPABASE_SERVICE_KEY", KEY), ("KIS_APP_KEY", APP_KEY),
                        ("KIS_APP_SECRET", APP_SECRET), ("KRX_CSV_PATH", str(csv))):
        monkeypatch.setenv(name, value)
    db, kis, kis_made = FakeSupabase(), FakeKis(), []

    def supabase_client(url, key):
        db.made.append((url, key))
        return db

    def kis_client(cfg):
        kis_made.append(cfg)
        return kis

    monkeypatch.setattr(core_db, "supabase_client", supabase_client)
    monkeypatch.setattr(jobs, "kis_client", kis_client)
    return SimpleNamespace(db=db, kis=kis, kis_made=kis_made, csv=csv,
                           stocks=lambda stocks: write_stocks(csv, stocks))


def closes(last, n=127, base=100):
    """n closes: ``base`` for all but the last, which is ``last``: every return is last/base − 1."""
    return [base] * (n - 1) + [last]


def full_world(world, stocks=STOCKS, last=110):
    world.stocks(stocks)
    for code, _ in stocks:
        world.kis.add(code, closes(last))
    return world


def kis_error(code):
    return KisError(f"KIS daily prices {code} failed after 3 retries: HTTP 500", status=500)


def lines(text: str) -> list[str]:
    return text.splitlines()


# ── the command ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("argv,usage", [
    (["prices", "--help"], "usage: research_desk prices"),
    (["prices", "update", "--help"], "usage: research_desk prices update"),
])
def test_help_exits_0(argv, usage, capsys):
    with pytest.raises(SystemExit) as excinfo:
        parse(*argv)
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith(usage)
    if argv[1] == "update":
        assert "--codes 005930,080220" in out


@pytest.mark.parametrize("argv", [
    ["prices"], ["prices", "bogus"], ["prices", "update", "--codes"], ["prices", "update", "--codes", ""],
    ["prices", "update", "--codes", ","], ["prices", "update", "--codes", "12345"],
    ["prices", "update", "--codes", "005930,abc"], ["prices", "update", "--codes", "0059300"],
    ["prices", "update", "extra"],
], ids=lambda argv: " ".join(argv) or "nothing")
def test_argument_errors_exit_2_with_the_usage(argv, capsys):
    with pytest.raises(SystemExit) as excinfo:
        parse(*argv)
    assert excinfo.value.code == 2
    out, err = capsys.readouterr()
    assert out == "" and err.startswith("usage: research_desk")   # extras: the top parser's usage


def test_codes_are_split_upper_cased_and_kept_once():
    assert parse("prices", "update", "--codes", " 005930 ,0126z0,005930,").codes == ["005930", "0126Z0"]
    assert parse("prices", "update").codes is None


# ── readiness: exit 4, nothing touched ───────────────────────────────────────

def not_ready(world, capsys, *argv) -> str:
    assert update(*argv) == 4
    out, err = capsys.readouterr()
    assert out == ""
    assert world.db.made == [] and world.db.executed == []
    assert world.kis_made == [] and world.kis.calls == []
    assert len(lines(err)) == 1
    return err


@pytest.mark.parametrize("missing", ["SUPABASE_URL", "SUPABASE_SERVICE_KEY"])
@pytest.mark.parametrize("argv", [[], ["--codes", "005930"]], ids=["all", "codes"])
def test_without_db_settings_nothing_happens(world, capsys, monkeypatch, missing, argv):
    monkeypatch.delenv(missing)
    monkeypatch.delenv("KIS_APP_KEY")   # the DB comes first
    assert not_ready(world, capsys, *argv) == NOT_READY + DB_REASON + "\n"


@pytest.mark.parametrize("missing", ["KIS_APP_KEY", "KIS_APP_SECRET"])
@pytest.mark.parametrize("argv", [[], ["--codes", "005930"]], ids=["all", "codes"])
def test_without_kis_keys_nothing_happens(world, capsys, monkeypatch, missing, argv):
    monkeypatch.setenv(missing, "")
    world.csv.unlink()   # the keys come before the stock list
    assert not_ready(world, capsys, *argv) == NOT_READY + KIS_REASON + "\n"


@pytest.mark.parametrize("break_it", [
    lambda csv: csv.unlink(),
    lambda csv: csv.write_bytes(HEADER_LINE.replace("종목명", "회사명").encode("utf-8")),
    lambda csv: csv.write_bytes((HEADER_LINE + "005930,삼성전자,KOSPI,a,b,c\n").encode("cp949")),
], ids=["missing", "wrong header", "not utf-8"])
def test_an_unreadable_stock_list_stops_before_anything(world, capsys, break_it):
    break_it(world.csv)
    err = not_ready(world, capsys)
    assert err.startswith(NOT_READY + "종목표 파일을 읽을 수 없습니다 (")


def test_without_a_stock_list_setting_the_default_path_is_read_from_the_current_folder(world, capsys,
                                                                                         monkeypatch):
    monkeypatch.delenv("KRX_CSV_PATH")   # the current folder is a temp folder without the file
    assert "docs" in not_ready(world, capsys)


def test_a_call_rate_that_is_not_a_number_is_an_error_before_any_call(world, monkeypatch):
    monkeypatch.setenv("PRICES_MAX_CALLS_PER_SEC", "fast")
    with pytest.raises(ValueError, match="PRICES_MAX_CALLS_PER_SEC"):
        update()
    assert world.db.made == [] and world.kis_made == []


# ── a full run ───────────────────────────────────────────────────────────────

def test_a_full_run_stores_every_stock_and_closes_the_run_ok(world, capsys):
    full_world(world, STOCKS)
    world.kis.add("080220", closes(130))
    world.kis.add("0126Z0", closes(150))
    assert update() == 0
    assert capsys.readouterr() == ("주가 갱신 완료(ok): 기준일 2026-10-08, 성공 3종목, 실패 0종목\n", "")

    [run] = world.db.runs()
    assert run == {"run_id": 1, "started_at": NOW.isoformat(), "finished_at": NOW.isoformat(), "status": "ok",
                   "as_of": "2026-10-08", "stocks_total": 3, "stocks_ok": 3, "stocks_failed": 0,
                   "message": None}
    samsung = world.db.snapshot("005930")
    assert samsung == {
        "stock_code": "005930", "market": "KOSPI", "as_of": "2026-10-08", "close": 110,
        "market_cap": 5_000_000_000, "avg_value_20d": 1_000_000, "traded": True,
        "ret_1w": 10.0, "ret_1m": 10.0, "ret_3m": 10.0, "ret_6m": 10.0,
        "xret_1w": 0.0, "xret_1m": 0.0, "xret_3m": 0.0, "xret_6m": 0.0,
        "flags": [], "source": "KIS", "updated_at": NOW.isoformat(),
    }
    # KOSDAQ GLOBAL counts as KOSDAQ: the KOSDAQ median of +30 % and +50 % is +40 %
    assert world.db.snapshot("0126Z0")["market"] == "KOSDAQ"
    assert {code: world.db.snapshot(code)["xret_1m"] for code in ("080220", "0126Z0")} == {
        "080220": -10.0, "0126Z0": 10.0}


def test_every_stock_is_asked_over_the_same_window_and_the_client_is_closed(world):
    full_world(world)
    assert update() == 0
    assert [call for call in world.kis.calls if call[0] == "daily_prices"] == [
        ("daily_prices", code, START, TODAY) for code, _ in STOCKS]
    assert world.kis.codes("quote") == [code for code, _ in STOCKS]
    assert world.kis.closed


def test_the_window_ends_on_the_kst_date(world, monkeypatch):
    full_world(world)
    monkeypatch.setattr(jobs, "utc_now", lambda: datetime(2026, 10, 8, 15, 30, tzinfo=timezone.utc))
    update()
    _, _, start, end = world.kis.calls[0]
    assert (start, end) == (date(2026, 10, 9) - timedelta(days=jobs.LOOKBACK_DAYS), date(2026, 10, 9))


def test_the_lookback_covers_127_trading_days_and_holidays():
    weekdays = sum(1 for i in range(jobs.LOOKBACK_DAYS) if (TODAY - timedelta(days=i)).weekday() < 5)
    assert weekdays >= 127 + 15


def test_the_run_record_is_running_while_kis_is_asked(world, monkeypatch):
    full_world(world)
    seen = []
    world.kis.during = lambda call: seen.append([run["status"] for run in world.db.runs()])
    ticks = iter(NOW + timedelta(seconds=i) for i in range(100))
    monkeypatch.setattr(jobs, "utc_now", lambda: next(ticks))   # a clock that moves on every call
    assert update() == 0
    assert len(seen) == 6 and all(statuses == ["running"] for statuses in seen)
    inserts, updates = world.db.queries(RUNS, "insert"), world.db.queries(RUNS, "update")
    assert [q.payload["status"] for q in inserts] == ["running"]
    assert inserts[0].payload["stocks_total"] == 3
    assert [q.payload["status"] for q in updates] == ["ok"]
    [run] = world.db.runs()
    assert run["started_at"] < run["finished_at"]


def test_duplicate_and_malformed_codes_in_the_stock_list_are_not_asked_twice(world):
    full_world(world, STOCKS + [("005930", "KOSPI"), ("", "KOSPI"), ("12345", "KOSPI")])
    assert update() == 0
    assert world.kis.codes("daily_prices") == ["005930", "080220", "0126Z0"]
    assert world.db.runs()[0]["stocks_total"] == 3


def test_a_halted_stock_is_stored_as_not_traded(world):
    full_world(world)
    world.kis.add("080220", closes(110), halted=True, admin_issue=True)
    assert update() == 0
    row = world.db.snapshot("080220")
    assert row["traded"] is False and row["flags"] == ["halted", "admin_issue"]


def test_a_new_listing_is_stored_with_short_history(world):
    full_world(world)
    world.kis.add("0126Z0", closes(120, n=30))
    assert update() == 0
    row = world.db.snapshot("0126Z0")
    assert (row["ret_1m"], row["ret_3m"], row["xret_3m"]) == (20.0, None, None)
    assert row["flags"] == ["short_history"]


def test_a_new_run_replaces_the_old_values_and_flags(world):
    full_world(world)
    world.db.add_snapshot({"stock_code": "005930", "market": "KOSPI", "close": 1, "ret_1w": 99.0,
                           "flags": ["no_data", "halted"], "updated_at": "2026-10-01T09:40:00+00:00"})
    assert update() == 0
    row = world.db.snapshot("005930")
    assert (row["close"], row["ret_1w"], row["flags"], row["updated_at"]) == (110, 10.0, [], NOW.isoformat())


# ── partial and failed ───────────────────────────────────────────────────────

TEN = [(f"00000{i}", "KOSPI") for i in range(10)]
OLD = {"market": "KOSPI", "as_of": "2026-10-07", "close": 777, "ret_1w": 7.0, "xret_1w": 1.0,
       "traded": True, "flags": ["halted"], "updated_at": "2026-10-07T09:40:00+00:00"}


def ten_with_failures(world, failing):
    full_world(world, TEN)
    for i in failing:
        world.kis.daily_errors[TEN[i][0]] = kis_error(TEN[i][0])


def test_partial_keeps_the_previous_values_of_the_stocks_not_received(world, capsys, caplog):
    ten_with_failures(world, [3, 6])   # 2 of 10 = 20 %: still partial
    world.db.add_snapshot(OLD | {"stock_code": "000003"})
    assert update() == 0
    out, err = capsys.readouterr()
    assert out == ("주가 갱신 일부 완료(partial): 기준일 2026-10-08, 성공 8종목, 실패 2종목. "
                   "못 받은 종목은 이전 값을 유지합니다\n")
    warned = [record.getMessage() for record in caplog.records if record.name == jobs.__name__]
    assert len(warned) == 2 and "000003" in warned[0] and "000006" in warned[1]   # one line per stock
    kept = world.db.snapshot("000003")
    assert {name: kept[name] for name in OLD} == OLD | {"flags": ["no_data", "halted"]}
    assert world.db.snapshot("000006") is None   # nothing earlier to keep: no row
    assert world.db.snapshot("000000")["close"] == 110
    [run] = world.db.runs()
    assert (run["status"], run["stocks_ok"], run["stocks_failed"], run["as_of"]) == ("partial", 8, 2, "2026-10-08")
    assert run["message"] == ("못 받은 종목은 이전 값을 유지합니다. 못 받은 종목: 000003, 000006. 첫 실패 000003: "
                              "KisError: KIS daily prices 000003 failed after 3 retries: HTTP 500")


def test_the_run_message_names_at_most_20_codes(world):
    many = [(f"{i:06d}", "KOSDAQ") for i in range(200)]
    full_world(world, many)
    for code, _ in many[:25]:
        world.kis.daily_errors[code] = kis_error(code)
    assert update() == 0   # 25 of 200: partial
    message = world.db.runs()[0]["message"]
    assert ", ".join(code for code, _ in many[:20]) + " 외 5종목. 첫 실패 000000: " in message
    assert "000020" not in message


@pytest.mark.parametrize("failing,status,code", [([3, 6], "partial", 0), ([2, 5, 9], "failed", 1)])
def test_the_20_percent_boundary(world, failing, status, code):
    ten_with_failures(world, failing)
    assert update() == code
    assert world.db.runs()[0]["status"] == status


def test_failed_leaves_every_snapshot_as_it_was(world, capsys):
    ten_with_failures(world, [2, 5, 9])   # 3 of 10: more than 20 %
    for code, _ in TEN[:5]:
        world.db.add_snapshot(OLD | {"stock_code": code})
    before = deepcopy(world.db.tables[SNAPSHOT])
    assert update() == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert lines(err)[-1] == ("주가 갱신 실패(failed): 기준일 2026-10-08, 성공 7종목, 실패 3종목. "
                              "못 받은 종목이 20%를 넘어 스냅샷을 바꾸지 않았습니다")
    assert world.db.tables[SNAPSHOT] == before
    assert world.db.queries(SNAPSHOT, "upsert") == []
    [run] = world.db.runs()
    assert (run["status"], run["stocks_ok"], run["stocks_failed"], run["as_of"]) == ("failed", 7, 3, "2026-10-08")
    assert run["finished_at"] == NOW.isoformat()
    assert "000002" in run["message"] and "KisError" in run["message"]


def test_a_run_stops_asking_kis_once_it_has_failed(world, capsys):
    ten_with_failures(world, [0, 1, 2])
    assert update() == 1
    assert world.kis.codes("daily_prices") == ["000000", "000001", "000002"]
    [run] = world.db.runs()
    assert (run["status"], run["stocks_ok"], run["stocks_failed"], run["as_of"]) == ("failed", 0, 10, None)
    assert "7종목" in run["message"]
    assert lines(capsys.readouterr().err)[-1] == (
        "주가 갱신 실패(failed): 기준일 없음, 성공 0종목, 실패 10종목. 못 받은 종목이 20%를 넘어 "
        "스냅샷을 바꾸지 않았습니다. 기준을 넘은 뒤 남은 7종목은 받지 않았습니다")


def test_no_usable_row_is_a_failure_and_skips_the_quote(world):
    full_world(world, TEN)
    world.kis.daily["000004"] = []
    world.kis.add("000007", [None, 0])
    assert update() == 0   # 2 of 10 not received: partial
    assert world.kis.codes("quote") == [code for code, _ in TEN if code not in ("000004", "000007")]
    assert world.db.runs()[0]["stocks_failed"] == 2


def test_a_quote_failure_is_a_failure_of_that_stock(world):
    full_world(world, TEN)
    world.kis.quote_errors["000004"] = KisError("KIS quote 000004 failed: HTTP 404", status=404)
    assert update() == 0
    assert world.db.snapshot("000004") is None
    assert world.db.runs()[0]["stocks_failed"] == 1


def test_a_strange_kis_answer_is_a_failure_of_that_stock(world):
    full_world(world, TEN)
    world.kis.daily_errors["000004"] = ValueError("time data 'x' does not match format '%Y%m%d'")
    assert update() == 0
    assert world.db.runs()[0]["status"] == "partial"


# ── crashes ──────────────────────────────────────────────────────────────────

def test_a_db_error_while_saving_closes_the_run_as_failed_and_goes_up(world):
    full_world(world)
    world.db.fail_next("upsert")
    with pytest.raises(FakeDBError):
        update()
    [run] = world.db.runs()
    assert run["status"] == "failed" and run["finished_at"] == NOW.isoformat()
    assert run["message"] == "실행 중 오류로 멈췄습니다: FakeDBError"
    assert world.kis.closed


def test_an_interrupt_closes_the_run_as_failed_and_goes_up(world):
    full_world(world)

    def interrupt(call):
        if call[1] == "080220":
            raise KeyboardInterrupt

    world.kis.during = interrupt
    with pytest.raises(KeyboardInterrupt):
        update()
    [run] = world.db.runs()
    assert run["status"] == "failed" and run["message"] == "실행 중 오류로 멈췄습니다: KeyboardInterrupt"
    assert world.db.tables[SNAPSHOT] == []


def test_a_run_record_that_cannot_be_written_stops_before_kis(world):
    full_world(world)
    world.db.fail_next("insert")
    with pytest.raises(FakeDBError):
        update()
    assert world.kis.calls == [] and world.db.runs() == []


def test_a_run_that_cannot_be_closed_still_shows_the_first_error(world):
    full_world(world)
    world.db.fail_next("upsert")
    world.db.fail_next("update")
    with pytest.raises(FakeDBError, match="upsert"):
        update()
    assert world.db.runs()[0]["status"] == "running"


# ── the access token: once a run, before any stock ───────────────────────────

TOKEN_REASON = "KisError: KIS access token failed: HTTP 403 EGW00133 접근토큰 발급 잠시 후 다시 시도하세요(1분당 1회)"


def run_without_token(reason: str) -> str:
    return f"KIS 접근 토큰을 받지 못했습니다 ({reason}). 아무 종목도 받지 않았고 스냅샷을 바꾸지 않았습니다"


def check_without_token(reason: str) -> str:
    return f"확인용 실행이라 아무것도 저장하지 않았습니다: KIS 접근 토큰을 받지 못했습니다 ({reason}). 아무 종목도 받지 않았습니다"


@pytest.mark.parametrize("argv,runs,asked", [
    ([], ["running"], ["005930", "080220", "0126Z0"]),
    (["--codes", "005930,080220"], [], ["005930", "080220"]),
], ids=["all", "codes"])
def test_the_token_is_asked_once_before_any_stock(world, argv, runs, asked):
    full_world(world)
    seen = []
    world.kis.token_during = lambda: seen.append((list(world.kis.calls), [run["status"] for run in world.db.runs()]))
    assert update(*argv) == 0
    assert world.kis.token_asked == 1
    assert seen == [([], runs)]   # no stock asked yet; a full run's record is already running
    assert world.kis.codes("daily_prices") == asked


def test_without_the_token_a_run_asks_no_stock_and_fails_at_once(world, capsys):
    full_world(world, TEN)
    for code, _ in TEN[:5]:
        world.db.add_snapshot(OLD | {"stock_code": code})
    before = deepcopy(world.db.tables[SNAPSHOT])
    world.kis.token_error = KisError(TOKEN_REASON.removeprefix("KisError: "), code="EGW00133", status=403)
    assert update() == 1
    assert world.kis.token_asked == 1 and world.kis.calls == [] and world.kis.closed
    out, err = capsys.readouterr()
    assert out == ""
    assert lines(err) == [f"주가 갱신 실패(failed): 기준일 없음, 성공 0종목, 실패 10종목. {run_without_token(TOKEN_REASON)}"]
    [run] = world.db.runs()
    assert run == {"run_id": 1, "started_at": NOW.isoformat(), "finished_at": NOW.isoformat(), "status": "failed",
                   "as_of": None, "stocks_total": 10, "stocks_ok": 0, "stocks_failed": 10,
                   "message": run_without_token(TOKEN_REASON)}
    assert world.db.tables[SNAPSHOT] == before and world.db.queries(SNAPSHOT) == []


def test_without_the_token_codes_ask_nothing_and_write_nothing(world, capsys):
    full_world(world)
    world.kis.token_error = KisError(TOKEN_REASON.removeprefix("KisError: "), code="EGW00133", status=403)
    assert update("--codes", "005930,080220") == 1
    assert world.kis.token_asked == 1 and world.kis.calls == [] and world.kis.closed
    assert capsys.readouterr() == ("", check_without_token(TOKEN_REASON) + "\n")
    assert world.db.executed == []   # stopped before anything: no read, nothing written


def test_an_interrupt_while_the_token_is_asked_closes_the_run_as_failed(world):
    full_world(world)
    world.kis.token_error = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        update()
    [run] = world.db.runs()
    assert run["status"] == "failed" and run["message"] == "실행 중 오류로 멈췄습니다: KeyboardInterrupt"
    assert world.kis.calls == [] and world.db.tables[SNAPSHOT] == []


class KisServer:
    """The KIS server behind a real ``KisClient`` (an httpx MockTransport: nothing leaves this PC).

    The token request gets ``token_answer()``; any other request an empty KIS answer. ``paths``
    lists every request. ``clock`` and ``sleep`` are the client's: sleeping moves the clock
    (nothing waits) and ``sleeps`` keeps each wait.
    """

    def __init__(self) -> None:
        self.token_answer = lambda: httpx.Response(200, json={"access_token": "fake-access-token"})
        self.paths: list[str] = []
        self.now = 0.0
        self.sleeps: list[float] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        if request.url.path == core_kis.TOKEN_PATH:
            return self.token_answer()
        return httpx.Response(200, json={"rt_cd": "0", "msg_cd": "MCA00000", "output2": [], "output": {}})

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def kis_server(world, monkeypatch):
    """``jobs.kis_client`` makes the real KIS client again, talking to a KisServer."""
    server = KisServer()
    monkeypatch.setenv("KIS_BASE_URL", "https://kis.example.invalid")   # never the real service
    monkeypatch.setattr(core_kis, "KisClient", functools.partial(
        core_kis.KisClient, transport=httpx.MockTransport(server.handle), sleep=server.sleep, clock=server.clock))
    monkeypatch.setattr(jobs, "kis_client", REAL_KIS_CLIENT)
    return server


HUNDRED = [(f"{i:06d}", "KOSPI") for i in range(100)]
TOKEN_FAILURES = {   # the token answer, then one attempt's token requests (the try and its retries) and waits
    "403": (lambda: httpx.Response(403, json={"error_code": "EGW00133",
                                              "error_description": "접근토큰 발급 잠시 후 다시 시도하세요(1분당 1회)"}),
            1, [], TOKEN_REASON),
    "5xx": (lambda: httpx.Response(503, text="Service Unavailable"), 1 + core_kis.MAX_RETRIES, [1.0, 2.0, 4.0],
            "KisError: KIS access token failed after 3 retries: HTTP 503"),
}


@pytest.mark.parametrize("answer,requests,waits,reason", list(TOKEN_FAILURES.values()), ids=list(TOKEN_FAILURES))
def test_a_run_with_the_real_client_asks_kis_for_one_token_and_no_stock(world, kis_server, capsys, answer,
                                                                        requests, waits, reason):
    world.stocks(HUNDRED)
    for code, _ in HUNDRED[:5]:
        world.db.add_snapshot(OLD | {"stock_code": code})
    before = deepcopy(world.db.tables[SNAPSHOT])
    kis_server.token_answer = answer
    assert update() == 1
    assert kis_server.paths.count(core_kis.TOKEN_PATH) == requests   # one attempt for the whole run
    assert kis_server.paths == [core_kis.TOKEN_PATH] * requests      # and no stock asked
    assert kis_server.sleeps == pytest.approx(waits)
    out, err = capsys.readouterr()
    assert out == ""
    assert lines(err) == [f"주가 갱신 실패(failed): 기준일 없음, 성공 0종목, 실패 100종목. {run_without_token(reason)}"]
    [run] = world.db.runs()
    assert (run["status"], run["stocks_ok"], run["stocks_failed"], run["as_of"], run["message"]) == (
        "failed", 0, 100, None, run_without_token(reason))
    assert world.db.tables[SNAPSHOT] == before and world.db.queries(SNAPSHOT) == []


@pytest.mark.parametrize("answer,requests,waits,reason", list(TOKEN_FAILURES.values()), ids=list(TOKEN_FAILURES))
def test_codes_with_the_real_client_ask_kis_for_one_token_and_no_stock(world, kis_server, capsys, answer,
                                                                       requests, waits, reason):
    world.stocks(STOCKS)
    kis_server.token_answer = answer
    assert update("--codes", "005930,080220,0126Z0") == 1
    assert kis_server.paths.count(core_kis.TOKEN_PATH) == requests
    assert kis_server.paths == [core_kis.TOKEN_PATH] * requests
    assert capsys.readouterr() == ("", check_without_token(reason) + "\n")
    assert world.db.writes() == [] and world.db.runs() == []


def test_a_refused_token_that_repeats_the_keys_shows_and_stores_no_key(world, kis_server, capsys):
    world.stocks(STOCKS)
    kis_server.token_answer = lambda: httpx.Response(403, json={
        "error_code": "EGW00103", "error_description": f"유효하지 않은 AppKey입니다 {APP_KEY} {APP_SECRET}"})
    assert update() == 1
    assert update("--codes", "005930") == 1
    out, err = capsys.readouterr()
    shown = out + err + str(world.db.runs())
    assert "EGW00103" in shown
    for secret in (APP_KEY, APP_SECRET, KEY):
        assert secret not in shown


# ── --codes: a check that writes nothing ─────────────────────────────────────

def stored(code, market, ret):
    return {"stock_code": code, "market": market, "as_of": "2026-10-07",
            **{f"ret_{p}": ret for p, _ in logic.PERIODS}}


def printed(out: str):
    *body, summary = lines(out)
    return json.loads("\n".join(body)), summary


def test_codes_print_the_results_and_write_nothing(world, capsys):
    full_world(world)
    world.kis.add("080220", closes(130))
    for row in (stored("111111", "KOSPI", 2.0), stored("222222", "KOSPI", 4.0), stored("333333", "KOSDAQ", 20.0),
                stored("444444", "KOSDAQ", None)):
        world.db.add_snapshot(row)
    snapshots_before, runs_before = deepcopy(world.db.tables[SNAPSHOT]), world.db.runs()
    assert update("--codes", "080220,005930") == 0
    out, err = capsys.readouterr()
    results, summary = printed(out)
    assert err == ""
    assert summary == "확인용 실행이라 아무것도 저장하지 않았습니다: 기준일 2026-10-08, 받음 2종목, 못 받음 0종목"
    assert list(results) == ["080220", "005930"]
    assert results["005930"]["ret_1w"] == 10.0 and results["005930"]["xret_1w"] == 7.0    # KOSPI median 3
    assert results["080220"]["ret_6m"] == 30.0 and results["080220"]["xret_6m"] == 10.0  # KOSDAQ median 20
    assert results["005930"]["close"] == 110 and results["005930"]["flags"] == []
    assert world.db.writes() == []
    assert world.db.tables[SNAPSHOT] == snapshots_before and world.db.runs() == runs_before == []
    assert world.kis.codes("daily_prices") == ["080220", "005930"]


def test_codes_count_kosdaq_global_as_kosdaq_against_the_stored_median(world, capsys):
    full_world(world)
    world.db.add_snapshot(stored("333333", "KOSDAQ", 4.0))
    assert update("--codes", "0126Z0") == 0
    results, _ = printed(capsys.readouterr().out)
    assert results["0126Z0"]["market"] == "KOSDAQ" and results["0126Z0"]["xret_1m"] == 6.0


def test_codes_without_stored_snapshots_print_blank_excess(world, capsys):
    full_world(world)
    assert update("--codes", "005930") == 0
    results, summary = printed(capsys.readouterr().out)
    assert [results["005930"][f"xret_{p}"] for p, _ in logic.PERIODS] == [None] * 4
    assert summary.endswith(". 저장된 스냅샷이 없어 초과수익률을 비웠습니다")
    assert world.db.writes() == []


def test_codes_with_one_not_received_exit_1(world, capsys):
    full_world(world)
    world.kis.daily_errors["080220"] = kis_error("080220")
    assert update("--codes", "005930,080220") == 1
    out, err = capsys.readouterr()
    results = json.loads(out)
    assert results["080220"] == {"stock_code": "080220", "flags": ["no_data"],
                                 "error": "KisError: KIS daily prices 080220 failed after 3 retries: HTTP 500"}
    assert lines(err)[-1] == ("확인용 실행이라 아무것도 저장하지 않았습니다: 기준일 2026-10-08, 받음 1종목, "
                              "못 받음 1종목. 저장된 스냅샷이 없어 초과수익률을 비웠습니다")
    assert world.db.writes() == []


def test_a_code_not_in_the_stock_list_is_not_asked(world, capsys):
    full_world(world)
    assert update("--codes", "999999,005930") == 1
    results = json.loads(capsys.readouterr().out)
    assert results["999999"] == {"stock_code": "999999", "flags": ["no_data"],
                                 "error": "종목표에 없는 종목코드입니다"}
    assert world.kis.codes("daily_prices") == ["005930"]
    assert world.db.writes() == []


# ── the KIS client and .env ──────────────────────────────────────────────────

def test_the_kis_client_gets_the_keys_address_and_call_rate(monkeypatch):
    made = []

    class Recorder:
        def __init__(self, app_key, app_secret, **options):
            made.append((app_key, app_secret, options))

    monkeypatch.setattr(core_kis, "KisClient", Recorder)
    cfg = SimpleNamespace(app_key=APP_KEY, app_secret=APP_SECRET, base_url="https://kis.example.invalid",
                          max_calls_per_sec=2.5)
    assert isinstance(REAL_KIS_CLIENT(cfg), Recorder)
    assert made == [(APP_KEY, APP_SECRET, {"max_calls_per_sec": 2.5,
                                          "base_url": "https://kis.example.invalid"})]


def test_the_clock_is_aware_utc():
    now = REAL_UTC_NOW()
    assert now.tzinfo is not None and now.utcoffset() == timedelta(0)


def test_the_run_uses_the_call_rate_and_address_settings(world, monkeypatch):
    full_world(world)
    monkeypatch.setenv("PRICES_MAX_CALLS_PER_SEC", "4")
    monkeypatch.setenv("KIS_BASE_URL", "https://kis.example.invalid")
    assert update() == 0
    [cfg] = world.kis_made
    assert (cfg.app_key, cfg.app_secret, cfg.max_calls_per_sec, cfg.base_url) == (
        APP_KEY, APP_SECRET, 4.0, "https://kis.example.invalid")


def test_values_added_to_env_are_used(env_file, world, monkeypatch, capsys):
    full_world(world)
    for name in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "KIS_APP_KEY", "KIS_APP_SECRET"):
        monkeypatch.delenv(name)
    assert update() == 4
    assert capsys.readouterr().err == NOT_READY + DB_REASON + "\n"
    env_file.write_text("SUPABASE_URL=https://from-env-file.test\nSUPABASE_SERVICE_KEY=k\n"
                        f"KIS_APP_KEY={APP_KEY}\nKIS_APP_SECRET={APP_SECRET}\nPRICES_MAX_CALLS_PER_SEC=3\n",
                        encoding="utf-8")
    assert update() == 0
    assert world.db.made == [("https://from-env-file.test", "k")]
    assert (world.kis_made[0].app_key, world.kis_made[0].max_calls_per_sec) == (APP_KEY, 3.0)


def test_no_key_is_printed(world, capsys):
    ten_with_failures(world, [0, 1, 2])
    update()
    update("--codes", "000000,000005")
    out, err = capsys.readouterr()
    for secret in (APP_KEY, APP_SECRET, KEY):
        assert secret not in out and secret not in err
        assert all(secret not in str(run) for run in world.db.runs())


def test_as_of_is_the_latest_day_received(world):
    full_world(world)
    world.kis.add("080220", closes(110), last_day=AS_OF - timedelta(days=1))
    assert update() == 0
    assert world.db.runs()[0]["as_of"] == "2026-10-08"
    assert world.db.snapshot("080220")["as_of"] == "2026-10-07"


# ── output on a cp949 console or pipe ────────────────────────────────────────

FOREIGN = "잠시 후 – réessayez"   # an error text from KIS or the network can hold what cp949 lacks
REPLACED = "잠시 후 ? r?essayez"


def cp949_pipes(monkeypatch) -> SimpleNamespace:
    """Point stdout and stderr at cp949 pipes with strict errors, as a pipe or a redirect is on
    this PC. Called in the test body: pytest puts its own capture back between a fixture and the
    test."""
    pipes = SimpleNamespace(out=io.TextIOWrapper(io.BytesIO(), encoding="cp949"),
                            err=io.TextIOWrapper(io.BytesIO(), encoding="cp949"))
    monkeypatch.setattr(sys, "stdout", pipes.out)
    monkeypatch.setattr(sys, "stderr", pipes.err)
    return pipes


def written(stream) -> str:
    """What reached the pipe, line ends as ``\\n``."""
    stream.flush()
    return stream.buffer.getvalue().decode("cp949").replace("\r\n", "\n")


def test_a_run_writes_its_summary_line_on_a_cp949_pipe(world, monkeypatch):
    full_world(world)
    world.kis.token_error = KisError(f"KIS access token failed: HTTP 403 EGW00133 {FOREIGN}", status=403)
    pipes = cp949_pipes(monkeypatch)
    assert update() == 1
    assert written(pipes.out) == ""
    assert lines(written(pipes.err)) == [
        "주가 갱신 실패(failed): 기준일 없음, 성공 0종목, 실패 3종목. "
        + run_without_token(f"KisError: KIS access token failed: HTTP 403 EGW00133 {REPLACED}")]
    # Only the output is replaced: the run record keeps the text.
    assert world.db.runs()[0]["message"] == run_without_token(
        f"KisError: KIS access token failed: HTTP 403 EGW00133 {FOREIGN}")


def test_codes_write_their_results_on_a_cp949_pipe(world, monkeypatch):
    full_world(world)
    world.kis.daily_errors["080220"] = KisError(f"KIS daily prices 080220 failed: {FOREIGN}", status=500)
    pipes = cp949_pipes(monkeypatch)
    assert update("--codes", "005930,080220") == 1
    results = json.loads(written(pipes.out))
    assert results["080220"] == {"stock_code": "080220", "flags": ["no_data"],
                                 "error": f"KisError: KIS daily prices 080220 failed: {REPLACED}"}
    assert results["005930"]["close"] == 110
    assert lines(written(pipes.err))[-1].startswith("확인용 실행이라 아무것도 저장하지 않았습니다: ")
