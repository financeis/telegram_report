"""Report rules shared by the tagger, manual review and the web features.

The single place that defines (spec §4, §9.2):

- the value sets in ``vocabulary.yaml``: report types, out-of-scope (OOS) reasons,
  publisher types, tagging statuses, tagging confidences;
- the in-scope rule ("분석 대상"): ``tagging_status`` in ``IN_SCOPE_STATUSES`` and
  ``out_of_scope_reason`` is null;
- the out-of-scope row shape ("분석 대상 외 행 모양") that both the tagger's write step
  and the review's OOS action produce.

Nothing here touches the DB: callers pass rows in and write the returned values themselves.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

VOCABULARY_PATH = Path(__file__).with_name("vocabulary.yaml")


def load_vocabulary(path: Path = VOCABULARY_PATH) -> dict[str, Any]:
    """Parse a vocabulary yaml file (default: the bundled ``vocabulary.yaml``)."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


_VOCABULARY = load_vocabulary()

REPORT_TYPES: tuple[str, ...] = tuple(_VOCABULARY["report_types"])
OOS_REASONS: tuple[str, ...] = tuple(_VOCABULARY["oos_reasons"])
PUBLISHER_TYPES: tuple[str, ...] = tuple(_VOCABULARY["publisher_types"])
TAGGING_STATUSES: tuple[str, ...] = tuple(_VOCABULARY["tagging_statuses"])
TAGGING_CONFIDENCES: tuple[str, ...] = tuple(_VOCABULARY["tagging_confidences"])

# Tagging statuses whose rows count as final classifications.
IN_SCOPE_STATUSES: tuple[str, ...] = ("auto", "verified")


def is_in_scope(row: Mapping[str, Any]) -> bool:
    """True for an in-scope report row: final status and no out-of-scope reason.

    A missing ``out_of_scope_reason`` key counts as null (the SQL ``IS NULL``).
    """
    return (row.get("tagging_status") in IN_SCOPE_STATUSES
            and row.get("out_of_scope_reason") is None)


# Out-of-scope rows empty these: published_at becomes null, the arrays become [].
OOS_CLEARED_COLUMNS: tuple[str, ...] = (
    "published_at", "stock_codes", "company_names", "sectors_major", "sectors_minor", "products",
)
# Out-of-scope rows keep these from the classification (LLM output or the current row).
OOS_KEPT_COLUMNS: tuple[str, ...] = (
    "report_type", "publisher", "publisher_type", "analysts", "title",
    "stock_codes_raw", "company_names_raw",
)


def oos_row_shape(row: Optional[Mapping[str, Any]], reason: str) -> dict[str, Any]:
    """Column values an out-of-scope row must have.

    ``row`` is a reports row, or the LLM's fields under the DB column names
    (``publisher``, not ``publisher_canon``); None means no classification at all.
    Returns the reason, the cleared columns and the kept columns (array columns copied,
    null arrays as []). The tagging status is not part of the shape: each caller sets it.
    Raises ValueError when ``reason`` is not one of ``OOS_REASONS``.
    """
    if reason not in OOS_REASONS:
        raise ValueError(f"reason must be one of {OOS_REASONS}, got: {reason!r}")
    source: Mapping[str, Any] = row or {}
    return {
        "out_of_scope_reason": reason,
        # cleared
        "published_at": None,
        "stock_codes": [],
        "company_names": [],
        "sectors_major": [],
        "sectors_minor": [],
        "products": [],
        # kept: classification + raw audit
        "report_type": source.get("report_type"),
        "publisher": source.get("publisher"),
        "publisher_type": source.get("publisher_type"),
        "analysts": list(source.get("analysts") or []),
        "title": source.get("title"),
        "stock_codes_raw": list(source.get("stock_codes_raw") or []),
        "company_names_raw": list(source.get("company_names_raw") or []),
    }
