"""Report analysis: analyze one selected report, read summaries, save comparisons.

``analyze_report(row)`` gets a row the reports feature has already read (its
404). Then, in this order (spec §9.5):

1. the same report is already being analyzed → 409;
2. inside an AI slot (like before, the rest of the analysis runs in one):
   not 단일종목 → 422;
3. a saved summary (active version) with financial_details → returned as is;
4. model key / codex CLI (``phase2_llm``) → ``NotReady("분석", …)``;
5. PDF in the storage folder (404) with readable text (422);
6. the AI call, arithmetic target-price direction, number grounding, save.

So reuse, summaries_for, 409 and 422 never need a model key.

``ai_slot()`` limits the web server to 2 AI calls at once, analysis and compare
together. The Supabase REST client is prepared on first use (re-reading
``.env``); a failed preparation is tried again on the next call.
"""
from __future__ import annotations

import asyncio
import threading
import weakref
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Iterable, Mapping, Optional

from fastapi import HTTPException

from research_desk.core import db, pdf
from research_desk.core import settings as core_settings
from research_desk.core.llm import LLMClient, api_key_env
from research_desk.core.settings import NotReady

from . import store
from .financials import ground_metrics
from .llm import extract_one
from .logic import normalize_target_price_dir
from .pdf_text import extract_all_pages
from .settings import load_settings, summary_version

AREA = "분석"

IN_FLIGHT = "이 보고서는 분석 중입니다. 잠시 후 새로고침해 주세요."
NOT_SINGLE_COMPANY = "금융 정보 분석은 단일종목 보고서를 선택해 주세요."
NO_TEXT = "PDF에서 읽을 수 있는 텍스트가 없습니다."
DB_NOT_CONFIGURED = "DB 접속 설정(SUPABASE_URL, SUPABASE_SERVICE_KEY)이 없습니다"
CODEX_MISSING = "codex CLI를 찾을 수 없습니다. 설치 후 `codex login`으로 로그인하세요."

# The local web server is one process; at most this many AI calls at once.
AI_CONCURRENCY = 2
SUMMARY_BATCH = 100
METADATA_KEYS = ("publisher", "stock_codes", "published_at", "title")


def _key_missing(env: str) -> str:
    return f"{env}가 설정되지 않았습니다. .env에 추가 후 분석 다시 시도하세요."


# ── AI slot ──────────────────────────────────────────────────────────────────

# One semaphore per running event loop, made on first use inside that loop: an
# asyncio.Semaphore cannot be shared across loops (the server has one loop).
_slots: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = (
    weakref.WeakKeyDictionary())
_slots_lock = threading.Lock()


def _slot_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    with _slots_lock:
        semaphore = _slots.get(loop)
        if semaphore is None:
            semaphore = _slots[loop] = asyncio.Semaphore(AI_CONCURRENCY)
        return semaphore


@asynccontextmanager
async def ai_slot() -> AsyncIterator[None]:
    """Hold one of the web server's 2 AI-call slots (shared by analysis and compare)."""
    async with _slot_semaphore():
        yield


# ── model connection ─────────────────────────────────────────────────────────

def phase2_llm() -> tuple[LLMClient, str, int]:
    """``(AI client, model name, per-call timeout in seconds)`` for an AI call now.

    Re-reads ``.env`` first, so a key added there counts without a restart.
    Raises NotReady("분석", …) when the model's API key or the codex CLI is
    missing. The client keeps the SDK's default retries; use it as
    ``async with client:`` so it is closed afterwards.
    """
    core_settings.load_env()
    cfg = load_settings()
    client = LLMClient(openai_api_key=core_settings.openai_api_key(),
                       anthropic_api_key=core_settings.anthropic_api_key())
    try:
        client.require_key(cfg.llm_model)
    except RuntimeError:
        env = api_key_env(cfg.llm_model)
        if env is None:
            raise NotReady(AREA, CODEX_MISSING) from None
        raise NotReady(AREA, _key_missing(env)) from None
    return client, cfg.llm_model, cfg.per_report_timeout_s


# ── report_summaries ─────────────────────────────────────────────────────────

_supabase_client: Optional[Any] = None
_supabase_lock = threading.Lock()


def _supabase():
    """The Supabase REST client, prepared on first use (NotReady without DB settings)."""
    global _supabase_client
    with _supabase_lock:
        if _supabase_client is None:
            core_settings.load_env()
            url, key = core_settings.supabase_url(), core_settings.supabase_service_key()
            if not (url and key):
                raise NotReady(AREA, DB_NOT_CONFIGURED)
            _supabase_client = db.supabase_client(url, key)
        return _supabase_client


def summaries_for(ids: Iterable[int]) -> dict[int, dict[str, Any]]:
    """Saved summaries of the active version, ``{report_id: summary}``, read 100 ids at a time."""
    ids = list(ids)
    if not ids:
        return {}
    sb = _supabase()
    version = summary_version()
    result: dict[int, dict[str, Any]] = {}
    for i in range(0, len(ids), SUMMARY_BATCH):
        result.update(store.fetch_summaries(sb, ids[i:i + SUMMARY_BATCH], version))
    return result


def save_comparison(report_id: int, prev_report_id: Optional[int], match_type: str,
                    narrative: Optional[str], comparison_details: Optional[dict] = None) -> None:
    """Store a comparison on the later report's summary (only the four comparison fields)."""
    store.update_diff(_supabase(), report_id, prev_report_id, match_type, narrative,
                      comparison_details)


# ── analysis ─────────────────────────────────────────────────────────────────

_analyzing: set[int] = set()


async def analyze_report(row: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    """Analyze one report row → ``(saved summary, reused)``. See the module docstring."""
    rid = row['id']
    if rid in _analyzing:
        raise HTTPException(409, IN_FLIGHT)
    _analyzing.add(rid)
    try:
        async with ai_slot():
            if row.get('report_type') != '단일종목':
                raise HTTPException(422, NOT_SINGLE_COMPANY)
            existing = (await asyncio.to_thread(summaries_for, [rid])).get(rid)
            if existing and existing.get('financial_details'):
                return existing, True
            llm, model, timeout_s = phase2_llm()
            cfg = load_settings()
            try:
                path = pdf.resolve_in_storage(core_settings.storage_base_dir(), row['file_path'])
            except pdf.PDFNotFound as e:
                raise HTTPException(404, e.message) from None
            text = await asyncio.to_thread(extract_all_pages, path, cfg.max_input_tokens)
            if not text.text:
                raise HTTPException(422, NO_TEXT)
            async with llm as client:
                result, tokens_in, tokens_out = await extract_one(
                    client=client, model=model,
                    metadata={k: row.get(k) for k in METADATA_KEYS},
                    pages_text=text.text, timeout_s=timeout_s,
                )
            result = normalize_target_price_dir(result)
            payload: dict[str, Any] = result.model_dump()
            if result.financial_details:
                grounded, omitted = ground_metrics(result.financial_details, text.text)
                payload['financial_details'] = (grounded.model_dump()
                                                | {'unsupported_numeric_values': omitted})
            payload.update(report_id=rid, input_truncated=text.input_truncated,
                           input_pages_used=text.pages_used, input_total_pages=text.total_pages,
                           summary_version=cfg.summary_version, llm_model=model,
                           llm_tokens_input=tokens_in, llm_tokens_output=tokens_out,
                           prev_report_id=None, prev_match_type=None, diff_narrative=None,
                           comparison_details=None)
            await asyncio.to_thread(store.upsert_summary, _supabase(), payload)
            return payload, False
    finally:
        _analyzing.discard(rid)
