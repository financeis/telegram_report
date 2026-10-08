"""Orchestrator batch flow tests with mock LLM client + mock supabase."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from langgraph_tagger.orchestrator import run_batch
from langgraph_tagger.tests.conftest import make_llm_extraction


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
    from langgraph_tagger.orchestrator import _empty_report
    rep = _empty_report("m")
    assert set(rep["oos"].keys()) == {"foreign", "fund", "digital", "private", "ir_self"}
    assert all(v == 0 for v in rep["oos"].values())
    assert rep["review_reasons"] == {}


def test_aggregate_oos_includes_ir_self():
    """v2: oos_reason='ir_self' is counted alongside foreign/fund/digital/private."""
    from langgraph_tagger.orchestrator import _aggregate
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
    from langgraph_tagger.orchestrator import _aggregate
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
    from langgraph_tagger.orchestrator import _aggregate
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
    from langgraph_tagger.orchestrator import _aggregate
    results = [
        {"id": 1, "tagging_status": "review_needed",
         "tagging_notes": "llm_refusal:rate_limit_exceeded"},
        {"id": 2, "tagging_status": "review_needed",
         "tagging_notes": "first_page_unreadable;llm_refusal:timeout"},
    ]
    rep = _aggregate(results, model="m", batch_size=2, dry_run=False)
    assert rep["review_reasons"]["llm_refusal"] == 2
    assert rep["review_reasons"]["first_page_unreadable"] == 1
