"""Coverage calculations: pandas aggregation of report rows and the two response payloads.

No DB, network, settings or files here: every function takes a DataFrame of report rows (the
reports window's 16 columns) and returns a DataFrame for a chart, or a payload dict. Ported
unchanged in behaviour from langgraph_tagger/analytics/aggregate.py (the aggregations) and
langgraph_tagger/workspace/coverage.py (``records``, ``market_payload``,
``stock_activity_payload``), plus ``period_start`` from the old web app.

Effective date (spec §9.7): every time-bucketed aggregation counts a row on its effective date =
published_at, else sent_at converted to the Korean (KST) date. Out-of-scope rows have
published_at NULL (the tagger sets it NULL for them), so without this fallback the report type
volume with out-of-scope rows included would silently drop those rows.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

KST = ZoneInfo('Asia/Seoul')


def period_start(days: int, now: Optional[datetime] = None) -> str:
    """First day of the period, YYYY-MM-DD: today in Korea minus ``days`` days.

    ``now`` (an aware datetime, any zone) stands in for the current time; tests pass it.
    """
    current = datetime.now(KST) if now is None else now.astimezone(KST)
    return (current - timedelta(days=days)).date().isoformat()


# ── aggregations (analytics/aggregate.py) ────────────────────────────────────

def _ensure_effective_date(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with an 'effective_date' column derived as:
       published_at  if not null,
       else sent_at converted to KST (UTC+9) and floored to the date.
    """
    out = df.copy()
    pub = pd.to_datetime(out['published_at'], errors='coerce')
    sent = pd.to_datetime(out['sent_at'], errors='coerce', utc=True)
    sent_kst = (sent.dt.tz_convert('Asia/Seoul')
                    .dt.tz_localize(None)
                    .dt.normalize())
    out['effective_date'] = pub.fillna(sent_kst)
    return out


def _floor_to_unit(s: pd.Series, unit: str) -> pd.Series:
    """Floor a datetime series to the unit: D (day), W (week), M (month).

    Uses period alias (not frequency alias). 'MS' is a frequency alias
    (Month Start offset) and is NOT a valid argument to Series.dt.to_period;
    use 'M' and let .dt.start_time give the first day of that month.
    """
    mapping = {'D': 'D', 'W': 'W', 'M': 'M'}
    freq = mapping.get(unit, 'D')
    return s.dt.to_period(freq).dt.start_time


def sector_timeseries(df: pd.DataFrame,
                       level: str,
                       items: list[str],
                       unit: str,
                       top_n: int = 10) -> pd.DataFrame:
    """For each (bucket, sector), return count.

    - level: one of 'sectors_major', 'sectors_minor', 'products'
    - items: selected sector names (overlap match). Empty → top N by volume.
    - unit: 'D', 'W', 'M'
    Output columns: ['bucket', 'sector', 'count']
    """
    if df.empty:
        return pd.DataFrame(columns=['bucket', 'sector', 'count'])
    d = _ensure_effective_date(df)
    d = d[d[level].apply(lambda lst: isinstance(lst, list) and len(lst) > 0)]
    if d.empty:
        return pd.DataFrame(columns=['bucket', 'sector', 'count'])
    if items:
        d = d[d[level].apply(lambda lst: any(s in items for s in lst))]
    exploded = d.assign(sector=d[level]).explode('sector')
    if items:
        exploded = exploded[exploded['sector'].isin(items)]
    else:
        top = (exploded.groupby('sector').size()
                       .nlargest(top_n).index.tolist())
        exploded = exploded[exploded['sector'].isin(top)]
    exploded['bucket'] = _floor_to_unit(exploded['effective_date'], unit)
    return (exploded.groupby(['bucket', 'sector']).size()
                    .reset_index(name='count'))


def sector_ranking(df: pd.DataFrame,
                    level: str,
                    items: list[str],
                    limit: int = 20) -> pd.DataFrame:
    """For rows matching the sector filter, unnest stock_codes and count desc.

    Output columns: ['code', 'count']
    """
    if df.empty:
        return pd.DataFrame(columns=['code', 'count'])
    d = df[df['stock_codes'].apply(lambda lst: isinstance(lst, list) and len(lst) > 0)]
    if items:
        d = d[d[level].apply(lambda lst: any(s in items for s in (lst or [])))]
    if d.empty:
        return pd.DataFrame(columns=['code', 'count'])
    codes = d['stock_codes'].explode()
    return (codes.value_counts()
                  .head(limit)
                  .rename_axis('code')
                  .reset_index(name='count'))


