"""resolve_krx node (v2): canonicalize+validate+enrich 통합.

report_type별 KRX lookup 정책 분기:
- 단일종목: 1 entry (stock_code 우선, 회사명 fallback)
- 섹터: N entry aggregate (stock_codes_raw + company_names_raw 모두 시도, dedupe)
- 산업 / 전략·시황: lookup skip
- 기타 (in-scope): 단일종목과 동일

published_at fallback (LLM published_at == None → sent_at KST date).
Stock lookups use the shared stock list (research_desk.domain.stocks) as is:
codes are matched exactly, without zero-padding.
"""
from __future__ import annotations

from datetime import date, timedelta, timezone
from typing import Optional

from research_desk.domain.stocks import StockEntry, StockList
from research_desk.tagger.state import RowState

KST = timezone(timedelta(hours=9))


def _parse_iso_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def resolve_krx(state: RowState, *, krx: StockList) -> dict:
    raw = state["llm_raw"]
    rt = raw.report_type

    # 산업 / 전략·시황: KRX lookup 자체를 skip
    if rt in ("산업", "전략·시황"):
        return _finalize(state, raw, krx=krx, entries=[], skipped=True, mismatch=False)

    # 단일종목 / 섹터 / 기타: KRX 시도
    entries: list[StockEntry] = []
    seen: set[str] = set()
    code_match_any = False

    # 1. stock_codes_raw 모두 lookup (모든 valid code dedupe)
    for code in raw.stock_codes_raw:
        if krx.validate_code(code):
            e = krx.lookup(code)
            if e and e.code not in seen:
                entries.append(e)
                seen.add(e.code)
                code_match_any = True

    # 2. 단일종목/기타에서 stock_code 미매칭이면 회사명 1개 fallback
    if rt in ("단일종목", "기타") and not entries:
        for name in raw.company_names_raw:
            e = krx.lookup_by_name(name)
            if e and e.code not in seen:
                entries.append(e)
                seen.add(e.code)
                break  # 단일종목/기타는 1개

    # 3. 섹터에서 회사명도 모두 추가 lookup (이미 stock_code로 잡힌 것은 dedupe)
    if rt == "섹터":
        for name in raw.company_names_raw:
            e = krx.lookup_by_name(name)
            if e and e.code not in seen:
                entries.append(e)
                seen.add(e.code)

    # 4. 단일종목/기타는 1개로 자른다
    if rt in ("단일종목", "기타") and len(entries) > 1:
        entries = entries[:1]

    # 5. name/code mismatch 감지 (단일종목 + stock_code 매칭 케이스만)
    mismatch = False
    if rt == "단일종목" and entries and code_match_any and raw.company_names_raw:
        norm_entry = "".join(entries[0].name.split()).lower()
        norm_raws = ["".join(n.split()).lower() for n in raw.company_names_raw]
        mismatch = norm_entry not in norm_raws

    return _finalize(state, raw, krx=krx, entries=entries, skipped=False, mismatch=mismatch)


def _finalize(state, raw, *, krx: StockList, entries: list[StockEntry], skipped: bool,
              mismatch: bool) -> dict:
    """entries → final 컬럼 + published_at fallback."""
    if entries:
        sm: list[str] = []
        smn: list[str] = []
        seen_sm: set[str] = set()
        seen_smn: set[str] = set()
        prods: list[str] = []
        seen_p: set[str] = set()
        for e in entries:
            if e.sector_major and e.sector_major not in seen_sm:
                sm.append(e.sector_major); seen_sm.add(e.sector_major)
            if e.sector_minor and e.sector_minor not in seen_smn:
                smn.append(e.sector_minor); seen_smn.add(e.sector_minor)
            for p in krx.split_products(e.products_text):
                if p not in seen_p:
                    prods.append(p); seen_p.add(p)
        result = {
            "krx_lookup_skipped": skipped,
            "krx_matched": True,
            "krx_entries": entries,
            "krx_name_code_mismatch": mismatch,
            "stock_codes_final": [e.code for e in entries],
            "company_names_final": [e.name for e in entries],
            "sectors_major_final": sm,
            "sectors_minor_final": smn,
            "products_final": prods,
        }
    else:
        # entries가 비어있는 경우: 산업/전략·시황(skipped=True), 또는 KRX 미매칭(skipped=False)
        if skipped:
            company_names_final: list[str] = []
        else:
            company_names_final = list(raw.company_names_raw)   # 미매칭 fallback
        result = {
            "krx_lookup_skipped": skipped,
            "krx_matched": False,
            "krx_entries": [],
            "krx_name_code_mismatch": False,
            "stock_codes_final": [],
            "company_names_final": company_names_final,
            "sectors_major_final": [],
            "sectors_minor_final": [],
            "products_final": [],
        }

    # published_at 폴백
    pub = _parse_iso_date(raw.published_at)
    used_fallback = False
    if pub is None:
        sent_at = state["sent_at"]
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=timezone.utc)
        pub = sent_at.astimezone(KST).date()
        used_fallback = True
    result["published_at_final"] = pub
    result["used_sent_at_fallback"] = used_fallback
    return result
