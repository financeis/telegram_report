"""End-to-end graph smoke tests with mock LLM client + mock supabase."""
from datetime import datetime, timezone

import pytest

from research_desk.tagger.graph import build_graph
from research_desk.tagger.tests.conftest import make_llm_extraction


@pytest.mark.asyncio
async def test_in_scope_single_stock_flows_end_to_end(krx, mock_llm_client, mock_supabase, tmp_path, monkeypatch):
    """In-scope 경로의 진짜 end-to-end. tiny PDF 합성으로 extract_pdf 통과시키고
    resolve_krx → decide_status → write까지 검증."""
    import fitz
    pdf = tmp_path / "samsung.pdf"
    doc = fitz.open()
    doc.new_page().insert_text(
        (72, 72),
        "키움증권 리서치센터\n분석가 홍길동\n삼성전자 [005930]\n투자의견 매수 목표주가 100,000원",
        fontsize=11,
    )
    doc.save(pdf)
    doc.close()
    monkeypatch.setenv("STORAGE_BASE_DIR", str(tmp_path))

    mock_llm_client.set_response(make_llm_extraction(
        report_type="단일종목",
        stock_codes_raw=["005930"],
        sectors_major=["반도체"],
        sectors_minor=["메모리반도체"],
        publisher_raw="키움",
        topics=["연준"],
    ))
    app = build_graph(mock_llm_client, mock_supabase, krx=krx,
                      dry_run=False, taxonomy_version="KRX@2026-05-08")

    init = {
        "id": 1,
        "file_path": "samsung.pdf",
        "file_name": "samsung.pdf",
        "sent_at": datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc),
        "caption": None,
        "chat_username": "x",
        "worker_id": "test",
        "model": "gpt-5.4-mini",
    }
    final = await app.ainvoke(init)

    # In-scope path: not OOS, not unreadable
    assert final["tagging_status"] == "auto"
    assert final.get("is_oos") is not True
    # write was called once with the expected resolved payload
    assert len(mock_supabase.executed) == 1
    sql, args = mock_supabase.executed[0]
    assert args[2] == "단일종목"          # report_type
    assert args[3] == "키움증권"           # publisher canonical
    assert args[4] == "broker"             # publisher_type
    assert "005930" in args[7]             # stock_codes_final
    assert args[14] is None                # out_of_scope_reason


@pytest.mark.asyncio
async def test_unreadable_pdf_routes_to_status_unreadable(krx, mock_llm_client, mock_supabase, monkeypatch, tmp_path):
    """Missing file → status_unreadable → review_needed/low. Splits the
    smoke coverage so the in-scope test above can't accidentally fall
    back to this path again."""
    monkeypatch.setenv("STORAGE_BASE_DIR", str(tmp_path))   # empty dir
    app = build_graph(mock_llm_client, mock_supabase, krx=krx,
                      dry_run=False, taxonomy_version="KRX@2026-05-08")

    init = {
        "id": 99,
        "file_path": "missing.pdf",
        "file_name": "missing.pdf",
        "sent_at": datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc),
        "caption": None,
        "chat_username": "x",
        "worker_id": "test",
        "model": "gpt-5.4-mini",
    }
    final = await app.ainvoke(init)

    assert final["tagging_status"] == "review_needed"
    assert final["tagging_confidence"] == "low"
    assert final["tagging_notes"] == "first_page_unreadable"
    # llm_extract should NOT have been called for an unreadable PDF
    mock_llm_client.parse.assert_not_called()
    # no AI result → no publisher checks
    assert not {"publisher_final", "publisher_type_final", "publisher_suspect"} & set(final)


