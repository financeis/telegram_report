"""Peers work for the web: a company's peers list and theme search (spec §6.2–§6.4, §7, §9, §10,
§12.1, §12.2, §12.4).

Readiness (spec §12.4). Creating the service reads nothing. The first request prepares, in this
order, and the process keeps what succeeded: ``core.settings.load_env()``, the DB settings
(SUPABASE_URL, SUPABASE_SERVICE_KEY) → the Supabase REST client; then the stock list from
``KRX_CSV_PATH``. Failures are ``NotReady("유사 기업", …)`` with a fixed sentence (the path and
the cause go to the local log only) and are not remembered, so filling in the setting or the
file recovers on the next request. A stock list whose content differs from its version file (or
without one) only logs a warning. These two are all the process keeps as readiness.

The public build. Every request looks up the latest ``done`` build (one small read, no tables),
so a build that becomes public is used from the next request without a restart; without one the
request is ``NotReady("유사 기업", "아직 공개된 유사도 계산 결과가 없습니다(python -m research_desk
peers build)")`` and nothing is remembered. A build's tables (percentile tables, term table) can
take megabytes: they are read once per build id and kept (a public build never changes). Theme
search reads the synonym table (``synonyms.yaml``) once per build id too, and logs a warning when
its fingerprint differs from the one the build recorded.

The peers list (``peers``), in this order: readiness; the latest public build; 404 ``종목을 찾을
수 없습니다.`` for a code not in the stock list; 404 ``이 종목은 유사 기업 자료가 없습니다.`` without
an ``ok`` profile of the build's fiscal year and profile version or without its company embedding
of the build's model; 422 ``그 사업부문이 없습니다.`` for a segment number the seed does not have
(the format checks are the router's, before any of this). Then:

- the seed's segments in §6.2 order; the chosen one is the asked number, else the first; a seed
  without segments has no segment side;
- ``match_company_profiles`` with the seed's company vector and ``match_company_segments`` with
  the chosen segment's vector (``MATCH_LIMIT`` rows each, the most the functions give), each
  company's best segment, the seed and codes not in the stock list left out, then the nearest
  ``COMPANY_TOP`` / ``SEGMENT_TOP``; codes without an ``ok`` profile row are left out too;
- ``logic.merge_peers`` (grading with the build's tables, at most ``PEERS_MAX``), shared terms
  from the profiles' ``terms`` and the build's term table, the same industry from the stock list's
  ``산업명(중)``, each peer's segments;
- report counts of the seed and the peers through ``coverage.report_counts(codes, days=365)`` and
  their prices through ``prices.snapshots(codes)`` (its ``market`` key is dropped), one call each;
  the reaction for the chosen window and the candidate flag.

Theme search (``search``): readiness and the latest build; the query normalized with the synonym
table; its embedding with the build's model at 1536 dimensions, from a cache of the last
``QUERY_CACHE_SIZE`` (model, normalized query) pairs, else one call in this feature's own slots
(``EMBED_CONCURRENCY`` at once in the process, not ``analysis.ai_slot()``: a sub-second call must
not queue behind minute-long analyses) within ``EMBED_TIMEOUT_S`` seconds. Before a call the
``.env`` is read again and a missing OPENAI_API_KEY is ``NotReady("유사 기업", "OPENAI_API_KEY가
설정되지 않았습니다")`` — on this address only. Three rankings fused with RRF (k = 60): companies
by their embedding, companies by their best segment (``COMPANY_TOP`` / ``SEGMENT_TOP`` each), and
companies whose ``terms`` hold a query term exactly; only companies with an ``ok`` profile of the
build and in the stock list. A timeout or a refused call is that request's error (the web app's
general 503), never NotReady.

Other features' NotReady (``리포트``/``커버리지`` from report counts, ``주가`` from prices) passes
through unchanged. An empty price table is not a readiness problem: ``price`` is null. Sync DB work
runs in the handler's thread (``def``) or in ``asyncio.to_thread`` (search).
"""
from __future__ import annotations

