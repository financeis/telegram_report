"""Reports (기업 리포트): reads of tagged reports, the report list, PDF and analyze routes.

This feature owns the reads of finished classifications in the reports table (spec §4); other
features read reports only through the names below. Public names:

- ``router``: ``GET /api/stocks/{code}/reports``, ``GET /api/reports/{rid}/pdf``,
  ``POST /api/reports/{rid}/analyze`` (spec §6).
- ``report_row(rid)`` → the in-scope DB row (16 columns, file_path included: never send it to
  the browser); 404 ``기업 보고서를 찾을 수 없습니다.`` when there is none.
- ``get_report(rid)`` → ``public_report`` of that row with its saved summary.
- ``public_report(row, summary)`` → the browser shape: id, title, file_name, published_at,
  publisher, report_type, stock_codes, company_names, sectors_major, sectors_minor, products,
  summary, pdf_url (``/api/reports/{id}/pdf``).
- ``period_rows(since, include_oos)`` → DataFrame of the period's rows (with out-of-scope rows
  kept by their effective date when ``include_oos``).
- ``stock_rows(code, since)`` → DataFrame of the in-scope rows holding ``code``.
- ``rows_for_stocks(codes, since)`` → DataFrame of the in-scope rows holding at least one of
  ``codes`` (each as given, no zero-padding), kept when their effective date (published_at, else
  the KST date of sent_at) is on or after ``since``. Codes that are not plain letters and digits
  match nothing; one plain string instead of a collection is a TypeError.
- ``latest_report_sent_at()`` → the latest ``sent_at`` among the in-scope rows, as an aware
  datetime (UTC), or None when there is none.
- ``tagging_in_progress(minutes=30)`` → True when some row is ``processing`` with
  ``tagging_locked_at`` in the last ``minutes`` minutes: a count, no row is read.

Every DataFrame (and ``report_row``) has the 16 columns: id, published_at, sent_at, report_type,
publisher, stock_codes, company_names, sectors_major, sectors_minor, products, tagging_status,
out_of_scope_reason, file_path, file_name, title, publisher_type — even when it has no rows.

Without DB settings every read raises ``NotReady("리포트", …)``; an unreadable stock list stops
only the report list (spec §9.9).
"""
from .router import router
from .service import (
    get_report,
    latest_report_sent_at,
    period_rows,
    public_report,
    report_row,
    rows_for_stocks,
    stock_rows,
    tagging_in_progress,
)