@pytest.mark.asyncio
async def test_oos_foreign_short_circuits_to_status_oos(krx, mock_llm_client, mock_supabase, tmp_path, monkeypatch):
    # Make a tiny PDF so extract_pdf succeeds
    import fitz
    pdf = tmp_path / "x.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "키움증권 분석가 홍길동", fontsize=12)
    doc.save(pdf)
    doc.close()
    monkeypatch.setenv("STORAGE_BASE_DIR", str(tmp_path))

    # Mock LLM to return foreign primary coverage signal
    from research_desk.tagger.llm_schemas import OOSSignals
    mock_llm_client.set_response(make_llm_extraction(
        report_type="기타",
        oos_signals=OOSSignals(
            foreign_primary_coverage=True, etf_or_fund=False,
            digital_asset=False, private_company_likely=False,
        ),
    ))

    app = build_graph(mock_llm_client, mock_supabase, krx=krx,
                      dry_run=False, taxonomy_version="KRX@2026-05-08")

    init = {
        "id": 2,
        "file_path": "x.pdf",
        "file_name": "x.pdf",
        "sent_at": datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc),
        "caption": None,
        "chat_username": "x",
        "worker_id": "test",
        "model": "gpt-5.4-mini",
    }
    final = await app.ainvoke(init)

    assert final["tagging_status"] == "auto"
    assert final["tagging_confidence"] == "high"
    assert final["oos_reason"] == "foreign"
    # write was called once with report_type=기타 + oos_reason=foreign
    assert len(mock_supabase.executed) == 1
    sql, args = mock_supabase.executed[0]
    assert args[2] == "기타"
    assert args[14] == "foreign"


def test_graph_has_8_nodes():
    """v2 graph has 8 nodes (down from v1's 10)."""
    from unittest.mock import MagicMock
    from research_desk.domain.stocks import StockList
    from research_desk.tagger.graph import build_graph
    from research_desk.tagger.tests.conftest import BUNDLED_CSV

    krx = StockList.load(BUNDLED_CSV)
    app = build_graph(
        client=MagicMock(), sb=MagicMock(),
        krx=krx, dry_run=True, taxonomy_version="t",
    )
    # LangGraph compiled app exposes nodes via .get_graph().nodes (1.0 API).
    nodes = app.get_graph().nodes
    # Subtract LangGraph's internal __start__/__end__ nodes if present.
    user_nodes = {n for n in nodes if not n.startswith("__")}
    assert user_nodes == {
        "extract_pdf", "llm_extract", "mark_oos_reason",
        "status_oos", "status_unreadable", "resolve_krx",
        "decide_status", "write",
    }


# ── publisher checks through the whole graph ─────────────────────────────────

def _pdf(tmp_path, monkeypatch, name: str) -> None:
    import fitz
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "리서치센터 분석가 홍길동 투자의견 매수", fontname="korea", fontsize=11)
    doc.save(tmp_path / name)
    doc.close()
    monkeypatch.setenv("STORAGE_BASE_DIR", str(tmp_path))


def _init(id_: int, file_name: str, model: str = "gpt-5.4-mini") -> dict:
    return {
        "id": id_, "file_path": file_name, "file_name": file_name,
        "sent_at": datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc),
        "caption": None, "chat_username": "x", "worker_id": "test", "model": model,
    }


@pytest.mark.asyncio
async def test_an_alias_answer_is_written_as_null_without_an_error(krx, mock_llm_client, mock_supabase,
                                                                   tmp_path, monkeypatch):
    """The model's JSON says "Eugene" (an alias): validation keeps the row going, and the
    row is written with a null publisher and type — no exception, so no revert."""
    import json

    from research_desk.core.llm import StructuredResult

    name = "samsung_005930_20260511_Eugene_1096333.pdf"
    _pdf(tmp_path, monkeypatch, name)
    reply = json.loads(make_llm_extraction().model_dump_json())
    reply.update(publisher_canon="Eugene", publisher_type="broker")
    text = json.dumps(reply, ensure_ascii=False)

    async def parse(*, schema, **_):
        return StructuredResult(parsed=schema.model_validate_json(text))
    mock_llm_client.parse.side_effect = parse

    app = build_graph(mock_llm_client, mock_supabase, krx=krx,
                      dry_run=False, taxonomy_version="KRX@2026-05-08")
    final = await app.ainvoke(_init(5, name))

    assert (final["publisher_final"], final["publisher_type_final"], final["publisher_suspect"]) == (
        None, None, "filename_mismatch")
    (_, args), = mock_supabase.executed
    assert (args[3], args[4]) == (None, None)
    assert args[2] == "단일종목"


