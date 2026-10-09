"""features.prices logic: the per-stock values, market medians and excess, run status (spec §8).

Pure functions, no DB, network, settings or files. Boundary values checked here:
- a return over N trading days needs N+1 closes (the latest and the one N trading days back):
  N+1 closes give the return, N closes give null and ``short_history``;
- the 20-day average trading value, blank values, fewer than 20 rows;
- halted / admin_issue flags and their order;
- the market median: KOSDAQ GLOBAL counted as KOSDAQ, stocks without that period's return left
  out, a market nobody has a return in gives no median (so no excess);
- run status at the 20 % boundary (ok / partial / failed).
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from research_desk.features.prices import logic

START = date(2026, 1, 5)  # a Monday
QUOTE = {"market_cap": 5_000_000_000, "listed_shares": 1_000_000, "halted": False, "admin_issue": False}


def daily(closes, values=None, start=START):
    """KIS daily rows (oldest first) for these closes, one per weekday from ``start``."""
    rows, day = [], start
    for i, close in enumerate(closes):
        while day.weekday() >= 5:
            day += timedelta(days=1)
        rows.append({"date": day, "close": close, "volume": 1000,
                     "trading_value": 1_000_000 if values is None else values[i]})
        day += timedelta(days=1)
    return rows


def snapshot(closes, quote=QUOTE, market="KOSPI", **kwargs):
    return logic.snapshot_row("005930", market, daily(closes, **kwargs), quote)


# ── returns ──────────────────────────────────────────────────────────────────

def test_the_periods_are_5_21_63_and_126_trading_days():
    assert logic.PERIODS == (("1w", 5), ("1m", 21), ("3m", 63), ("6m", 126))


@pytest.mark.parametrize("period,days", logic.PERIODS)
def test_a_return_needs_the_close_n_trading_days_back(period, days):
    closes = [100] + [105] * (days - 1) + [110]          # days + 1 closes
    assert logic.period_return(closes, days) == 10.0     # percent
    assert logic.period_return(closes[1:], days) is None  # one fewer


def test_returns_are_percent_against_the_close_n_days_back():
    assert logic.period_return([200, 150], 1) == -25.0
    assert logic.period_return([3, 4], 1) == pytest.approx(33.333333333)
    assert logic.period_return([110, 1, 1, 1, 1, 121], 5) == 10.0   # only the two ends count


@pytest.mark.parametrize("period,days", logic.PERIODS)
def test_a_snapshot_has_the_return_from_n_plus_1_rows_and_null_from_n(period, days):
    enough = snapshot([100] + [120] * days)
    assert enough[f"ret_{period}"] == 20.0
    short = snapshot([100] + [120] * (days - 1))
    assert short[f"ret_{period}"] is None
    assert logic.SHORT_HISTORY in short["flags"]


def test_short_history_is_set_while_any_period_lacks_rows():
    full = snapshot([100] * 127)
    assert full["flags"] == []
    assert [full[f"ret_{p}"] for p, _ in logic.PERIODS] == [0.0, 0.0, 0.0, 0.0]
    short = snapshot([100] * 126)
    assert short["flags"] == [logic.SHORT_HISTORY]
    assert [short[f"ret_{p}"] for p, _ in logic.PERIODS] == [0.0, 0.0, 0.0, None]


def test_blank_and_zero_closes_are_not_trading_days_of_the_series():
    rows = daily([100, None, 0, 110, 121])
    assert [row["close"] for row in logic.usable_rows(rows)] == [100, 110, 121]
    row = logic.snapshot_row("005930", "KOSPI", rows, QUOTE)
    assert row["ret_1w"] is None              # 3 usable closes only
    assert logic.period_return([100, 110, 121], 2) == 21.0


def test_rows_are_put_in_date_order_once_per_day():
    rows = list(reversed(daily([100, 110, 121])))
    rows.append(dict(rows[0]))   # the newest day again
    assert [row["close"] for row in logic.usable_rows(rows)] == [100, 110, 121]


# ── the other values of a stock ──────────────────────────────────────────────

def test_a_snapshot_row_holds_the_latest_day_and_the_quote():
    row = snapshot([100] * 126 + [130])
    assert row == {
        "stock_code": "005930", "market": "KOSPI", "as_of": daily([1] * 127)[-1]["date"].isoformat(),
        "close": 130, "market_cap": 5_000_000_000, "avg_value_20d": 1_000_000, "traded": True,
        "ret_1w": 30.0, "ret_1m": 30.0, "ret_3m": 30.0, "ret_6m": 30.0,
        "xret_1w": None, "xret_1m": None, "xret_3m": None, "xret_6m": None,
        "flags": [],
    }


def test_no_usable_row_gives_no_snapshot():
    assert logic.snapshot_row("005930", "KOSPI", [], QUOTE) is None
    assert logic.snapshot_row("005930", "KOSPI", daily([None, 0]), QUOTE) is None


def test_the_20_day_average_value_uses_the_last_20_rows():
    rows = daily([100] * 25, values=list(range(1, 26)))
    assert logic.average_value(rows) == 16     # (6 + … + 25) / 20 = 15.5, rounded half up
    assert snapshot([100] * 25, values=list(range(1, 26)))["avg_value_20d"] == 16


def test_the_average_value_of_fewer_rows_and_blank_values():
    assert logic.average_value(daily([100] * 3, values=[10, 20, 31])) == 20   # 61 / 3
    assert logic.average_value(daily([100] * 3, values=[None, 10, 20])) == 15
    assert logic.average_value(daily([100] * 2, values=[None, None])) is None
    assert logic.average_value([]) is None


def test_a_halted_stock_is_not_traded():
    row = snapshot([100] * 127, quote=QUOTE | {"halted": True})
    assert row["traded"] is False
    assert row["flags"] == [logic.HALTED]


def test_an_admin_issue_is_only_a_flag():
    row = snapshot([100] * 127, quote=QUOTE | {"admin_issue": True})
    assert row["traded"] is True
    assert row["flags"] == [logic.ADMIN_ISSUE]


def test_flags_come_in_a_fixed_order():
    row = snapshot([100] * 10, quote=QUOTE | {"halted": True, "admin_issue": True})
    assert row["flags"] == [logic.SHORT_HISTORY, logic.HALTED, logic.ADMIN_ISSUE]
    assert logic.FLAG_ORDER == ("no_data", "short_history", "halted", "admin_issue")


def test_a_blank_market_cap_stays_blank():
    assert snapshot([100] * 127, quote=QUOTE | {"market_cap": None})["market_cap"] is None


@pytest.mark.parametrize("listed,market", [
    ("KOSPI", "KOSPI"), ("KOSDAQ", "KOSDAQ"), ("KOSDAQ GLOBAL", "KOSDAQ"), (" KOSDAQ GLOBAL ", "KOSDAQ"),
    ("KONEX", None), ("", None), (None, None),
])
def test_kosdaq_global_counts_as_kosdaq(listed, market):
    assert logic.market_of(listed) == market
    assert snapshot([100] * 127, market=listed)["market"] == market


# ── market medians and excess returns ────────────────────────────────────────

def returns(market, **values):
    return {"market": market, **{f"ret_{p}": values.get(p) for p, _ in logic.PERIODS}}


def test_the_market_median_leaves_out_stocks_without_that_return():
    medians = logic.market_medians([
        returns("KOSPI", **{"1w": 1.0}), returns("KOSPI", **{"1w": 3.0}), returns("KOSPI"),
        returns("KOSPI", **{"1w": 100.0}),
        returns("KOSDAQ", **{"1w": -2.0, "1m": 5.0}), returns("KOSDAQ GLOBAL", **{"1w": 4.0}),
        returns(None, **{"1w": 50.0, "3m": 7.0}),   # no market: in no median
    ])
    assert medians["KOSPI"] == {"1w": 3.0, "1m": None, "3m": None, "6m": None}
    assert medians["KOSDAQ"] == {"1w": 1.0, "1m": 5.0, "3m": None, "6m": None}   # GLOBAL folded in
    assert set(medians) == {"KOSPI", "KOSDAQ"}


def test_an_even_count_takes_the_mean_of_the_two_middle_values():
    medians = logic.market_medians([returns("KOSPI", **{"1w": v}) for v in (4.0, 1.0, 3.0, 10.0)])
    assert medians["KOSPI"]["1w"] == 3.5


def test_no_rows_give_no_medians():
    assert logic.market_medians([]) == {m: {p: None for p, _ in logic.PERIODS} for m in logic.MARKETS}


def test_excess_is_the_return_minus_its_market_median():
    medians = {"KOSPI": {"1w": 9.0, "1m": 9.0, "3m": 9.0, "6m": 9.0},
               "KOSDAQ": {"1w": 1.0, "1m": 2.0, "3m": None, "6m": 0.5}}
    row = returns("KOSDAQ", **{"1w": 5.0, "3m": 2.0, "6m": 1.0}) | {"stock_code": "080220"}
    out = logic.with_excess(row, medians)
    assert (out["xret_1w"], out["xret_1m"], out["xret_3m"], out["xret_6m"]) == (4.0, None, None, 0.5)
    assert out["stock_code"] == "080220" and row.get("xret_1w") is None   # a new dict


def test_a_stock_without_a_market_has_no_excess():
    medians = {m: {p: 0.0 for p, _ in logic.PERIODS} for m in logic.MARKETS}
    out = logic.with_excess(returns(None, **{"1w": 5.0}), medians)
    assert [out[f"xret_{p}"] for p, _ in logic.PERIODS] == [None, None, None, None]


def test_excess_in_one_run_folds_kosdaq_global_into_kosdaq():
    run = [logic.snapshot_row(code, market, daily([100] * 126 + [close]), QUOTE)
           for code, market, close in (("000001", "KOSDAQ", 110), ("000002", "KOSDAQ GLOBAL", 130),
                                       ("000003", "KOSDAQ", 150), ("000004", "KOSPI", 90))]
    medians = logic.market_medians(run)
    excess = {row["stock_code"]: logic.with_excess(row, medians)["xret_1m"] for row in run}
    assert excess == {"000001": -20.0, "000002": 0.0, "000003": 20.0, "000004": 0.0}


# ── run status ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("total,failed,status", [
    (10, 0, "ok"), (10, 1, "partial"), (10, 2, "partial"), (10, 3, "failed"),
    (5, 1, "partial"), (5, 2, "failed"), (2558, 511, "partial"), (2558, 512, "failed"),
    (1, 1, "failed"), (0, 0, "ok"),
])
def test_more_than_20_percent_failed_is_a_failed_run(total, failed, status):
    assert logic.run_status(total, failed) == status


def test_the_failed_share_is_a_module_constant(monkeypatch):
    assert logic.MAX_FAILED_PERCENT == 20
    monkeypatch.setattr(logic, "MAX_FAILED_PERCENT", 30)
    assert logic.run_status(10, 3) == "partial"


# ── no_data, the window shape, codes ────────────────────────────────────────

@pytest.mark.parametrize("flags,marked", [
    ([], ["no_data"]),
    (["halted"], ["no_data", "halted"]),
    (["no_data", "short_history"], ["no_data", "short_history"]),
    (["admin_issue", "short_history"], ["no_data", "short_history", "admin_issue"]),
    (["later_flag", "halted"], ["no_data", "halted", "later_flag"]),
    (None, ["no_data"]),
])
def test_no_data_is_added_to_the_flags_a_row_had(flags, marked):
    assert logic.with_no_data(flags) == marked


STORED = {
    "stock_code": "005930", "market": "KOSPI", "as_of": "2026-10-08", "close": 61000,
    "market_cap": 364_000_000_000_000, "avg_value_20d": 1_234_567_890, "traded": True,
    "ret_1w": 1.5, "ret_1m": 2.5, "ret_3m": None, "ret_6m": 9.0,
    "xret_1w": 0.5, "xret_1m": -1.0, "xret_3m": None, "xret_6m": 3.0,
    "flags": ["short_history"], "source": "KIS", "updated_at": "2026-10-08T09:40:00+00:00",
}


def test_the_window_shape_is_the_screen_price_plus_market():
    assert logic.price_view(STORED) == {
        "market": "KOSPI", "as_of": "2026-10-08", "close": 61000,
        "market_cap": 364_000_000_000_000, "avg_value_20d": 1_234_567_890, "traded": True,
        "returns": {"1w": 1.5, "1m": 2.5, "3m": None},
        "excess": {"1w": 0.5, "1m": -1.0, "3m": None},
        "flags": ["short_history"],
    }


def test_the_window_shape_makes_db_numbers_ints_and_floats():
    view = logic.price_view(STORED | {"close": "61000", "market_cap": 3.64e14, "avg_value_20d": "1234567890.4",
                                      "ret_1w": 2, "flags": None, "traded": None})
    assert (view["close"], view["market_cap"], view["avg_value_20d"]) == (61000, 364_000_000_000_000,
                                                                         1_234_567_890)
    assert isinstance(view["returns"]["1w"], float) and view["returns"]["1w"] == 2.0
    assert view["flags"] == [] and view["traded"] is None


def test_an_empty_row_gives_nulls():
    view = logic.price_view({"stock_code": "005930"})
    assert view["close"] is None and view["as_of"] is None and view["market"] is None
    assert view["returns"] == {"1w": None, "1m": None, "3m": None}


def test_wanted_codes_keep_the_order_once_each():
    assert logic.wanted_codes(["080220", "005930", "080220", "", " 1", "0126Z0", None, 5930]) == [
        "080220", "005930", "0126Z0"]


def test_one_plain_string_is_not_a_list_of_codes():
    with pytest.raises(TypeError):
        logic.wanted_codes("005930")