import asyncio
import logging
import threading
import weakref
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Optional

from fastapi import HTTPException

from research_desk.core import db as core_db
from research_desk.core import settings as core_settings
from research_desk.core.llm import LLMClient
from research_desk.core.settings import NotReady
from research_desk.domain.stocks import StockList, StockListError
from research_desk.features import coverage, prices

from . import logic
from . import settings as peers_settings
from .store import PeersStore

logger = logging.getLogger(__name__)

AREA = "유사 기업"
DB_NOT_CONFIGURED = "DB 접속 설정(SUPABASE_URL, SUPABASE_SERVICE_KEY)이 없습니다"
STOCK_LIST_UNREADABLE = "종목표 파일을 읽을 수 없습니다"
NO_PUBLIC_BUILD = "아직 공개된 유사도 계산 결과가 없습니다(python -m research_desk peers build)"
OPENAI_KEY_MISSING = "OPENAI_API_KEY가 설정되지 않았습니다"

STOCK_NOT_FOUND = "종목을 찾을 수 없습니다."
NO_PEER_DATA = "이 종목은 유사 기업 자료가 없습니다."
NO_SEGMENT = "그 사업부문이 없습니다."

MATCH_LIMIT = 200            # the most rows the match functions give
EMBED_DIMS = 1536
EMBED_CONCURRENCY = 2        # this feature's own embedding slots in the process
EMBED_TIMEOUT_S = 15.0
QUERY_CACHE_SIZE = 256

SEED_COLUMNS = "stock_code, profile, is_holding, is_financial, info_quality, terms"
PEER_COLUMNS = "stock_code, one_line, is_holding, is_financial, info_quality, terms"
RESULT_COLUMNS = "stock_code, one_line"


@dataclass(frozen=True)
class BuildTables:
    """A public build's tables (spec §6.3, §6.5)."""
    company_quantiles: Optional[list]
    segment_quantiles: Optional[list]
    term_table: dict


# ── readiness helpers ────────────────────────────────────────────────────────

def connect() -> PeersStore:
    """Re-read ``.env``, then open the Supabase REST client. NotReady without the DB settings."""
    core_settings.load_env()
    url, key = core_settings.supabase_url(), core_settings.supabase_service_key()
    if not (url and key):
        raise NotReady(AREA, DB_NOT_CONFIGURED)
    return PeersStore(core_db.supabase_client(url, key))


def load_stock_list() -> StockList:
    """Re-read ``.env``, then load the stock list from ``KRX_CSV_PATH``. NotReady when it cannot be
    read; a version problem only logs a warning."""
    core_settings.load_env()
    path = core_settings.krx_csv_path()
    try:
        stocks = StockList.load(path)
    except StockListError as exc:
        # The answer carries a fixed sentence; the path and the cause go to the local log only.
        logger.warning("유사 기업 기능이 종목표를 읽지 못했습니다. 다음 요청 때 다시 시도합니다: %s", exc)
        raise NotReady(AREA, STOCK_LIST_UNREADABLE) from exc
    check = stocks.verify()
    if not check.ok:
        logger.warning("종목표 버전 정보 확인 결과 %s: %s. 유사 기업 기능은 경고만 남기고 그대로 동작합니다.",
                       check.reason, path)
    return stocks


def embedding_client() -> LLMClient:
    """A new AI client for one query embedding (closed right after the call)."""
    return LLMClient(openai_api_key=core_settings.openai_api_key(), timeout=EMBED_TIMEOUT_S)


# ── this feature's embedding slots ───────────────────────────────────────────

# One semaphore per running event loop, made on first use inside that loop: an asyncio.Semaphore
# cannot be shared across loops (the web server has one loop).
_slots: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = weakref.WeakKeyDictionary()
_slots_lock = threading.Lock()


def _slot_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    with _slots_lock:
        semaphore = _slots.get(loop)
        if semaphore is None:
            semaphore = _slots[loop] = asyncio.Semaphore(EMBED_CONCURRENCY)
        return semaphore