@pytest.mark.asyncio
async def test_oos_rows_get_the_same_publisher_checks(krx, mock_llm_client, mock_supabase,
                                                      tmp_path, monkeypatch):
    """An IR자료 file tagged with a broker: the stored publisher stays the AI's 해당기업
    (type other from the dictionary); the tag only marks a filename mismatch."""
    name = "ecopro_ir_20260511_MERITZ_1096333.pdf"
    _pdf(tmp_path, monkeypatch, name)
    mock_llm_client.set_response(make_llm_extraction(
        report_type="IR자료", publisher_canon="해당기업", stock_codes_raw=[], company_names_raw=["에코프로"],
    ))

    app = build_graph(mock_llm_client, mock_supabase, krx=krx,
                      dry_run=False, taxonomy_version="KRX@2026-05-08")
    final = await app.ainvoke(_init(6, name))

    assert final["oos_reason"] == "ir_self"
    assert (final["publisher_final"], final["publisher_type_final"], final["publisher_suspect"]) == (
        "해당기업", "other", "filename_mismatch")
    (_, args), = mock_supabase.executed
    assert (args[3], args[4], args[14]) == ("해당기업", "other", "ir_self")


@pytest.mark.asyncio
async def test_in_scope_rows_keep_the_ai_publisher_against_the_filename_tag(krx, mock_llm_client,
                                                                            mock_supabase, tmp_path,
                                                                            monkeypatch):
    name = "samsung_005930_20260511_MERITZ_1096333.pdf"
    _pdf(tmp_path, monkeypatch, name)
    mock_llm_client.set_response(make_llm_extraction(publisher_canon="키움증권"))

    app = build_graph(mock_llm_client, mock_supabase, krx=krx,
                      dry_run=False, taxonomy_version="KRX@2026-05-08")
    final = await app.ainvoke(_init(7, name))

    assert final["publisher_suspect"] == "filename_mismatch"
    (_, args), = mock_supabase.executed
    assert (args[3], args[4]) == ("키움증권", "broker")    # not 메리츠증권


# ── PDFs with no text on pages 1–3: page 1 is read as a picture ──────────────

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _picture_pdf(tmp_path, monkeypatch, name: str = "scan.pdf", pages: int = 1) -> None:
    """Pages that carry only a picture and no text layer, like a scanned report."""
    import fitz
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page()
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 20), False)
        pix.clear_with(180)
        page.insert_image(fitz.Rect(72, 72, 272, 172), pixmap=pix)
    doc.save(tmp_path / name)
    doc.close()
    monkeypatch.setenv("STORAGE_BASE_DIR", str(tmp_path))


def _graph(mock_llm_client, mock_supabase, krx, dry_run: bool = False):
    return build_graph(mock_llm_client, mock_supabase, krx=krx,
                       dry_run=dry_run, taxonomy_version="KRX@2026-05-08")


@pytest.mark.asyncio
async def test_a_picture_only_pdf_is_read_from_its_page_png(krx, mock_llm_client, mock_supabase,
                                                           tmp_path, monkeypatch):
    from research_desk.tagger.prompts import PAGE_IMAGE_NOTE

    _picture_pdf(tmp_path, monkeypatch, pages=2)
    mock_llm_client.set_response(make_llm_extraction())      # 단일종목 005930, 키움증권

    final = await _graph(mock_llm_client, mock_supabase, krx).ainvoke(_init(10, "scan.pdf"))

    kwargs = mock_llm_client.parse.call_args.kwargs
    (png,) = kwargs["images"]
    assert png.startswith(PNG_SIGNATURE)
    assert PAGE_IMAGE_NOTE in kwargs["user"]
    assert final["page_image"] is True
    assert final["pdf_unreadable"] is False
    assert "page_image_unsupported" not in final
    assert not final.get("page_images")                     # the PNG is dropped after the call
    # the in-scope branch, as for a text row
    assert final["tagging_status"] == "auto"
    assert final.get("is_oos") is not True
    assert final["krx_matched"] is True
    assert (final["publisher_final"], final["publisher_type_final"]) == ("키움증권", "broker")
    (_, args), = mock_supabase.executed
    assert args[2] == "단일종목"
    assert (args[3], args[4]) == ("키움증권", "broker")
    assert list(args[7]) == ["005930"]
    assert list(args[8]) == ["삼성전자"]
    assert args[14] is None


