"""Orchestrator batch flow tests with mock LLM client + mock supabase."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from research_desk.tagger.orchestrator import run_batch
from research_desk.tagger.tests.conftest import make_llm_extraction


def _row(id_=1, file_path="x.pdf", file_name="삼성전자.pdf"):
    return {
        "id": id_,
        "file_path": file_path,
        "file_name": file_name,
        "sent_at": datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc),
        "caption": None,
        "chat_username": "x",
    }


@pytest.fixture
def make_pdf(tmp_path, monkeypatch):
    """Create a tiny PDF and point STORAGE_BASE_DIR at tmp_path."""
    import fitz
    pdf = tmp_path / "x.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "키움증권 분석가 홍길동 투자의견 매수", fontsize=12)
    doc.save(pdf)
    doc.close()
    monkeypatch.setenv("STORAGE_BASE_DIR", str(tmp_path))
    return pdf


@pytest.mark.asyncio
async def test_normal_batch_processes_all_rows(krx, mock_llm_client, mock_supabase, make_pdf):
    # stale_reclaim is execute(), not fetch — only one queue_fetch needed (atomic claim).
    mock_supabase.queue_fetch([_row(1), _row(2)])  # atomic claim returns 2 rows

    mock_llm_client.set_response(make_llm_extraction(
        stock_codes_raw=["005930"], publisher_raw="키움",
    ))

    report = await run_batch(
        sb=mock_supabase, client=mock_llm_client, krx=krx,
        taxonomy_version="KRX@2026-05-08",
        batch_size=10, dry_run=False, row_ids=[],
        model="gpt-5.4-mini", max_concurrent_llm=4, worker_id="w1",
    )
    assert report["processed"] == 2
    assert report["auto"] >= 1


@pytest.mark.asyncio
async def test_dry_run_does_not_call_update(krx, mock_llm_client, mock_supabase, make_pdf):
    mock_supabase.queue_fetch([_row(1)])
    mock_llm_client.set_response(make_llm_extraction())

    report = await run_batch(
        sb=mock_supabase, client=mock_llm_client, krx=krx,
        taxonomy_version="KRX@2026-05-08",
        batch_size=10, dry_run=True, row_ids=[],
        model="gpt-5.4-mini", max_concurrent_llm=4, worker_id="w1",
    )
    # Only the SELECT happened — no UPDATE
    assert all("UPDATE reports" not in sql for sql, _ in mock_supabase.executed)


@pytest.mark.asyncio
async def test_row_ids_path_skips_atomic_claim(krx, mock_llm_client, mock_supabase, make_pdf):
    mock_supabase.queue_fetch([_row(42)])  # ROW_IDS_FETCH_SQL response
    mock_llm_client.set_response(make_llm_extraction())

    report = await run_batch(
        sb=mock_supabase, client=mock_llm_client, krx=krx,
        taxonomy_version="KRX@2026-05-08",
        batch_size=10, dry_run=False, row_ids=[42],
        model="gpt-5.4", max_concurrent_llm=4, worker_id="w1",
    )
    # No stale reclaim, no atomic claim — only ROW_IDS_FETCH (in fetched) + write (in executed)
    assert report["processed"] == 1
    fetched_sqls = [s for s, _ in mock_supabase.fetched]
    assert any("ANY($1::bigint[])" in s for s in fetched_sqls)
    assert all("FOR UPDATE SKIP LOCKED" not in s for s, _ in mock_supabase.executed)


@pytest.mark.asyncio
async def test_transient_llm_error_reverts_row_to_pending(krx, mock_llm_client, mock_supabase, make_pdf):
    from openai import RateLimitError
    import httpx

    # stale_reclaim is execute(), not fetch.
    mock_supabase.queue_fetch([_row(99)])  # atomic claim
    # openai 2.x requires the response to have its request set.
    _resp = httpx.Response(429, request=httpx.Request("POST", "http://x"))
    mock_llm_client.set_exception(RateLimitError("429", response=_resp, body=None))

    report = await run_batch(
        sb=mock_supabase, client=mock_llm_client, krx=krx,
        taxonomy_version="KRX@2026-05-08",
        batch_size=10, dry_run=False, row_ids=[],
        model="gpt-5.4-mini", max_concurrent_llm=4, worker_id="w1",
    )
    # The orchestrator should have called REVERT for row 99
    revert_calls = [args for sql, args in mock_supabase.executed
                    if "tagging_status='pending'" in sql and "id=$1" in sql]
    assert any(args == (99,) for args in revert_calls)


@pytest.mark.asyncio
async def test_empty_claim_returns_zero_processed(krx, mock_llm_client, mock_supabase):
    # stale_reclaim is execute(), not fetch.
    mock_supabase.queue_fetch([])  # atomic claim returns nothing

    report = await run_batch(
        sb=mock_supabase, client=mock_llm_client, krx=krx,
        taxonomy_version="KRX@2026-05-08",
        batch_size=10, dry_run=False, row_ids=[],
        model="gpt-5.4-mini", max_concurrent_llm=4, worker_id="w1",
    )
    assert report["processed"] == 0
    # No graph invocations
    mock_llm_client.parse.assert_not_called()


@pytest.mark.asyncio
async def test_per_row_deadline_reverts_to_pending(krx, mock_llm_client, mock_supabase, make_pdf):
    """Long-running rows should hit asyncio.wait_for and REVERT."""
    import asyncio
    mock_supabase.queue_fetch([_row(7)])

    async def _slow(*a, **kw):
        await asyncio.sleep(1.0)
        return mock_llm_client.parse.return_value
    mock_llm_client.parse = _slow

    report = await run_batch(
        sb=mock_supabase, client=mock_llm_client, krx=krx,
        taxonomy_version="KRX@2026-05-08",
        batch_size=10, dry_run=False, row_ids=[],
        model="gpt-5.4-mini", max_concurrent_llm=4, worker_id="w1",
        # Pass an aggressive deadline as an explicit arg — no env / module-level state.
        lock_ttl_minutes=30, per_row_deadline_s=0.01,
    )
    assert any("tagging_status='pending'" in sql and args == (7,)
               for sql, args in mock_supabase.executed)
    assert report.get("deadline_errors", 0) >= 1


@pytest.mark.asyncio
async def test_unhandled_exception_does_not_burst_gather(krx, mock_llm_client, mock_supabase, make_pdf):
    """A node raising an unexpected exception must NOT crash gather()."""
    mock_supabase.queue_fetch([_row(11), _row(12)])

    # First call raises, second succeeds
    call_state = {"n": 0}
    async def _flaky(*a, **kw):
        call_state["n"] += 1
        if call_state["n"] == 1:
            raise RuntimeError("simulated unknown failure")
        return mock_llm_client.parse.return_value
    mock_llm_client.parse = _flaky
    # Set a default valid response for the non-raising path
    mock_llm_client.set_response(make_llm_extraction())

    report = await run_batch(
        sb=mock_supabase, client=mock_llm_client, krx=krx,
        taxonomy_version="KRX@2026-05-08",
        batch_size=10, dry_run=False, row_ids=[],
        model="gpt-5.4-mini", max_concurrent_llm=4, worker_id="w1",
    )
    # Both rows accounted for; first reverted as 'unhandled', second processed.
    assert report["processed"] == 2
    revert_calls = [args for sql, args in mock_supabase.executed
                    if "tagging_status='pending'" in sql and "id=$1" in sql]
    assert (11,) in revert_calls


def test_empty_report_v2_oos_includes_ir_self():
    """v2: _empty_report's oos dict has 5 keys including ir_self."""
    from research_desk.tagger.orchestrator import _empty_report
    rep = _empty_report("m")
    assert set(rep["oos"].keys()) == {"foreign", "fund", "digital", "private", "ir_self"}
    assert all(v == 0 for v in rep["oos"].values())
    assert rep["review_reasons"] == {}