@asynccontextmanager
async def embed_slot() -> AsyncIterator[None]:
    """Hold one of this feature's query-embedding slots (``EMBED_CONCURRENCY`` in the process)."""
    async with _slot_semaphore():
        yield


# ── answer pieces ────────────────────────────────────────────────────────────

def _basis(build: dict) -> dict:
    return {"fiscal_year": build["fiscal_year"], "build_id": build["build_id"], "built_at": build.get("finished_at"),
            "companies_profiled": build.get("profiled"), "companies_eligible": build.get("eligible")}


def _price(snapshot: Optional[dict]) -> Optional[dict]:
    """Spec §12.1's ``price``: the prices window's snapshot without its ``market``; None without one."""
    if snapshot is None:
        return None
    return {key: value for key, value in snapshot.items() if key != "market"}


def _segment_brief(row: dict) -> dict:
    return {"no": row["seg_no"], "name": row["name"], "revenue_share_pct": row["revenue_share_pct"]}


def _segments_by_code(rows: list[dict]) -> dict[str, list]:
    grouped: dict[str, list] = {}
    for row in rows:
        grouped.setdefault(row["stock_code"], []).append(row)
    return {code: logic.ordered_segments(found) for code, found in grouped.items()}


class PeersService:
    """Peers for one web app process. Creating it reads nothing; first use prepares.

    ``llm_client`` makes the AI client of one query embedding (default ``embedding_client``) and
    ``synonyms`` loads the synonym table (default ``settings.synonyms``); tests pass stand-ins.
    """

    def __init__(self, llm_client: Optional[Callable[[], Any]] = None,
                 synonyms: Optional[Callable[[], logic.Synonyms]] = None) -> None:
        self._llm_client = llm_client
        self._load_synonyms = synonyms
        self._store: Optional[PeersStore] = None
        self._stocks: Optional[StockList] = None
        self._prepare_lock = threading.Lock()
        self._build_lock = threading.Lock()       # one read of a build's tables at a time
        self._tables: Optional[tuple[int, BuildTables]] = None
        self._synonyms: Optional[tuple[int, logic.Synonyms]] = None
        self._vectors: OrderedDict[tuple[str, str], list[float]] = OrderedDict()
        self._vectors_lock = threading.Lock()

    # ── readiness ────────────────────────────────────────────────────────────

    def store(self) -> PeersStore:
        """The peers tables. Prepares the DB now if needed; NotReady without DB settings."""
        store = self._store
        if store is None:
            with self._prepare_lock:
                store = self._store
                if store is None:
                    store = self._store = connect()
        return store

    def stock_list(self) -> StockList:
        """The stock list. Loads it now if needed; NotReady when it cannot be read."""
        stocks = self._stocks
        if stocks is None:
            with self._prepare_lock:
                stocks = self._stocks
                if stocks is None:
                    stocks = self._stocks = load_stock_list()
        return stocks

    def prepare(self) -> tuple[PeersStore, StockList]:
        """The DB first, then the stock list."""
        return self.store(), self.stock_list()

    # ── the public build ─────────────────────────────────────────────────────

    def latest_build(self, store: PeersStore) -> dict:
        """The latest public build, looked up now; NotReady without one (not remembered)."""
        build = store.latest_done_build()
        if build is None:
            raise NotReady(AREA, NO_PUBLIC_BUILD)
        return build

    def tables(self, store: PeersStore, build: dict) -> BuildTables:
        """The build's tables: read once per build id and kept. A build gone in between gives
        empty tables (nothing graded) that are not kept."""
        build_id = build["build_id"]
        with self._build_lock:
            if self._tables is not None and self._tables[0] == build_id:
                return self._tables[1]
            row = store.build_tables(build_id)
            tables = BuildTables(company_quantiles=(row or {}).get("company_quantiles"),
                                 segment_quantiles=(row or {}).get("segment_quantiles"),
                                 term_table=(row or {}).get("term_table") or {})
            if row is not None:
                self._tables = (build_id, tables)
            return tables

    def synonyms(self, build: dict) -> logic.Synonyms:
        """The synonym table for theme search: loaded once per build id."""
        build_id = build["build_id"]
        with self._build_lock:
            if self._synonyms is not None and self._synonyms[0] == build_id:
                return self._synonyms[1]
            table = (self._load_synonyms or peers_settings.synonyms)()
            recorded = build.get("synonyms_version")
            if recorded and recorded != table.fingerprint:
                logger.warning("빌드 %s는 지금과 다른 동의어표로 만들어졌습니다. 테마 검색의 용어 일치가 "
                               "어긋날 수 있으니 다음 빌드(peers build)로 맞추세요.", build_id)
            self._synonyms = (build_id, table)
            return table

    # ── GET /api/stocks/{code}/peers ─────────────────────────────────────────

    def peers(self, code: str, window: str, segment_no: Optional[int]) -> dict:
        """A company's peers list (see the module docstring for the order of the checks)."""
        store, stocks = self.prepare()
        build = self.latest_build(store)
        entry = stocks.lookup(code)
        if entry is None:
            raise HTTPException(404, STOCK_NOT_FOUND)
        fy, pv, model = build["fiscal_year"], build["profile_version"], build["embed_model"]
        seed = store.profile(fy, pv, code, SEED_COLUMNS)
        seed_vector = store.company_vector(fy, pv, model, code) if seed is not None else None
        if seed_vector is None:
            raise HTTPException(404, NO_PEER_DATA)
        seed_segments = logic.ordered_segments(store.segments_of(fy, pv, [code]))
        if segment_no is None:
            chosen = seed_segments[0] if seed_segments else None
        else:
            chosen = next((s for s in seed_segments if s["seg_no"] == segment_no), None)
            if chosen is None:
                raise HTTPException(422, NO_SEGMENT)
        tables = self.tables(store, build)

        def eligible(other: str) -> bool:
            return other != code and stocks.lookup(other) is not None

        company_rows = logic.top_matches(store.match_company_profiles(fy, pv, model, seed_vector, MATCH_LIMIT),
                                         eligible, logic.COMPANY_TOP)
        segment_rows: list[dict] = []
        if chosen is not None:
            segment_vector = store.segment_vector(fy, pv, model, code, chosen["seg_no"])
            if segment_vector is not None:
                matches = store.match_company_segments(fy, pv, model, segment_vector, MATCH_LIMIT)
                segment_rows = logic.top_matches(logic.best_segments(matches), eligible, logic.SEGMENT_TOP)
        candidates = list(dict.fromkeys([r["stock_code"] for r in company_rows + segment_rows]))
        profiles = {row["stock_code"]: row for row in store.profiles_of(fy, pv, candidates, PEER_COLUMNS)}
        seed_terms = seed.get("terms") or []
        ranked = logic.merge_peers(
            [r for r in company_rows if r["stock_code"] in profiles],
            [r for r in segment_rows if r["stock_code"] in profiles],
            tables.company_quantiles, tables.segment_quantiles,
            shared_counts={other: logic.shared_count(seed_terms, row.get("terms"), tables.term_table)
                           for other, row in profiles.items()})
        peer_codes = [peer["code"] for peer in ranked]
        segments = _segments_by_code(store.segments_of(fy, pv, peer_codes))

        codes = [code] + peer_codes
        counts = coverage.report_counts(codes, days=logic.COVERAGE_DAYS)
        snapshots = prices.snapshots(codes)
        seed_price = _price(snapshots.get(code))
        seed_excess = seed_price["excess"].get(window) if seed_price and seed_price.get("excess") else None
        seed_as_of = seed_price.get("as_of") if seed_price else None

        rows = []
        for rank, peer in enumerate(ranked, 1):
            other = peer["code"]
            listed = stocks.lookup(other)
            profile_row = profiles[other]
            own_segments = segments.get(other, [])
            price = _price(snapshots.get(other))
            reaction = logic.reaction(seed_excess, seed_as_of, price, window)
            counted = counts.get(other)
            candidate, reasons = logic.candidacy(label=(counted or {}).get("label"), reaction=reaction, price=price,
                                                 is_holding=profile_row.get("is_holding"), tier=peer["tier"])
            rows.append({
                "rank": rank, "code": other, "name": listed.name, "market": listed.market,
                "sector_minor": listed.sector_minor, "one_line": profile_row.get("one_line") or "",
                "tier": peer["tier"], "company_match": peer["company_match"],
                "segment_match": self._segment_match(peer["segment_match"], own_segments),
                "segments": [_segment_brief(s) for s in own_segments],
                "shared_terms": logic.shared_terms(seed_terms, profile_row.get("terms"), tables.term_table),
                "same_industry": logic.same_industry(entry.sector_minor, listed.sector_minor),
                "is_holding": profile_row.get("is_holding"), "is_financial": profile_row.get("is_financial"),
                "info_quality": profile_row.get("info_quality"), "coverage": counted, "price": price,
                "reaction": reaction, "candidate": candidate, "not_candidate_reasons": reasons,
            })

        profile = seed.get("profile") or {}
        return {
            "seed": {
                "code": code, "name": entry.name, "market": entry.market, "sector_minor": entry.sector_minor,
                "profile": {"niche_industry": profile.get("niche_industry", ""), "summary": profile.get("summary", ""),
                            "keywords": list(profile.get("keywords") or []),
                            "segments": [{**_segment_brief(s), "products": list(s.get("products") or [])}
                                         for s in seed_segments]},
                "is_holding": seed.get("is_holding"), "is_financial": seed.get("is_financial"),
                "info_quality": seed.get("info_quality"), "coverage": counts.get(code), "price": seed_price,
            },
            "basis": _basis(build),
            "window": window,
            "segment": _segment_brief(chosen) if chosen is not None else None,
            "price_as_of": seed_as_of,
            "seed_excess_pct": seed_excess,
            "judgeable": logic.judgeable(seed_excess),
            "peers": rows,
        }

    @staticmethod
    def _segment_match(match: Optional[dict], own_segments: list) -> Optional[dict]:
        if match is None:
            return None
        found = next((s for s in own_segments if s["seg_no"] == match["seg_no"]), None)
        return {"segment": found["name"] if found else None,
                "revenue_share_pct": found["revenue_share_pct"] if found else None,
                "similarity": match["similarity"], "percentile": match["percentile"], "tier": match["tier"]}

    # ── POST /api/peers/search ───────────────────────────────────────────────

    async def search(self, query: str, window: str, limit: int) -> dict:
        """Theme search (see the module docstring)."""
        store, stocks, build, tables, synonyms = await asyncio.to_thread(self._search_basis)
        vector = await self.query_vector(build["embed_model"], logic.normalize_query(query, synonyms))
        return await asyncio.to_thread(self._search_answer, store, stocks, build, tables, synonyms, query, vector,
                                       window, limit)

    def _search_basis(self):
        store, stocks = self.prepare()
        build = self.latest_build(store)
        return store, stocks, build, self.tables(store, build), self.synonyms(build)

    async def query_vector(self, model: str, text: str) -> list[float]:
        """The unit embedding of ``text`` with ``model``: from the cache, else one call in this
        feature's slot within ``EMBED_TIMEOUT_S`` seconds (NotReady without OPENAI_API_KEY)."""
        key = (model, text)
        with self._vectors_lock:
            cached = self._vectors.get(key)
            if cached is not None:
                self._vectors.move_to_end(key)
                return cached
        core_settings.load_env()
        if not core_settings.openai_api_key():
            raise NotReady(AREA, OPENAI_KEY_MISSING)
        async with embed_slot():
            client = (self._llm_client or embedding_client)()
            try:
                async with asyncio.timeout(EMBED_TIMEOUT_S):
                    result = await client.embed(model=model, texts=[text], dimensions=EMBED_DIMS)
            finally:
                await client.close()
        if len(result.vectors) != 1 or len(result.vectors[0]) != EMBED_DIMS:
            raise RuntimeError(f"질의 임베딩이 {EMBED_DIMS}차원 하나가 아닙니다")
        vector = logic.normalize(result.vectors[0])
        with self._vectors_lock:
            self._vectors[key] = vector
            self._vectors.move_to_end(key)
            while len(self._vectors) > QUERY_CACHE_SIZE:
                self._vectors.popitem(last=False)
        return vector

    def _search_answer(self, store: PeersStore, stocks: StockList, build: dict, tables: BuildTables,
                       synonyms: logic.Synonyms, query: str, vector: list[float], window: str, limit: int) -> dict:
        fy, pv, model = build["fiscal_year"], build["profile_version"], build["embed_model"]

        def eligible(code: str) -> bool:
            return stocks.lookup(code) is not None

        companies = logic.top_matches(store.match_company_profiles(fy, pv, model, vector, MATCH_LIMIT),
                                      eligible, logic.COMPANY_TOP)
        segment_rows = logic.top_matches(
            logic.best_segments(store.match_company_segments(fy, pv, model, vector, MATCH_LIMIT)),
            eligible, logic.SEGMENT_TOP)
        terms = logic.query_terms(query, synonyms)
        keys = [key for key, _ in terms]
        matched: dict[str, list[str]] = {}
        for row in store.profiles_with_terms(fy, pv, keys):
            held = set(row.get("terms") or [])
            hits = [key for key in keys if key in held]
            if hits and eligible(row["stock_code"]):
                matched[row["stock_code"]] = hits
        fused = logic.rrf([[r["stock_code"] for r in companies], [r["stock_code"] for r in segment_rows],
                           logic.term_ranking(matched, tables.term_table)])
        top = [code for code, _ in fused[:limit]]
        profiles = {row["stock_code"]: row for row in store.profiles_of(fy, pv, top, RESULT_COLUMNS)}
        top = [code for code in top if code in profiles]
        best = {row["stock_code"]: row for row in segment_rows if row["stock_code"] in profiles}
        names = {(s["stock_code"], s["seg_no"]): s for s in store.segments_of(fy, pv, [c for c in top if c in best])}
        counts = coverage.report_counts(top, days=logic.COVERAGE_DAYS) if top else {}
        snapshots = prices.snapshots(top) if top else {}
        written = dict(terms)

        results = []
        for rank, code in enumerate(top, 1):
            listed = stocks.lookup(code)
            segment = names.get((code, best[code]["seg_no"])) if code in best else None
            results.append({
                "rank": rank, "code": code, "name": listed.name, "market": listed.market,
                "sector_minor": listed.sector_minor, "one_line": profiles[code].get("one_line") or "",
                "matched_terms": [(tables.term_table.get(key) or {}).get("display") or written[key]
                                  for key in matched.get(code, [])],
                "segment_match": ({"segment": segment["name"], "revenue_share_pct": segment["revenue_share_pct"]}
                                  if segment is not None else None),
                "coverage": counts.get(code), "price": _price(snapshots.get(code)),
            })
        dates = [r["price"]["as_of"] for r in results if r["price"] and r["price"].get("as_of")]
        return {"query": query, "basis": _basis(build), "window": window,
                "price_as_of": max(dates) if dates else None, "results": results}


# ── the process-wide service ─────────────────────────────────────────────────

_service: Optional[PeersService] = None
_service_lock = threading.Lock()


def get_service() -> PeersService:
    """The process-wide service, created on first use (creating it reads nothing).

    The router depends on this, so every request shares the prepared DB client and stock list, the
    build tables, the query cache and the embedding slots. Tests override it
    (``app.dependency_overrides[get_service]``) or replace ``_service``.
    """
    global _service
    with _service_lock:
        if _service is None:
            _service = PeersService()
        return _service
