"""Parity regression: run the row graph headlessly per fixture and assert
the DB UPDATE payload matches the expected snapshot.

v2 변경 (rev-7):
- UPDATE_SQL은 19-arg payload (Task 12)
- LLMExtraction v2 schema (Task 5)
- 6 report_types × 5 OOS reasons + mismatch 케이스

This is the primary check that environment-change-only is honoured.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import fitz  # PyMuPDF
import pytest

from research_desk.tagger.graph import build_graph
from research_desk.tagger.llm_schemas import LLMExtraction, OOSSignals
from research_desk.tagger.sql import UPDATE_SQL


FIXTURES = Path(__file__).parent / "parity" / "fixtures.json"


# v2 UPDATE_SQL bind args ($1..$19) → column names for readable assertions.
# Order matches supabase_io.UPDATE_SQL.
COLUMNS = [
    "id",                    # $1
    "published_at",          # $2
    "report_type",           # $3
    "publisher",             # $4
    "publisher_type",        # $5
    "analysts",              # $6
    "title",                 # $7
    "stock_codes",           # $8
    "company_names",         # $9
    "stock_codes_raw",       # $10
    "company_names_raw",     # $11
    "sectors_major",         # $12
    "sectors_minor",         # $13
    "products",              # $14
    "out_of_scope_reason",   # $15
    "tagging_status",        # $16
    "tagging_confidence",    # $17
    "tagging_notes",         # $18
    "taxonomy_version",      # $19
]


def _load_fixtures() -> list[dict]:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


def _make_llm_mock(payload: dict) -> LLMExtraction:
    payload = dict(payload)
    payload["oos_signals"] = OOSSignals(**payload["oos_signals"])
    return LLMExtraction(**payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("fix", _load_fixtures(), ids=lambda f: f["case"])
async def test_parity(fix, krx, mock_llm_client, mock_supabase, monkeypatch, tmp_path):
    # Stub PDF on disk — extract_pdf reads STORAGE_BASE_DIR/<file_path>.
    # Use the built-in 'korea' CJK font so Hangul roundtrips through PyMuPDF.
    pdf = tmp_path / fix["input"]["file_name"]
    doc = fitz.open()
    doc.new_page().insert_text(
        (72, 72), fix["input"]["pdf_text"], fontname="korea", fontsize=11
    )
    doc.save(pdf)
    doc.close()
    monkeypatch.setenv("STORAGE_BASE_DIR", str(tmp_path))

    mock_llm_client.set_response(_make_llm_mock(fix["llm_mock"]))

    app = build_graph(
        mock_llm_client, mock_supabase, krx=krx,
        dry_run=False, taxonomy_version="KRX@parity-test",
    )
    init = {
        "id": 1,
        "file_path": fix["input"]["file_name"],
        "file_name": fix["input"]["file_name"],
        "sent_at": datetime.fromisoformat(fix["input"]["sent_at"]),
        "caption": fix["input"]["caption"],
        "chat_username": "x",
        "worker_id": "parity",
        "model": "gpt-5.4-mini",
    }
    await app.ainvoke(init)

    # Inspect the recorded UPDATE payload — exactly one UPDATE per row.
    assert len(mock_supabase.executed) == 1
    sql, args = mock_supabase.executed[0]
    assert sql == UPDATE_SQL
    assert len(args) == 19, f"v2 UPDATE_SQL expects 19 args, got {len(args)}"

    actual = dict(zip(COLUMNS, args))
    expected = fix["expected"]

    # List-valued columns: cast to list for stable comparison (asyncpg may
    # return tuples and Python list literals in expected JSON come back as lists).
    list_cols = {
        "analysts", "stock_codes", "company_names",
        "stock_codes_raw", "company_names_raw",
        "sectors_major", "sectors_minor", "products",
    }

    for k, v in expected.items():
        if k in list_cols:
            assert list(actual[k]) == v, (
                f"{k}: expected {v!r}, got {actual[k]!r}"
            )
        else:
            assert actual[k] == v, (
                f"{k}: expected {v!r}, got {actual[k]!r}"
            )