def test_aggregate_oos_includes_ir_self():
    """v2: oos_reason='ir_self' is counted alongside foreign/fund/digital/private."""
    from research_desk.tagger.orchestrator import _aggregate
    results = [
        {"id": 1, "is_oos": True, "oos_reason": "ir_self", "tagging_status": "auto",
         "tagging_confidence": "high"},
        {"id": 2, "is_oos": True, "oos_reason": "foreign", "tagging_status": "auto",
         "tagging_confidence": "high"},
    ]
    rep = _aggregate(results, model="m", batch_size=2, dry_run=False)
    assert rep["oos"]["ir_self"] == 1
    assert rep["oos"]["foreign"] == 1
    assert rep["oos"]["fund"] == 0
    assert rep["oos"]["digital"] == 0
    assert rep["oos"]["private"] == 0


def test_aggregate_review_reasons_v2_keys():
    """v2: review_reasons set tracks first_page_unreadable, llm_refusal, type_indeterminate,
    krx_unmatched_in_scope. unknown_* tags are no longer recognized."""
    from research_desk.tagger.orchestrator import _aggregate
    results = [
        {"id": 1, "tagging_status": "review_needed",
         "tagging_notes": "krx_unmatched_in_scope:ipo_pending_or_unknown"},
        {"id": 2, "tagging_status": "review_needed",
         "tagging_notes": "type_indeterminate"},
    ]
    rep = _aggregate(results, model="m", batch_size=2, dry_run=False)
    assert rep["review_reasons"] == {
        "krx_unmatched_in_scope": 1, "type_indeterminate": 1,
    }


