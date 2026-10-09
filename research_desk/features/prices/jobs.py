"""``prices update [--codes 005930,080220]``: the daily KIS price snapshot (spec §8, §11).

``register(subparsers)`` adds ``prices`` and its subcommand ``update``; the window exports it as
``register_jobs`` for ``cli.py``. The command function returns the exit code.

- 4 = not ready. Checked before anything else, in this order: the DB settings (SUPABASE_URL,
  SUPABASE_SERVICE_KEY), the KIS keys (KIS_APP_KEY, KIS_APP_SECRET), the stock list
  (KRX_CSV_PATH) loads. One Korean line on stderr; no DB or KIS call, nothing written. A
  PRICES_MAX_CALLS_PER_SEC that is not a number above 0 is a ValueError (exit 1), also before any
  call.
- The KIS access token is asked for once a run, before any stock (spec §8: KIS issues about one
  a minute, and the app key is shared with another project). Without it nothing else is asked
  (the full run's step 2, ``--codes``), and the KIS client does not ask for it again.
- A full run (no ``--codes``) covers every stock of the stock list (each valid code once, in
  file order):
  1. a run record ``running`` (started_at, stocks_total);
  2. the access token. When it cannot be had (a KIS error or a strange answer) the run is failed
     at once: no stock is asked and no snapshot changes; the run record is closed with no stock
     received, every stock counted as failed and the message ``KIS 접근 토큰을 받지 못했습니다
     (<reason>). …``, which also ends the summary line on stderr; exit 1;
  3. per stock, one after another, paced by the KIS client at PRICES_MAX_CALLS_PER_SEC: the
     adjusted daily prices of the ``LOOKBACK_DAYS`` calendar days up to today (KST), then the
     quote. A KIS error, a strange answer or no usable row is that stock's failure (one warning
     line each). Once more than 20 % of the stocks have failed the run is failed whatever comes
     next, so the rest are not asked;
  4. the status (``logic.run_status``). ok / partial: the received stocks are upserted with the
     excess against this run's market medians, and the stocks not received keep their stored
     values and get the ``no_data`` flag (a stock without a stored row gets none). failed: no
     snapshot changes;
  5. the run record is closed with the status, as_of (the latest day received), the counts and a
     message;
  6. one summary line: stdout for ok / partial (exit 0), stderr for failed (exit 1).
  An exception on the way closes the run record as failed and goes up: Python prints it and the
  process ends with 1. Ctrl+C also closes the run record as failed and goes up, but the process
  then ends with the interpreter's interrupt code, not 1 (on Windows 0xC000013A, which
  PowerShell shows as -1073741510).
- ``--codes`` is a check. The access token comes first: without it nothing is asked or read and
  nothing goes to stdout (one line on stderr, exit 1). Then only those codes are asked and their
  computed rows printed as JSON, then one summary line. Nothing is written (no snapshot, no run
  record). Excess uses the market medians of every stored snapshot, blank when there is none. A
  code not in the stock list is not asked. Exit 0 when every code was received, else 1 (summary
  on stderr).

Heavy imports (the KIS client with its HTTP library, supabase-py) happen when the command runs:
``cli.py`` imports this module for every command. Output holds no key: KIS errors are masked by
the client. The command first sets stdout and stderr to write ``?`` for a character their
encoding lacks instead of failing: piped or redirected output on Korean Windows is cp949, and an
error text from KIS or the network can hold characters it has not (an en dash, ``é``). The
encoding stays; the run record keeps the text as it was.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional, Sequence

from research_desk.core import settings as core_settings
from research_desk.core.settings import NotReady
from research_desk.domain.stocks import StockEntry, StockList, StockListError

from . import logic, service, store
from . import settings as prices_settings

logger = logging.getLogger(__name__)

EXIT_NOT_READY = 4
# Calendar days of daily prices asked: 127 trading days (the 6-month return) with room for
# holidays and halts, still within two KIS pages of 100 rows.
LOOKBACK_DAYS = 240
KST = timezone(timedelta(hours=9), "KST")   # Korea keeps no daylight saving time
SOURCE = "KIS"

NOT_READY = "주가 갱신을 시작하지 않았습니다. 이유: {reason}"
KIS_NOT_CONFIGURED = "KIS 접속 설정(KIS_APP_KEY, KIS_APP_SECRET)이 없습니다"
STOCK_LIST_UNREADABLE = "종목표 파일을 읽을 수 없습니다 ({cause})"
NO_PRICES = "받은 시세가 없습니다"
NOT_LISTED = "종목표에 없는 종목코드입니다"
TOKEN_FAILED = "KIS 접근 토큰을 받지 못했습니다 ({reason})"
RUN_WITHOUT_TOKEN = TOKEN_FAILED + ". 아무 종목도 받지 않았고 스냅샷을 바꾸지 않았습니다"
CHECK_WITHOUT_TOKEN = "확인용 실행이라 아무것도 저장하지 않았습니다: " + TOKEN_FAILED + ". 아무 종목도 받지 않았습니다"
CRASHED = "실행 중 오류로 멈췄습니다: {error}"
STATUS_WORDS = {logic.OK: "완료", logic.PARTIAL: "일부 완료", logic.FAILED: "실패"}
MESSAGE_CODES = 20    # codes named in a run record's message
REASON_CHARS = 300    # a failure reason is cut to this length

_CODE = re.compile(r"[0-9A-Z]{6}")


class _NotReadyToRun(Exception):
    """A start-up check failed; ``str()`` is the reason."""


def utc_now() -> datetime:
    """The current time, aware UTC. Tests replace this name."""
    return datetime.now(timezone.utc)


def kis_client(cfg: prices_settings.PricesSettings) -> Any:
    """The KIS client of one run, paced at PRICES_MAX_CALLS_PER_SEC. Tests replace this name."""
    from research_desk.core import kis   # the HTTP library loads only when the command runs

    return kis.KisClient(cfg.app_key, cfg.app_secret, max_calls_per_sec=cfg.max_calls_per_sec,
                         base_url=cfg.base_url)


# ── the command ──────────────────────────────────────────────────────────────

def code_list(text: str) -> list[str]:
    """``--codes``: comma-separated 6-character codes (digits and capital letters; lower case is
    raised), each once. Anything else is an argument error (exit 2)."""
    codes = [part.strip().upper() for part in text.split(",") if part.strip()]
    if not codes:
        raise argparse.ArgumentTypeError("종목코드를 쉼표로 나눠 적어 주세요 (예: 005930,080220)")
    bad = [code for code in codes if not _CODE.fullmatch(code)]
    if bad:
        raise argparse.ArgumentTypeError(f"종목코드는 숫자와 영문 대문자 6자리입니다: {', '.join(bad)}")
    return list(dict.fromkeys(codes))


def register(subparsers) -> argparse.ArgumentParser:
    """Add ``prices`` and its subcommand ``update`` (``func(args) -> exit code``)."""
    prices = subparsers.add_parser("prices", help="주가 스냅샷: update",
                                   description="주가 스냅샷(한국투자증권 KIS) 관리")
    sub = prices.add_subparsers(dest="prices_command", required=True)
    description = ("종목표 전 종목의 주가를 한국투자증권(KIS)에서 받아 주가 스냅샷과 실행 기록을 갱신합니다. "
                   "종료 코드: 0 성공(ok, partial), 1 실패(failed), 4 준비 문제.")
    update_parser = sub.add_parser("update", help="주가 스냅샷 갱신", description=description)
    update_parser.add_argument("--codes", type=code_list, metavar="005930,080220",
                               help="확인용: 이 종목들만 받아 계산 결과를 출력하고 아무것도 저장하지 않습니다")
    update_parser.set_defaults(func=update)
    return prices


def _tolerant(stream: Any) -> None:
    """Set ``stream`` to write ``?`` for a character its encoding lacks (same object, same
    encoding); a stream without ``reconfigure`` (a ``StringIO``) is left as it is."""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(errors="replace")


def update(args: argparse.Namespace) -> int:
    """``prices update``: see the module docstring. Returns the exit code."""
    _tolerant(sys.stdout)
    _tolerant(sys.stderr)
    core_settings.load_env()
    try:
        stocks = _ready()
    except _NotReadyToRun as exc:
        print(NOT_READY.format(reason=exc), file=sys.stderr)
        return EXIT_NOT_READY
    cfg = prices_settings.load_settings()   # a malformed number: ValueError, before any call
    sb = service.connect()
    today = utc_now().astimezone(KST).date()
    window = (today - timedelta(days=LOOKBACK_DAYS), today)
    with kis_client(cfg) as kis:
        codes = getattr(args, "codes", None)
        if codes is not None:
            return _check(sb, kis, stocks, codes, window)
        return _run(sb, kis, _targets(stocks), window)


def _ready() -> StockList:
    """The start-up checks, in order: DB settings, KIS keys, the stock list."""
    try:
        service.db_settings()
    except NotReady as exc:
        raise _NotReadyToRun(exc.reason) from exc
    if not (prices_settings.app_key() and prices_settings.app_secret()):
        raise _NotReadyToRun(KIS_NOT_CONFIGURED)
    try:
        return StockList.load(core_settings.krx_csv_path())
    except StockListError as exc:   # the cause may name the file: shown on this PC's terminal only
        raise _NotReadyToRun(STOCK_LIST_UNREADABLE.format(cause=exc)) from exc


def _targets(stocks: StockList) -> list[StockEntry]:
    """Every stock of the list once (a repeated code keeps its last row), in file order; codes
    that are not 6 digits or capital letters are left out."""
    return [entry for entry in stocks.by_code.values() if stocks.validate_code(entry.code)]


def _token_problem(kis: Any) -> Optional[str]:
    """Get the run's KIS access token before any stock is asked: None when the client has it,
    else the reason (a KisError's text holds no key: the client masks it)."""
    try:
        kis.ensure_token()
    except Exception as exc:   # a KIS error or a strange answer: no stock can be asked without it
        return _reason(exc)
    return None


def _fetch(kis: Any, entry: StockEntry, start: date, end: date) -> tuple[Optional[dict], Optional[str]]:
    """``(snapshot row, None)`` for a received stock, ``(None, reason)`` for one not received."""
    try:
        daily = kis.daily_prices(entry.code, start, end)
        if not logic.usable_rows(daily):
            return None, NO_PRICES
        quote = kis.quote(entry.code)
    except Exception as exc:   # a KIS error or a strange answer fails this stock only
        return None, _reason(exc)
    return logic.snapshot_row(entry.code, entry.market, daily, quote), None


def _reason(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    return text[:REASON_CHARS]


# ── a full run ───────────────────────────────────────────────────────────────

def _run(sb: Any, kis: Any, entries: Sequence[StockEntry], window: tuple[date, date]) -> int:
    total = len(entries)
    run_id = store.start_run(sb, started_at=utc_now().isoformat(), total=total)
    try:
        problem = _token_problem(kis)
        if problem is not None:   # no stock is asked without the token, so no snapshot changes
            received, status, as_of = [], logic.FAILED, None
            outcome = message = RUN_WITHOUT_TOKEN.format(reason=problem)
        else:
            received, failures, skipped = _collect(kis, entries, window)
            status = logic.run_status(total, total - len(received))
            as_of = max((row["as_of"] for row in received), default=None)
            if status != logic.FAILED:
                _save(sb, received, list(failures))
            outcome, message = _outcome(status, skipped), _run_message(status, failures, skipped)
        failed = total - len(received)
        store.finish_run(sb, run_id, status=status, finished_at=utc_now().isoformat(), as_of=as_of,
                         stocks_ok=len(received), stocks_failed=failed, message=message)
    except BaseException as exc:
        _close_crashed(sb, run_id, exc)
        raise
    line = (f"주가 갱신 {STATUS_WORDS[status]}({status}): 기준일 {as_of or '없음'}, "
            f"성공 {len(received)}종목, 실패 {failed}종목")
    if outcome:
        line += f". {outcome}"
    print(line, file=sys.stderr if status == logic.FAILED else sys.stdout)
    return 0 if status in logic.SUCCESS_STATUSES else 1


def _collect(kis: Any, entries: Sequence[StockEntry],
             window: tuple[date, date]) -> tuple[list[dict], dict[str, str], int]:
    """Ask KIS stock by stock: ``(received rows, {code: reason} not received, codes not asked)``."""
    start, end = window
    received: list[dict] = []
    failures: dict[str, str] = {}
    for i, entry in enumerate(entries):
        row, reason = _fetch(kis, entry, start, end)
        if row is not None:
            received.append(row)
            continue
        failures[entry.code] = reason
        logger.warning("주가를 받지 못했습니다: %s (%s)", entry.code, reason)
        if logic.run_status(len(entries), len(failures)) == logic.FAILED:
            return received, failures, len(entries) - i - 1   # failed whatever comes next
    return received, failures, 0


def _save(sb: Any, received: list[dict], not_received: list[str]) -> None:
    """Upsert the received stocks with this run's excess; flag the stored rows of the others."""
    medians = logic.market_medians(received)
    written_at = utc_now().isoformat()
    store.upsert_snapshots(sb, [logic.with_excess(row, medians) | {"source": SOURCE, "updated_at": written_at}
                                for row in received])
    if not_received:
        stored = store.read_snapshots(sb, not_received, columns=("stock_code", "flags"))
        store.upsert_snapshots(sb, [{"stock_code": code, "flags": logic.with_no_data(stored[code].get("flags"))}
                                    for code in not_received if code in stored])


def _outcome(status: str, skipped: int) -> Optional[str]:
    """What the status meant for the snapshot (None for ok), for the summary line and the run
    message."""
    if status == logic.PARTIAL:
        return "못 받은 종목은 이전 값을 유지합니다"
    if status == logic.FAILED:
        text = f"못 받은 종목이 {logic.MAX_FAILED_PERCENT}%를 넘어 스냅샷을 바꾸지 않았습니다"
        if skipped:
            text += f". 기준을 넘은 뒤 남은 {skipped}종목은 받지 않았습니다"
        return text
    return None


def _run_message(status: str, failures: dict[str, str], skipped: int) -> Optional[str]:
    """The run record's message: none for ok, else the outcome, the codes and the first reason."""
    if status == logic.OK or not failures:
        return None
    codes = list(failures)
    named = ", ".join(codes[:MESSAGE_CODES])
    if len(codes) > MESSAGE_CODES:
        named += f" 외 {len(codes) - MESSAGE_CODES}종목"
    return (f"{_outcome(status, skipped)}. 못 받은 종목: {named}. "
            f"첫 실패 {codes[0]}: {failures[codes[0]]}")


def _close_crashed(sb: Any, run_id: int, exc: BaseException) -> None:
    """Close the run record as failed after an exception; the exception itself goes up."""
    try:
        store.finish_run(sb, run_id, status=logic.FAILED, finished_at=utc_now().isoformat(),
                         message=CRASHED.format(error=type(exc).__name__))
    except Exception as close_error:
        logger.warning("실행 기록 %s를 failed로 닫지 못했습니다: %s", run_id, type(close_error).__name__)


# ── --codes: a check that writes nothing ─────────────────────────────────────

def _check(sb: Any, kis: Any, stocks: StockList, codes: Sequence[str], window: tuple[date, date]) -> int:
    problem = _token_problem(kis)
    if problem is not None:   # nothing is asked or read without the token
        print(CHECK_WITHOUT_TOKEN.format(reason=problem), file=sys.stderr)
        return 1
    medians = logic.market_medians(store.read_all_returns(sb))
    start, end = window
    results: dict[str, dict] = {}
    for code in codes:
        entry = stocks.lookup(code)
        row, reason = (None, NOT_LISTED) if entry is None else _fetch(kis, entry, start, end)
        results[code] = (logic.with_excess(row, medians) if row is not None
                         else {"stock_code": code, "flags": [logic.NO_DATA], "error": reason})
    print(json.dumps(results, ensure_ascii=False, indent=2))
    received = [row for row in results.values() if "error" not in row]
    missed = len(results) - len(received)
    as_of = max((row["as_of"] for row in received), default=None)
    line = (f"확인용 실행이라 아무것도 저장하지 않았습니다: 기준일 {as_of or '없음'}, "
            f"받음 {len(received)}종목, 못 받음 {missed}종목")
    if all(median is None for periods in medians.values() for median in periods.values()):
        line += ". 저장된 스냅샷이 없어 초과수익률을 비웠습니다"
    print(line, file=sys.stderr if missed else sys.stdout)
    return 1 if missed else 0
