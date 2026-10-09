"""Business-report texts (사업보고서) from the DART collection's MongoDB, read only (spec §4).

One document is one section of one annual report (``FS.A001_v2`` by default). Fields are
guaranteed from ``parser_version`` 0.2.0 on, so documents of older parsers are never read.

- Fiscal-year rule: for fiscal year N each company (``stock_code``) uses one report: final
  (``is_final``), ``fiscal_year = N``, the latest ``fiscal_end`` (then the latest receipt). A
  March year-end report of 2025-03-31 belongs to N = 2025 like a December one.
- Targets: ``corp_cls`` Y (KOSPI) or K (KOSDAQ), in the app's stock list, and neither the DART
  name nor the stock-list name holds 스팩, 기업인수목적, 리츠 or 부동산투자회사. KONEX is out.
- Texts are read per report, only when a company's profile is (re)built.

``DartSource`` wraps a pymongo Collection (``core.mongo.mongo_collection``); this module never
imports pymongo.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

from research_desk.domain.stocks import StockList

from .logic import INPUT_SECTIONS, Section

MIN_PARSER_VERSION = (0, 2, 0)
TARGET_CORP_CLASSES = ("Y", "K")
# Any of these in a company name keeps it out (SPACs, REITs). Literal words, as the spec says.
EXCLUDED_NAME_WORDS = ("스팩", "기업인수목적", "리츠", "부동산투자회사")

_CODE = re.compile(r"^[0-9A-Z]{6}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_VERSION = re.compile(r"^\s*v?(\d+(?:\.\d+)*)")

META_FIELDS = ("stock_code", "corp_code", "corp_name", "corp_cls", "rcept_no", "rcept_dt",
               "report_name", "fiscal_end", "fiscal_year", "is_final", "parser_version", "section_code")


def parse_version(value: Any) -> Optional[tuple[int, ...]]:
    """``"0.2.0"`` → ``(0, 2, 0)``; the leading numbers of e.g. ``"v0.3.0"`` or ``"0.2.0rc1"``;
    None when there are none."""
    if not isinstance(value, str):
        return None
    match = _VERSION.match(value)
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def _padded(version: tuple[int, ...]) -> tuple[int, ...]:
    return version + (0,) * max(0, 3 - len(version))


def is_supported_parser(value: Any) -> bool:
    """A parser version of 0.2.0 or later (the fields this module reads are guaranteed)."""
    version = parse_version(value)
    return version is not None and _padded(version) >= MIN_PARSER_VERSION


def _version_order(value: str) -> tuple[int, ...]:
    return _padded(parse_version(value) or ())


@dataclass(frozen=True)
class CompanyReport:
    """The one report a company uses for the fiscal year, without its texts."""
    stock_code: str
    corp_code: Optional[str]
    corp_name: str
    corp_cls: str
    rcept_no: str
    rcept_dt: Optional[str]
    report_name: Optional[str]
    fiscal_end: Optional[str]           # YYYY-MM-DD, None when the document's value is not a date
    fiscal_year: int
    parser_version: str                 # the newest parser version among the report's sections
    section_codes: tuple[str, ...]


def _excluded_name(*names: Optional[str]) -> bool:
    return any(word in (name or "") for name in names for word in EXCLUDED_NAME_WORDS)


def select_reports(docs: Iterable[dict], fiscal_year: int, stocks: StockList) -> list[CompanyReport]:
    """The target companies' reports for ``fiscal_year`` from section documents' metadata
    (the fiscal-year rule, then the target rules), in stock-code order."""
    reports: dict[str, dict[str, dict]] = {}
    for doc in docs:
        code = doc.get("stock_code")
        if not isinstance(code, str) or not _CODE.match(code):
            continue
        if doc.get("is_final") is not True or doc.get("fiscal_year") != fiscal_year:
            continue
        rcept_no = doc.get("rcept_no")
        if not rcept_no:
            continue
        report = reports.setdefault(code, {}).setdefault(rcept_no, {"doc": doc, "sections": [], "versions": []})
        if doc.get("section_code") and doc["section_code"] not in report["sections"]:
            report["sections"].append(doc["section_code"])
        report["versions"].append(doc.get("parser_version") or "")

    chosen: list[CompanyReport] = []
    for code in sorted(reports):
        latest = max(reports[code].values(),
                     key=lambda r: (str(r["doc"].get("fiscal_end") or ""), str(r["doc"].get("rcept_dt") or ""),
                                    str(r["doc"].get("rcept_no"))))
        doc = latest["doc"]
        entry = stocks.lookup(code)
        if doc.get("corp_cls") not in TARGET_CORP_CLASSES or entry is None:
            continue
        if _excluded_name(doc.get("corp_name"), entry.name):
            continue
        fiscal_end = doc.get("fiscal_end")
        chosen.append(CompanyReport(
            stock_code=code,
            corp_code=doc.get("corp_code"),
            corp_name=doc.get("corp_name") or entry.name,
            corp_cls=doc["corp_cls"],
            rcept_no=doc["rcept_no"],
            rcept_dt=doc.get("rcept_dt"),
            report_name=doc.get("report_name"),
            fiscal_end=fiscal_end if isinstance(fiscal_end, str) and _DATE.match(fiscal_end) else None,
            fiscal_year=fiscal_year,
            parser_version=max(latest["versions"], key=_version_order),
            section_codes=tuple(sorted(latest["sections"])),
        ))
    return chosen


class DartSource:
    """Reads of the business-report collection. ``close()`` ends its client."""

    def __init__(self, collection: Any) -> None:
        self._collection = collection

    def parser_versions(self, fiscal_year: int) -> list[str]:
        """The parser versions (0.2.0 or later) among the year's documents, oldest first."""
        values = self._collection.distinct("parser_version", {"fiscal_year": fiscal_year})
        return sorted((v for v in values if is_supported_parser(v)), key=_version_order)

    def reports(self, fiscal_year: int, stocks: StockList,
                parser_versions: Sequence[str]) -> list[CompanyReport]:
        """The target companies' reports for the year (metadata only, no text)."""
        docs = self._collection.find(
            {"fiscal_year": fiscal_year, "is_final": True, "parser_version": {"$in": list(parser_versions)}},
            {**{name: 1 for name in META_FIELDS}, "_id": 0},
        )
        return select_reports(docs, fiscal_year, stocks)

    def sections(self, report: CompanyReport) -> dict[str, Section]:
        """The input sections of one report: ``{section_code: Section(prose, tables)}``. When a
        section has several documents, the newest parser's wins."""
        docs = self._collection.find(
            {"rcept_no": report.rcept_no, "stock_code": report.stock_code,
             "section_code": {"$in": list(INPUT_SECTIONS)}},
            {"section_code": 1, "prose_text": 1, "table_text": 1, "parser_version": 1, "_id": 0},
        )
        best: dict[str, dict] = {}
        for doc in docs:
            code = doc.get("section_code")
            current = best.get(code)
            if current is None or _version_order(doc.get("parser_version") or "") > _version_order(
                    current.get("parser_version") or ""):
                best[code] = doc
        return {code: Section(prose=doc.get("prose_text") or "", tables=doc.get("table_text") or "")
                for code, doc in sorted(best.items())}

    def close(self) -> None:
        self._collection.database.client.close()