def test_aggregate_review_reasons_ignores_v1_unknown_tags():
    """v2 review_reasons set MUST NOT count v1 unknown_* tags (they're gone)."""
    from research_desk.tagger.orchestrator import _aggregate
    results = [
        {"id": 1, "tagging_status": "review_needed",
         "tagging_notes": "unknown_stock_code"},
        {"id": 2, "tagging_status": "review_needed",
         "tagging_notes": "unknown_sector;unknown_product"},
        {"id": 3, "tagging_status": "review_needed",
         "tagging_notes": "unknown_publisher"},
    ]
    rep = _aggregate(results, model="m", batch_size=3, dry_run=False)
    # All v1 unknown_* tags are filtered out — review_reasons stays empty.
    assert rep["review_reasons"] == {}


def test_aggregate_review_reasons_handles_llm_refusal_with_detail():
    """v2: llm_refusal:<error> note format — split(":", 1)[0] extracts the prefix."""
    from research_desk.tagger.orchestrator import _aggregate
    results = [
        {"id": 1, "tagging_status": "review_needed",
         "tagging_notes": "llm_refusal:rate_limit_exceeded"},
        {"id": 2, "tagging_status": "review_needed",
         "tagging_notes": "first_page_unreadable;llm_refusal:timeout"},
    ]
    rep = _aggregate(results, model="m", batch_size=2, dry_run=False)
    assert rep["review_reasons"]["llm_refusal"] == 2
    assert rep["review_reasons"]["first_page_unreadable"] == 1


# ── Picture / suspect counts and dry-run rows (docs/contracts.md, tag run report) ────────────────

_REPORT_KEYS = [
    "model", "processed", "auto", "review_needed", "confidence", "oos", "review_reasons",
    "transient_errors", "deadline_errors", "unhandled_errors",
    "page_image", "page_image_unsupported", "publisher_suspect",
    "dry_run", "batch_size",
]


def _final(id_, **values):
    """A row's final graph state as run_batch collects it."""
    return {"id": id_, **values}


_MIXED_RESULTS = [
    # in-scope auto, read from the picture, suspect publisher
    _final(1, tagging_status="auto", tagging_confidence="medium",
           tagging_notes="page_image;publisher_suspect:unknown",
           llm_raw=make_llm_extraction(publisher_canon=None), page_image=True,
           publisher_final=None, publisher_type_final=None, publisher_suspect="unknown"),
    # in-scope review, suspect only
    _final(2, tagging_status="review_needed", tagging_confidence="low",
           tagging_notes="krx_unmatched_in_scope:ipo_pending_or_unknown;publisher_suspect:filename_mismatch",
           llm_raw=make_llm_extraction(stock_codes_raw=[], company_names_raw=["미상장IPO후보"]),
           publisher_final="키움증권", publisher_type_final="broker",
           publisher_suspect="filename_mismatch"),
    # picture the model could not take
    _final(3, tagging_status="review_needed", tagging_confidence="low",
           tagging_notes="first_page_unreadable", llm_raw=None, pdf_unreadable=True,
           page_image_unsupported=True),
    # OOS, read from the picture
    _final(4, tagging_status="auto", tagging_confidence="medium", tagging_notes="page_image",
           llm_raw=make_llm_extraction(report_type="IR자료", publisher_canon="해당기업"),
           is_oos=True, oos_reason="ir_self", page_image=True,
           publisher_final="해당기업", publisher_type_final="other", publisher_suspect=None),
    # plain text row, unmarked
    _final(5, tagging_status="auto", tagging_confidence="high", tagging_notes=None,
           llm_raw=make_llm_extraction(), publisher_final="키움증권",
           publisher_type_final="broker", publisher_suspect=None),
    # refused picture row
    _final(6, tagging_status="review_needed", tagging_confidence="low",
           tagging_notes="llm_refusal:cannot read;page_image", llm_raw=None,
           llm_refusal="cannot read", page_image=True),
    # error rows
    {"id": 7, "error": "transient", "detail": "429"},
    {"id": 8, "error": "deadline_exceeded"},
    {"id": 9, "error": "unhandled", "detail": "RuntimeError:boom"},
]


