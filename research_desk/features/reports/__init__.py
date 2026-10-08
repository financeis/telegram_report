"""Reports (기업 리포트): reads of tagged reports, the report list, PDF and analyze routes.

This feature owns the reads of finished classifications in the reports table (spec §4); other
features read reports only through the names below. Public names:

- ``router``: ``GET /api/stocks/{code}/reports``, ``GET /api/reports/{rid}/pdf``,
  ``POST /api/reports/{rid}/analyze`` (spec §6).
- ``report_row(rid)`` → the in-scope DB row (15 columns, file_path included: never send it to
  the browser); 404 ``기업 보고서를 찾을 수 없습니다.`` when there is none.
- ``get_report(rid)`` → ``public_report`` of that row with its saved summary.
- ``public_report(row, summary)`` → the browser shape: id, title, file_name, published_at,
  publisher, report_type, stock_codes, company_names, sectors_major, sectors_minor, products,
  summary, pdf_url (``/api/reports/{id}/pdf``).
- ``period_rows(since, include_oos)`` → DataFrame of the period's rows (with out-of-scope rows
  kept by their effective date when ``include_oos``).
- ``stock_rows(code, since)`` → DataFrame of the in-scope rows holding ``code``.

Without DB settings every read raises ``NotReady("리포트", …)``; an unreadable stock list stops
only the report list (spec §9.9).
"""
from .router import router
from .service import get_report, period_rows, public_report, report_row, stock_rows