@pytest.mark.asyncio
async def test_a_picture_only_pdf_can_be_out_of_scope(krx, mock_llm_client, mock_supabase,
                                                     tmp_path, monkeypatch):
    from research_desk.tagger.llm_schemas import OOSSignals

    _picture_pdf(tmp_path, monkeypatch)
    mock_llm_client.set_response(make_llm_extraction(
        report_type="기타", stock_codes_raw=[], company_names_raw=["Disney"],
        oos_signals=OOSSignals(foreign_primary_coverage=True, etf_or_fund=False,
                               digital_asset=False, private_company_likely=False),
    ))

    final = await _graph(mock_llm_client, mock_supabase, krx).ainvoke(_init(11, "scan.pdf"))

    assert final["page_image"] is True
    assert final["oos_reason"] == "foreign"
    assert final["tagging_status"] == "auto"
    (_, args), = mock_supabase.executed
    assert (args[2], args[14]) == ("기타", "foreign")
    assert list(args[7]) == []


@pytest.mark.asyncio
async def test_a_refused_picture_row_is_a_refusal(krx, mock_llm_client, mock_supabase,
                                                  tmp_path, monkeypatch):
    _picture_pdf(tmp_path, monkeypatch)
    mock_llm_client.set_refusal("cannot read")

    final = await _graph(mock_llm_client, mock_supabase, krx).ainvoke(_init(12, "scan.pdf"))

    assert final["page_image"] is True
    assert final["tagging_status"] == "review_needed"
    # spec §3.2: a refused picture row is a refusal, and also carries the page_image note.
    assert final["tagging_confidence"] == "low"
    assert final["tagging_notes"] == "llm_refusal:cannot read;page_image"
    (_, args), = mock_supabase.executed
    assert (args[15], args[16], args[17]) == ("review_needed", "low",
                                              "llm_refusal:cannot read;page_image")


@pytest.mark.asyncio
async def test_a_picture_row_is_written_at_most_medium_with_the_page_image_note(
        krx, mock_llm_client, mock_supabase, tmp_path, monkeypatch):
    _picture_pdf(tmp_path, monkeypatch)
    mock_llm_client.set_response(make_llm_extraction())      # 단일종목 005930, 키움증권

    final = await _graph(mock_llm_client, mock_supabase, krx).ainvoke(_init(18, "scan.pdf"))

    assert final["publisher_suspect"] is None
    (_, args), = mock_supabase.executed
    assert (args[15], args[16], args[17]) == ("auto", "medium", "page_image")


@pytest.mark.asyncio
async def test_a_suspect_text_row_is_written_at_most_medium_with_the_suspect_note(
        krx, mock_llm_client, mock_supabase, tmp_path, monkeypatch):
    name = "samsung_005930_20260511_MERITZ_1096333.pdf"
    _pdf(tmp_path, monkeypatch, name)
    mock_llm_client.set_response(make_llm_extraction(publisher_canon="키움증권"))

    final = await _graph(mock_llm_client, mock_supabase, krx).ainvoke(_init(19, name))

    assert "page_image" not in final
    (_, args), = mock_supabase.executed
    assert (args[15], args[16], args[17]) == ("auto", "medium", "publisher_suspect:filename_mismatch")