def test_report_counts_picture_and_suspect_rows():
    from research_desk.tagger.orchestrator import _aggregate

    rep = _aggregate(_MIXED_RESULTS, model="m", batch_size=10, dry_run=False)

    assert rep["page_image"] == 3                 # rows 1, 4, 6
    assert rep["page_image_unsupported"] == 1     # row 3
    assert rep["publisher_suspect"] == 2          # rows 1, 2
    assert all(isinstance(rep[k], int) for k in ("page_image", "page_image_unsupported",
                                                  "publisher_suspect"))


def test_report_keeps_the_existing_keys_and_adds_three():
    from research_desk.tagger.orchestrator import _aggregate

    rep = _aggregate(_MIXED_RESULTS, model="m", batch_size=10, dry_run=False)

    assert list(rep) == _REPORT_KEYS
    assert rep["processed"] == 9
    assert (rep["auto"], rep["review_needed"]) == (3, 3)
    assert rep["confidence"] == {"high": 1, "medium": 2, "low": 3}
    assert rep["oos"]["ir_self"] == 1
    assert (rep["transient_errors"], rep["deadline_errors"], rep["unhandled_errors"]) == (1, 1, 1)


def test_review_reasons_count_only_the_four_names():
    """page_image and publisher_suspect notes are not review reasons (docs/business-rules.md)."""
    from research_desk.tagger.orchestrator import _aggregate

    rep = _aggregate(_MIXED_RESULTS, model="m", batch_size=10, dry_run=False)

    assert rep["review_reasons"] == {
        "krx_unmatched_in_scope": 1, "first_page_unreadable": 1, "llm_refusal": 1,
    }


def test_a_report_without_marked_rows_counts_zero():
    from research_desk.tagger.orchestrator import _aggregate

    rep = _aggregate([_MIXED_RESULTS[4]], model="m", batch_size=1, dry_run=False)

    assert (rep["page_image"], rep["page_image_unsupported"], rep["publisher_suspect"]) == (0, 0, 0)


def test_dry_run_report_lists_each_row():
    from research_desk.tagger.orchestrator import _aggregate

    rep = _aggregate(_MIXED_RESULTS, model="m", batch_size=10, dry_run=True)

    assert list(rep) == [*_REPORT_KEYS, "rows"]
    assert rep["rows"] == [
        {"id": 1, "tagging_status": "auto", "tagging_confidence": "medium",
         "report_type": "단일종목", "publisher": None, "publisher_type": None,
         "tagging_notes": "page_image;publisher_suspect:unknown"},
        {"id": 2, "tagging_status": "review_needed", "tagging_confidence": "low",
         "report_type": "단일종목", "publisher": "키움증권", "publisher_type": "broker",
         "tagging_notes": "krx_unmatched_in_scope:ipo_pending_or_unknown;"
                          "publisher_suspect:filename_mismatch"},
        {"id": 3, "tagging_status": "review_needed", "tagging_confidence": "low",
         "report_type": None, "publisher": None, "publisher_type": None,
         "tagging_notes": "first_page_unreadable"},
        {"id": 4, "tagging_status": "auto", "tagging_confidence": "medium",
         "report_type": "IR자료", "publisher": "해당기업", "publisher_type": "other",
         "tagging_notes": "page_image"},
        {"id": 5, "tagging_status": "auto", "tagging_confidence": "high",
         "report_type": "단일종목", "publisher": "키움증권", "publisher_type": "broker",
         "tagging_notes": None},
        {"id": 6, "tagging_status": "review_needed", "tagging_confidence": "low",
         "report_type": None, "publisher": None, "publisher_type": None,
         "tagging_notes": "llm_refusal:cannot read;page_image"},
        {"id": 7, "error": "transient", "detail": "429"},
        {"id": 8, "error": "deadline_exceeded", "detail": None},
        {"id": 9, "error": "unhandled", "detail": "RuntimeError:boom"},
    ]


def test_dry_run_rows_are_json_ready():
    import json

    from research_desk.tagger.orchestrator import _aggregate

    rep = _aggregate(_MIXED_RESULTS, model="m", batch_size=10, dry_run=True)

    assert json.loads(json.dumps(rep, ensure_ascii=False))["rows"] == rep["rows"]


