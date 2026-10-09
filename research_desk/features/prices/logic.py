"""Price calculations (spec §8). Pure: no DB, network, settings or files.

Units, the same in the DB and in the window: returns and excess returns are percent floats
(12.3 means +12.3 %), money (close, market cap, average trading value) is whole KRW.

- A stock's series is its KIS daily rows (adjusted prices) with a close above 0, oldest first, one
  per day. ``as_of`` is the series' last day and ``close`` that day's close.
- ``ret_<period>``: the close against the close N trading days earlier in the series, N = 5 / 21 /
  63 / 126 for 1w / 1m / 3m / 6m. It needs N+1 closes; with fewer it is null and the stock gets
  ``short_history``.
- ``avg_value_20d``: the mean trading value (거래대금) of the last 20 rows of the series (all of
  them when there are fewer), blank values left out, rounded half up to whole won; null when no
  row has one.
- ``traded`` is false when the quote says 거래정지 (flag ``halted``); ``admin_issue`` (관리종목 등)
  is only shown.
- ``xret_<period>`` = ret − the median of that period's returns among the stocks of the same run
  and the same market, counting only the stocks that have that return. KOSDAQ GLOBAL counts as
  KOSDAQ; a stock without a known market has no excess.
- Run status: no failure → ``ok``; failures up to ``MAX_FAILED_PERCENT`` (20) % of the stocks →
  ``partial``; more → ``failed``.
- ``no_data``: the stock was not received in this run; its stored row keeps its previous values
  and gets this flag.
"""
from __future__ import annotations

import statistics
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Mapping, Optional, Sequence

# (period, trading days back)
PERIODS: tuple[tuple[str, int], ...] = (("1w", 5), ("1m", 21), ("3m", 63), ("6m", 126))
# The periods the window gives (spec §12.1); 6m is stored only.
SCREEN_PERIODS: tuple[str, ...] = ("1w", "1m", "3m")
AVERAGE_VALUE_DAYS = 20
# A run with more than this percent of its stocks not received is failed (adjustable).
MAX_FAILED_PERCENT = 20

RUNNING, OK, PARTIAL, FAILED = "running", "ok", "partial", "failed"
SUCCESS_STATUSES: tuple[str, ...] = (OK, PARTIAL)

NO_DATA, SHORT_HISTORY, HALTED, ADMIN_ISSUE = "no_data", "short_history", "halted", "admin_issue"
FLAG_ORDER: tuple[str, ...] = (NO_DATA, SHORT_HISTORY, HALTED, ADMIN_ISSUE)

MARKETS: tuple[str, ...] = ("KOSPI", "KOSDAQ")
_MARKET_OF = {"KOSPI": "KOSPI", "KOSDAQ": "KOSDAQ", "KOSDAQ GLOBAL": "KOSDAQ"}


def ret_column(period: str) -> str:
    return f"ret_{period}"


def xret_column(period: str) -> str:
    return f"xret_{period}"


def market_of(listed: Optional[str]) -> Optional[str]:
    """The snapshot market of a stock-list market: KOSPI, KOSDAQ (KOSDAQ GLOBAL too), else None."""
    return _MARKET_OF.get((listed or "").strip())


# ── one stock ────────────────────────────────────────────────────────────────