@pytest.mark.asyncio
async def test_a_picture_row_the_model_cannot_take_gets_no_extra_note(
        krx, mock_llm_client, mock_supabase, tmp_path, monkeypatch):
    _picture_pdf(tmp_path, monkeypatch)

    await _graph(mock_llm_client, mock_supabase, krx).ainvoke(_init(20, "scan.pdf", "gpt-4"))

    (_, args), = mock_supabase.executed
    assert (args[15], args[16], args[17]) == ("review_needed", "low", "first_page_unreadable")


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["gpt-4", "codex:o3-mini"])
async def test_a_picture_only_pdf_with_a_text_only_model_is_unreadable(krx, mock_llm_client,
                                                                       mock_supabase, tmp_path,
                                                                       monkeypatch, model):
    _picture_pdf(tmp_path, monkeypatch)

    final = await _graph(mock_llm_client, mock_supabase, krx).ainvoke(_init(13, "scan.pdf", model))

    mock_llm_client.parse.assert_not_called()
    assert final["page_image_unsupported"] is True
    assert "page_image" not in final
    assert (final["tagging_status"], final["tagging_confidence"], final["tagging_notes"]) == (
        "review_needed", "low", "first_page_unreadable")
    (_, args), = mock_supabase.executed
    assert args[2] is None


@pytest.mark.asyncio
async def test_parse_refusing_pictures_does_not_fail_the_row(krx, mock_llm_client, mock_supabase,
                                                             tmp_path, monkeypatch):
    from research_desk.core.llm import ImageInputUnsupported

    _picture_pdf(tmp_path, monkeypatch)
    mock_llm_client.set_exception(ImageInputUnsupported("model gpt-5.4-mini cannot take page images"))

    final = await _graph(mock_llm_client, mock_supabase, krx).ainvoke(_init(14, "scan.pdf"))

    assert final["page_image_unsupported"] is True
    assert final["tagging_notes"] == "first_page_unreadable"
    assert len(mock_supabase.executed) == 1


@pytest.mark.asyncio
async def test_an_encrypted_pdf_is_unreadable_without_an_ai_call(krx, mock_llm_client, mock_supabase,
                                                                 tmp_path, monkeypatch):
    import fitz
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "키움증권 리서치센터", fontname="korea", fontsize=11)
    doc.save(tmp_path / "locked.pdf", encryption=fitz.PDF_ENCRYPT_AES_256,
             user_pw="user", owner_pw="owner")
    doc.close()
    monkeypatch.setenv("STORAGE_BASE_DIR", str(tmp_path))

    final = await _graph(mock_llm_client, mock_supabase, krx).ainvoke(_init(15, "locked.pdf"))

    mock_llm_client.parse.assert_not_called()
    assert final["tagging_notes"] == "first_page_unreadable"
    assert not {"page_image", "page_image_unsupported"} & set(final)


@pytest.mark.asyncio
async def test_text_rows_are_asked_without_pictures(krx, mock_llm_client, mock_supabase,
                                                    tmp_path, monkeypatch):
    _pdf(tmp_path, monkeypatch, "text.pdf")
    mock_llm_client.set_response(make_llm_extraction())

    final = await _graph(mock_llm_client, mock_supabase, krx).ainvoke(_init(16, "text.pdf"))

    assert "images" not in mock_llm_client.parse.call_args.kwargs
    assert not {"page_image", "page_image_unsupported", "page_images"} & set(final)


@pytest.mark.asyncio
async def test_dry_run_reads_picture_rows_the_same_way(krx, mock_llm_client, mock_supabase,
                                                       tmp_path, monkeypatch):
    _picture_pdf(tmp_path, monkeypatch)
    mock_llm_client.set_response(make_llm_extraction())

    final = await _graph(mock_llm_client, mock_supabase, krx, dry_run=True).ainvoke(
        _init(17, "scan.pdf"))

    assert len(mock_llm_client.parse.call_args.kwargs["images"]) == 1
    assert final["page_image"] is True
    assert final["tagging_status"] == "auto"
    assert mock_supabase.executed == []