def test_a_report_that_is_not_a_dry_run_has_no_rows():
    from research_desk.tagger.orchestrator import _aggregate

    rep = _aggregate(_MIXED_RESULTS, model="m", batch_size=10, dry_run=False)

    assert "rows" not in rep


def test_the_empty_report_is_unchanged():
    """No row taken: the empty report keeps its shape (no new keys, no rows)."""
    from research_desk.tagger.orchestrator import _empty_report

    assert _empty_report("m") == {
        "model": "m", "processed": 0,
        "auto": 0, "review_needed": 0,
        "confidence": {"high": 0, "medium": 0, "low": 0},
        "oos": {"foreign": 0, "fund": 0, "digital": 0, "private": 0, "ir_self": 0},
        "review_reasons": {},
        "transient_errors": 0,
        "deadline_errors": 0,
        "unhandled_errors": 0,
    }


@pytest.mark.asyncio
async def test_an_empty_dry_run_returns_the_empty_report(krx, mock_llm_client, mock_supabase):
    from research_desk.tagger.orchestrator import _empty_report

    mock_supabase.queue_fetch([])

    report = await run_batch(
        sb=mock_supabase, client=mock_llm_client, krx=krx,
        taxonomy_version="KRX@2026-05-08",
        batch_size=10, dry_run=True, row_ids=[],
        model="gpt-5.4-mini", max_concurrent_llm=2, worker_id="w1",
    )
    assert report == _empty_report("gpt-5.4-mini")


def _picture_pdf(path):
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 20), False)
    pix.clear_with(180)
    page.insert_image(fitz.Rect(72, 72, 272, 172), pixmap=pix)
    doc.save(path)
    doc.close()


@pytest.mark.asyncio
async def test_dry_run_batch_reports_rows_and_marks(krx, mock_llm_client, mock_supabase,
                                                    make_pdf, tmp_path):
    """A text row with a suspect publisher, a picture row, and a failed row, end to end."""
    _picture_pdf(tmp_path / "scan.pdf")
    _picture_pdf(tmp_path / "broken_scan.pdf")
    mock_supabase.queue_fetch([
        _row(1, file_name="samsung_005930_20260511_MERITZ_1096333.pdf"),
        _row(2, file_path="scan.pdf", file_name="scan.pdf"),
        _row(3, file_path="broken_scan.pdf", file_name="broken_scan.pdf"),
    ])
    answer = make_llm_extraction()

    async def parse(**kwargs):
        from research_desk.core.llm import StructuredResult
        if "broken_scan.pdf" in kwargs["user"]:
            raise RuntimeError("boom")
        return StructuredResult(parsed=answer)
    mock_llm_client.parse = parse

    report = await run_batch(
        sb=mock_supabase, client=mock_llm_client, krx=krx,
        taxonomy_version="KRX@2026-05-08",
        batch_size=10, dry_run=True, row_ids=[],
        model="gpt-5.4-mini", max_concurrent_llm=1, worker_id="w1",
    )

    assert mock_supabase.executed == []
    assert (report["page_image"], report["page_image_unsupported"], report["publisher_suspect"]) == (
        1, 0, 1)
    assert report["rows"] == [
        {"id": 1, "tagging_status": "auto", "tagging_confidence": "medium",
         "report_type": "단일종목", "publisher": "키움증권", "publisher_type": "broker",
         "tagging_notes": "publisher_suspect:filename_mismatch"},
        {"id": 2, "tagging_status": "auto", "tagging_confidence": "medium",
         "report_type": "단일종목", "publisher": "키움증권", "publisher_type": "broker",
         "tagging_notes": "page_image"},
        {"id": 3, "error": "unhandled", "detail": "RuntimeError:boom"},
    ]


@pytest.mark.asyncio
async def test_a_normal_batch_report_has_the_counts_but_no_rows(krx, mock_llm_client, mock_supabase,
                                                                make_pdf):
    mock_supabase.queue_fetch([_row(1, file_name="samsung_005930_20260511_MERITZ_1096333.pdf")])
    mock_llm_client.set_response(make_llm_extraction())

    report = await run_batch(
        sb=mock_supabase, client=mock_llm_client, krx=krx,
        taxonomy_version="KRX@2026-05-08",
        batch_size=10, dry_run=False, row_ids=[],
        model="gpt-5.4-mini", max_concurrent_llm=2, worker_id="w1",
    )

    assert "rows" not in report
    assert (report["page_image"], report["page_image_unsupported"], report["publisher_suspect"]) == (
        0, 0, 1)