def usable_rows(rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The series: rows with a close above 0, oldest first, the last row of a day kept."""
    by_day: dict[date, Mapping[str, Any]] = {}
    for row in rows:
        close = row.get("close")
        if close is not None and close > 0:
            by_day[row["date"]] = row
    return [by_day[day] for day in sorted(by_day)]


def period_return(closes: Sequence[float], days: int) -> Optional[float]:
    """Percent change of the last close against the close ``days`` entries earlier; None when
    there are not ``days`` + 1 closes."""
    if days < 1 or len(closes) <= days:
        return None
    base, last = closes[-1 - days], closes[-1]
    return (last - base) * 100 / base


def average_value(rows: Sequence[Mapping[str, Any]], days: int = AVERAGE_VALUE_DAYS) -> Optional[int]:
    """Mean trading value of the last ``days`` rows, blanks left out, rounded half up; None
    without any value."""
    values = [row["trading_value"] for row in rows[-days:] if row.get("trading_value") is not None]
    if not values:
        return None
    total, count = sum(values), len(values)
    return int((2 * total + count) // (2 * count))


def snapshot_row(code: str, listed_market: Optional[str], daily_rows: Iterable[Mapping[str, Any]],
                 quote: Mapping[str, Any]) -> Optional[dict]:
    """The stored row of one received stock, excess still null; None when no row has a close.

    ``daily_rows``: ``KisClient.daily_prices`` rows. ``quote``: ``KisClient.quote``.
    """
    series = usable_rows(daily_rows)
    if not series:
        return None
    closes = [row["close"] for row in series]
    halted = bool(quote.get("halted"))
    row: dict[str, Any] = {
        "stock_code": code,
        "market": market_of(listed_market),
        "as_of": series[-1]["date"].isoformat(),
        "close": int(closes[-1]),
        "market_cap": quote.get("market_cap"),
        "avg_value_20d": average_value(series),
        "traded": not halted,
    }
    for period, days in PERIODS:
        row[ret_column(period)] = period_return(closes, days)
    for period, _ in PERIODS:
        row[xret_column(period)] = None
    flags = []
    if any(row[ret_column(period)] is None for period, _ in PERIODS):
        flags.append(SHORT_HISTORY)
    if halted:
        flags.append(HALTED)
    if quote.get("admin_issue"):
        flags.append(ADMIN_ISSUE)
    row["flags"] = flags
    return row


# ── market medians and excess ────────────────────────────────────────────────

def market_medians(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Optional[float]]]:
    """``{market: {period: median}}`` of the rows' returns; only rows with that return count."""
    found: dict[str, dict[str, list[float]]] = {
        market: {period: [] for period, _ in PERIODS} for market in MARKETS}
    for row in rows:
        market = market_of(row.get("market"))
        if market is None:
            continue
        for period, _ in PERIODS:
            value = row.get(ret_column(period))
            if value is not None:
                found[market][period].append(float(value))
    return {market: {period: statistics.median(values) if values else None
                     for period, values in periods.items()}
            for market, periods in found.items()}


def with_excess(row: Mapping[str, Any], medians: Mapping[str, Mapping[str, Optional[float]]]) -> dict:
    """A copy of ``row`` with ``xret_*`` = ret − its market's median (null when either is missing)."""
    out = dict(row)
    market = medians.get(market_of(row.get("market"))) or {}
    for period, _ in PERIODS:
        value, median = row.get(ret_column(period)), market.get(period)
        out[xret_column(period)] = None if value is None or median is None else float(value) - median
    return out


# ── runs and flags ───────────────────────────────────────────────────────────

def run_status(total: int, failed: int) -> str:
    """``ok`` without failures, ``partial`` up to MAX_FAILED_PERCENT % failed, else ``failed``."""
    if failed <= 0:
        return OK
    if failed * 100 <= MAX_FAILED_PERCENT * total:
        return PARTIAL
    return FAILED


def with_no_data(flags: Optional[Iterable[str]]) -> list[str]:
    """``flags`` plus ``no_data``, known flags in FLAG_ORDER, unknown ones after them."""
    present = list(dict.fromkeys([NO_DATA, *(flags or [])]))
    return ([flag for flag in FLAG_ORDER if flag in present]
            + [flag for flag in present if flag not in FLAG_ORDER])


# ── the window shape ─────────────────────────────────────────────────────────

def price_view(row: Mapping[str, Any]) -> dict:
    """A stored row as the window gives it: spec §12.1 ``price`` plus ``market``."""
    return {
        "market": row.get("market"),
        "as_of": _day_text(row.get("as_of")),
        "close": _whole(row.get("close")),
        "market_cap": _whole(row.get("market_cap")),
        "avg_value_20d": _whole(row.get("avg_value_20d")),
        "traded": row.get("traded"),
        "returns": {period: _number(row.get(ret_column(period))) for period in SCREEN_PERIODS},
        "excess": {period: _number(row.get(xret_column(period))) for period in SCREEN_PERIODS},
        "flags": list(row.get("flags") or []),
    }


def wanted_codes(codes: Iterable[str]) -> list[str]:
    """Each code once, in the given order; codes that are not plain letters and digits are left
    out. One plain string is a TypeError (iterating it would ask for its characters)."""
    if isinstance(codes, str):
        raise TypeError("codes must be a collection of stock codes, not one string")
    return [code for code in dict.fromkeys(codes) if isinstance(code, str) and code.isalnum()]


def _whole(value: Any) -> Optional[int]:
    """A DB number (int, float or numeric text) as whole won, rounded half up."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    return int(Decimal(str(value)).to_integral_value(rounding=ROUND_HALF_UP))


def _number(value: Any) -> Optional[float]:
    return None if value is None else float(value)


def _day_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, date) else str(value)
