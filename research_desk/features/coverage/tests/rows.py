"""Report rows for the coverage tests, shaped like the reports window's frames.

``frame()`` is the helper of langgraph_tagger/workspace/tests/test_migration.py: two in-scope
reports of 016360 (단일종목 and 산업, published 2026-05-11) and one out-of-scope row with no
published_at, sent 2026-05-11T16:00Z = 2026-05-12 in Korea.

``report()`` / ``frame_of()``: rows for the report counts of companies, one field at a time.
"""
from __future__ import annotations

import pandas as pd

# The 16 columns of every frame the reports window returns (period_rows / stock_rows /
# rows_for_stocks).
REPORT_COLUMNS: tuple[str, ...] = (
    'id', 'published_at', 'sent_at', 'report_type', 'publisher',
    'stock_codes', 'company_names', 'sectors_major', 'sectors_minor',
    'products', 'tagging_status', 'out_of_scope_reason', 'file_path',
    'file_name', 'title', 'publisher_type',
)


def frame() -> pd.DataFrame:
    base = dict.fromkeys(REPORT_COLUMNS)
    base.update(stock_codes=['016360'], company_names=['삼성증권'],
                sectors_major=['금융'], sectors_minor=['증권'], products=['증권'],
                publisher='KB', report_type='단일종목', tagging_status='auto',
                published_at='2026-05-11', sent_at='2026-05-11T01:00:00Z')
    return pd.DataFrame([base | {'id': 1}, base | {'id': 2, 'report_type': '산업'},
                         base | {'id': 3, 'out_of_scope_reason': 'foreign',
                                 'published_at': None, 'report_type': '기타',
                                 'stock_codes': [], 'sent_at': '2026-05-11T16:00:00Z'}])


def wider_frame() -> pd.DataFrame:
    """More rows over several weeks, sectors, products, publishers and stocks (999999 is not in
    the test stock lists), so different level / unit / items give different answers. Stock counts
    differ (005930 ×3, 000660 ×2, 999999 ×1), so ranking orders never depend on ties."""
    rows = []
    specs = [
        # id, published_at, report_type, publisher, codes, sectors_major, sectors_minor, products, reason
        (11, '2026-04-27', '단일종목', 'KB', ['005930'], ['반도체'], ['메모리반도체'], ['DRAM'], None),
        (12, '2026-04-29', '단일종목', 'NH', ['005930'], ['반도체'], ['메모리반도체'], ['DRAM'], None),
        (13, '2026-05-04', '섹터', 'NH', ['005930', '000660'], ['반도체'], ['메모리반도체'], ['DRAM', 'NAND'], None),
        (14, '2026-05-06', '산업', '키움', [], [], [], [], None),
        (15, '2026-05-13', '단일종목', '키움', ['000660'], ['반도체'], ['메모리반도체'], ['NAND'], None),
        (16, '2026-05-14', '단일종목', 'KB', ['999999'], ['조선'], ['상선'], ['LNG선'], None),
        (17, None, 'IR자료', '해당기업', ['016360'], [], [], [], 'ir_self'),
    ]
    for rid, published, report_type, publisher, codes, major, minor, products, reason in specs:
        row = dict.fromkeys(REPORT_COLUMNS)
        row.update(id=rid, published_at=published, sent_at='2026-05-15T03:00:00Z',
                   report_type=report_type, publisher=publisher, stock_codes=codes,
                   company_names=[], sectors_major=major, sectors_minor=minor, products=products,
                   tagging_status='auto', out_of_scope_reason=reason)
        rows.append(row)
    return pd.DataFrame(rows, columns=list(REPORT_COLUMNS))


def report(rid, codes=('005930',), *, publisher_type='broker', report_type='단일종목', publisher='KB',
           published='2026-10-01', sent='2026-10-01T01:00:00Z', **extra) -> dict:
    """An in-scope reports row with the 16 columns: a broker's 단일종목 report of 005930 unless told
    otherwise. ``extra`` sets any other column."""
    row = dict.fromkeys(REPORT_COLUMNS)
    row.update(id=rid, stock_codes=list(codes), publisher_type=publisher_type, report_type=report_type,
               publisher=publisher, published_at=published, sent_at=sent, tagging_status='auto')
    row.update(extra)
    return row


def frame_of(*rows: dict) -> pd.DataFrame:
    """The rows as the reports window gives them: a DataFrame of the 16 columns, even when empty."""
    return pd.DataFrame(list(rows), columns=list(REPORT_COLUMNS))