def report_type_timeseries(df: pd.DataFrame,
                            unit: str,
                            include_oos: bool = False) -> pd.DataFrame:
    """For each (bucket, report_type), return count.

    If include_oos is False, exclude rows with out_of_scope_reason set.
    Output columns: ['bucket', 'report_type', 'count']
    """
    if df.empty:
        return pd.DataFrame(columns=['bucket', 'report_type', 'count'])
    d = _ensure_effective_date(df)
    if not include_oos:
        d = d[d['out_of_scope_reason'].isna()]
    if d.empty:
        return pd.DataFrame(columns=['bucket', 'report_type', 'count'])
    d['bucket'] = _floor_to_unit(d['effective_date'], unit)
    return (d.groupby(['bucket', 'report_type']).size()
             .reset_index(name='count'))


def stock_monthly(df: pd.DataFrame, code: str, unit: str) -> pd.DataFrame:
    """For a single stock, time-bucketed count.

    Output columns: ['bucket', 'count']
    """
    if df.empty:
        return pd.DataFrame(columns=['bucket', 'count'])
    d = df[df['stock_codes'].apply(lambda lst: isinstance(lst, list) and code in lst)]
    if d.empty:
        return pd.DataFrame(columns=['bucket', 'count'])
    d = _ensure_effective_date(d)
    d['bucket'] = _floor_to_unit(d['effective_date'], unit)
    return d.groupby('bucket').size().reset_index(name='count')


def publisher_dist(df: pd.DataFrame, top_k: int = 5) -> pd.DataFrame:
    """Top K publishers by row count; rest grouped into '기타'.

    Output columns: ['publisher', 'count']
    """
    if df.empty:
        return pd.DataFrame(columns=['publisher', 'count'])
    counts = df['publisher'].value_counts()
    if len(counts) <= top_k:
        return counts.rename_axis('publisher').reset_index(name='count')
    top = counts.head(top_k).rename_axis('publisher').reset_index(name='count')
    others = pd.DataFrame([{
        'publisher': '기타',
        'count': int(counts.iloc[top_k:].sum()),
    }])
    return pd.concat([top, others], ignore_index=True)


# ── payloads (workspace/coverage.py) ─────────────────────────────────────────

def records(frame):
    """A DataFrame as JSON-ready records; timestamps become ISO strings."""
    return json.loads(frame.to_json(orient='records', date_format='iso'))


def market_payload(df, names, level, items, unit, include_oos):
    """``GET /api/market`` body for the period's rows ``df``.

    ``names`` is a DataFrame with ``code`` and ``name`` columns (the stock list): the ranking's
    codes get their names from it, '' for a code it does not have.
    """
    inscope = df[df['out_of_scope_reason'].isna()]
    available = sorted({value for values in inscope[level] for value in (values if isinstance(values, list) else []) if value})
    rank = sector_ranking(inscope, level, items)
    if not rank.empty:
        rank = rank.merge(names[['code', 'name']], on='code', how='left').fillna('')
    dated = _ensure_effective_date(df)
    dates = dated['effective_date'].dropna()
    return {
        'total': len(df), 'inscope': len(inscope), 'oos': len(df) - len(inscope),
        'publishers': int(df['publisher'].nunique()),
        'latest': dates.max().date().isoformat() if len(dates) else None,
        'earliest': dates.min().date().isoformat() if len(dates) else None,
        'available_items': available,
        'coverage': records(sector_timeseries(inscope, level, items, unit)),
        'ranking': records(rank),
        'types': records(report_type_timeseries(df, unit, include_oos)),
    }


def stock_activity_payload(df, code, unit):
    """``GET /api/stocks/{code}/activity`` body for the company's rows ``df``."""
    return {'timeline': records(stock_monthly(df, code, unit)),
            'publishers': records(publisher_dist(df)), 'total': len(df)}
