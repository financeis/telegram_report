"""Freshness rules (spec §12.3): how current the price snapshot and the newest report are.

Pure: no DB, network, settings or files. Every function takes the window values (the prices
window's ``latest_run()`` answer, the reports window's ``latest_report_sent_at()``) and "now" as
an aware datetime in any zone; Korean time (KST) is what counts.

Expected trading day (``expected_trading_day``): today when now is a weekday at or after
``CUTOFF`` (18:30, when the daily price run starts), else the nearest weekday before today. So a
weekday before 18:30 expects the weekday before (Monday morning: last Friday), and Saturday and
Sunday expect Friday. The rule knows weekdays only, not market holidays.

Prices (``price_freshness``) are stale, with a ``note`` sentence giving the reason, in the first
case that fits:

1. no successful (``ok`` / ``partial``) run ever — no run at all, or only failed or running runs
   (``as_of`` is None): ``NOTE_NEVER_UPDATED``;
2. the latest run failed, whatever ``as_of`` is: ``NOTE_LAST_RUN_FAILED``;
3. ``as_of`` is before the expected trading day: ``NOTE_BEHIND`` with the as_of day ("10월 7일"),
   then ``HOLIDAY_HINT``. A weekday market holiday makes this rule warn wrongly from 18:30 that
   day until the next trading day's prices are in, and the rule cannot tell, so the hint comes
   with every such note.

Otherwise they are fresh and ``note`` is None: stale exactly when there is a note. A ``running``
latest run is not stale by itself; it is judged by ``as_of`` like any other, so the daily run
that has started at 18:30 but not finished yet shows the as_of note until it ends. An ``as_of``
after the expected trading day (a run by hand during trading hours) is fresh.

Reports (``report_freshness``) are stale when there is no in-scope report, or when the newest
in-scope report's sent_at is more than ``REPORT_STALE_DAYS`` (3) days before now — exactly 3 days
is not stale. A sent_at after now is not stale.

Output (``freshness_payload``, spec §12.3's keys in its order): times as ISO text in Korea cut to
the second (``2026-10-08T09:12:00+09:00``), ``as_of`` as YYYY-MM-DD, None for what is not known.

``CUTOFF``, ``REPORT_STALE_DAYS`` and the sentences are adjustable here; the rules read them when
they run.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')

# ── the adjustable values ────────────────────────────────────────────────────
CUTOFF = time(18, 30)       # from this time on a weekday, that day's prices are expected
REPORT_STALE_DAYS = 3       # reports are stale when the newest is older than this many days

# The prices window's status of a failed run (the prices feature's value; a test checks it).
FAILED = 'failed'

# ── note sentences (Korean, fixed; the screen shows them in the status line) ─
NOTE_NEVER_UPDATED = '주가가 아직 한 번도 갱신되지 않았습니다'
NOTE_LAST_RUN_FAILED = '마지막 주가 갱신이 실패했습니다'
NOTE_BEHIND = '주가가 {day} 기준으로 밀려 있습니다'
HOLIDAY_HINT = '휴장일이면 정상입니다'


def expected_trading_day(now: datetime) -> date:
    """The day whose prices should be in by ``now`` (aware, any zone), counted in Korea: today on
    a weekday at or after ``CUTOFF``, else the nearest weekday before today."""
    local = now.astimezone(KST)
    today = local.date()
    if today.weekday() < 5 and local.time() >= CUTOFF:
        return today
    day = today - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def korean_day(day: date) -> str:
    """``10월 7일``."""
    return f'{day.month}월 {day.day}일'


def iso_kst(moment: Optional[datetime]) -> Optional[str]:
    """An aware time as ISO text in Korea cut to the second (``2026-10-08T09:12:00+09:00``); None
    stays None."""
    if moment is None:
        return None
    return moment.astimezone(KST).isoformat(timespec='seconds')


def price_note(status: Optional[str], as_of: Optional[date], now: datetime) -> Optional[str]:
    """Why the prices are stale (the first rule that fits), or None when they are fresh."""
    if as_of is None:
        return NOTE_NEVER_UPDATED
    if status == FAILED:
        return NOTE_LAST_RUN_FAILED
    if as_of < expected_trading_day(now):
        return f'{NOTE_BEHIND.format(day=korean_day(as_of))}. {HOLIDAY_HINT}'
    return None


def price_freshness(run: Optional[Mapping[str, Any]], now: datetime) -> dict:
    """``{"as_of", "last_run_at", "last_run_status", "stale", "note"}`` for the prices window's
    ``latest_run()`` answer ``run`` (None without any run)."""
    last_run_at = run['last_run_at'] if run is not None else None
    status = run['last_run_status'] if run is not None else None
    as_of = run['as_of'] if run is not None else None
    note = price_note(status, as_of, now)
    return {
        'as_of': as_of.isoformat() if as_of is not None else None,
        'last_run_at': iso_kst(last_run_at),
        'last_run_status': status,
        'stale': note is not None,
        'note': note,
    }


def report_freshness(latest_at: Optional[datetime], now: datetime) -> dict:
    """``{"latest_at", "stale"}`` for the newest in-scope report's sent_at (None without one)."""
    stale = latest_at is None or now - latest_at > timedelta(days=REPORT_STALE_DAYS)
    return {'latest_at': iso_kst(latest_at), 'stale': stale}


def freshness_payload(run: Optional[Mapping[str, Any]], latest_at: Optional[datetime],
                      now: datetime) -> dict:
    """The ``GET /api/freshness`` body: ``{"prices": …, "reports": …, "checked_at"}``."""
    return {
        'prices': price_freshness(run, now),
        'reports': report_freshness(latest_at, now),
        'checked_at': iso_kst(now),
    }
