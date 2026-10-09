"""DB reads and writes of the peers tables and the two match functions (migration 008).

This feature owns ``company_profiles``, ``company_segments``, ``company_embeddings``,
``segment_embeddings`` and ``peer_builds`` and the functions ``match_company_profiles`` /
``match_company_segments``. ``PeersStore`` wraps a supabase-py client (service key):

- every list read pages ``PAGE`` (1000) rows at a time in a fixed order until a short page
  (Supabase REST answers at most 1000 rows); vector reads page ``VECTOR_PAGE`` rows, since a
  1536-number vector comes back as text and makes rows large. Vectors come back as lists.
- a profile is replaced by deleting the old row first: its segments and embeddings go with it
  (ON DELETE CASCADE), so "embed what has no embedding" sees the new profile. An ``ok`` profile
  is written without a status, then its segments, then marked ``ok``: a run cut off in between
  leaves a row that is neither reused nor served.
- the public build rewrites ``terms`` with an UPDATE of that column (never a partial upsert).
- embeddings are written per model (upsert on the full key, 100 rows a request); another
  model's rows are never touched.
- the match functions take the query vector as a list; they return every row they find (the
  seed itself included; for segments one row per company, its nearest segment) — callers filter.
- the web side reads the latest public build without its tables (one row, every request) and a
  build's tables by id (the caller keeps them per build id: a public build never changes); reads
  for a list of codes go ``CODES_CHUNK`` codes a request, each paged; the profiles holding a
  query key are found with an array overlap on ``terms`` whose keys are each quoted
  (``array_literal``), so a comma or a brace in a key stays inside it.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Optional, Sequence

PAGE = 1000
VECTOR_PAGE = 200
WRITE_CHUNK = 100
CODES_CHUNK = 100     # codes per read for a list of codes (keeps the request address short)

PROFILES = "company_profiles"
SEGMENTS = "company_segments"
COMPANY_EMBEDDINGS = "company_embeddings"
SEGMENT_EMBEDDINGS = "segment_embeddings"
BUILDS = "peer_builds"

PROFILE_KEY = ("fiscal_year", "profile_version", "stock_code")
BUILD_LIST_COLUMNS = ("build_id, fiscal_year, profile_version, embed_model, embed_dims, parser_version, "
                      "synonyms_version, stock_list_version, status, eligible, profiled, failed, "
                      "started_at, heartbeat_at, finished_at, message")
BUILD_TABLE_COLUMNS = "company_quantiles, segment_quantiles, term_table"
MINIMAL = "minimal"


def parse_vector(value: Any) -> list[float]:
    """A pgvector value as REST returns it (``"[0.1,0.2,…]"``) or a list, as floats."""
    if isinstance(value, str):
        value = json.loads(value)
    return [float(x) for x in value]


def array_literal(values: Iterable[str]) -> str:
    """A PostgreSQL text array literal with every value double-quoted (backslashes and quotes
    escaped): ``{"a,b","c"}``. PostgREST hands it to PostgreSQL as it is."""
    quoted = ('"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"' for value in values)
    return "{" + ",".join(quoted) + "}"


def _chunks(items: Sequence, size: int) -> Iterable[Sequence]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


class PeersStore:
    """The peers tables over a supabase-py client."""

    def __init__(self, client: Any) -> None:
        self._sb = client

    def _table(self, name: str):
        return self._sb.table(name)

    @staticmethod
    def _paged(chain, page: int = PAGE) -> list[dict]:
        rows: list[dict] = []
        offset = 0
        while True:
            batch = chain.range(offset, offset + page - 1).execute().data or []
            rows.extend(batch)
            if len(batch) < page:
                return rows
            offset += page

    def _key(self, chain, fiscal_year: int, profile_version: str):
        return chain.eq("fiscal_year", fiscal_year).eq("profile_version", profile_version)

    # ── builds ───────────────────────────────────────────────────────────────

    def start_build(self, row: dict) -> dict:
        """Insert a build row; returns it with its ``build_id``."""
        return self._table(BUILDS).insert(row).execute().data[0]

    def update_build(self, build_id: int, fields: dict, *, only_running: bool = False) -> None:
        """Set ``fields`` on a build; with ``only_running``, only while it is ``running``."""
        chain = self._table(BUILDS).update(fields, returning=MINIMAL).eq("build_id", build_id)
        if only_running:
            chain = chain.eq("status", "running")
        chain.execute()

    def fail_build(self, build_id: int, message: str, finished_at: str) -> None:
        """Close a ``running`` build as ``failed``; a build already closed stays as it is."""
        self.update_build(build_id, {"status": "failed", "message": message, "finished_at": finished_at,
                                     "heartbeat_at": finished_at}, only_running=True)

    def running_builds(self) -> list[dict]:
        return (self._table(BUILDS).select(BUILD_LIST_COLUMNS).eq("status", "running")
                .order("build_id").execute().data or [])

    def done_builds(self) -> list[dict]:
        """Public builds, latest first (finished_at, then build_id)."""
        return (self._table(BUILDS).select(BUILD_LIST_COLUMNS).eq("status", "done")
                .order("finished_at", desc=True, nullsfirst=False).order("build_id", desc=True)
                .execute().data or [])

    def latest_done_build(self) -> Optional[dict]:
        """The latest public build (finished_at, then build_id) without its tables, or None: one
        small read, made for every web request."""
        rows = (self._table(BUILDS).select(BUILD_LIST_COLUMNS).eq("status", "done")
                .order("finished_at", desc=True, nullsfirst=False).order("build_id", desc=True)
                .limit(1).execute().data or [])
        return rows[0] if rows else None

    def build_tables(self, build_id: int) -> Optional[dict]:
        """``{"company_quantiles", "segment_quantiles", "term_table"}`` of one build (they can
        take megabytes), or None when there is no such build."""
        rows = self._table(BUILDS).select(BUILD_TABLE_COLUMNS).eq("build_id", build_id).execute().data or []
        return rows[0] if rows else None

    def recent_builds(self, limit: int = 10) -> list[dict]:
        """The latest ``limit`` builds of any status, newest first (no tables in the rows)."""
        return (self._table(BUILDS).select(BUILD_LIST_COLUMNS).order("build_id", desc=True)
                .limit(limit).execute().data or [])

    def builds(self) -> list[dict]:
        """Every build (no tables in the rows), in id order."""
        return self._paged(self._table(BUILDS).select(BUILD_LIST_COLUMNS).order("build_id"))

    def delete_builds(self, build_ids: Sequence[int]) -> None:
        if build_ids:
            self._table(BUILDS).delete(returning=MINIMAL).in_("build_id", list(build_ids)).execute()

    # ── profiles and segments ────────────────────────────────────────────────

    def profiles(self, fiscal_year: int, profile_version: str, columns: str = "*", *,
                 status: Optional[str] = None) -> list[dict]:
        """Profiles of one fiscal year and profile version (optionally one status), by code."""
        chain = self._key(self._table(PROFILES).select(columns), fiscal_year, profile_version)
        if status is not None:
            chain = chain.eq("status", status)
        return self._paged(chain.order("stock_code"))

    def all_profiles(self, columns: str) -> list[dict]:
        """``columns`` of every profile row, in key order."""
        chain = (self._table(PROFILES).select(columns).order("fiscal_year").order("profile_version")
                 .order("stock_code"))
        return self._paged(chain)

    def segments(self, fiscal_year: int, profile_version: str) -> list[dict]:
        """Segments of one fiscal year and profile version, by code and number."""
        chain = self._key(self._table(SEGMENTS).select("*"), fiscal_year, profile_version)
        return self._paged(chain.order("stock_code").order("seg_no"))

    def profile(self, fiscal_year: int, profile_version: str, stock_code: str,
                columns: str = "*") -> Optional[dict]:
        """``columns`` of one company's ``ok`` profile, or None."""
        rows = (self._key(self._table(PROFILES).select(columns), fiscal_year, profile_version)
                .eq("stock_code", stock_code).eq("status", "ok").execute().data or [])
        return rows[0] if rows else None

    @staticmethod
    def _code_parts(codes: Iterable[str]) -> Iterable[list[str]]:
        unique = sorted(dict.fromkeys(codes))
        for start in range(0, len(unique), CODES_CHUNK):
            yield unique[start:start + CODES_CHUNK]

    def profiles_of(self, fiscal_year: int, profile_version: str, codes: Iterable[str],
                    columns: str) -> list[dict]:
        """``columns`` of the ``ok`` profiles of ``codes`` (each once), by code."""
        rows: list[dict] = []
        for part in self._code_parts(codes):
            chain = (self._key(self._table(PROFILES).select(columns), fiscal_year, profile_version)
                     .eq("status", "ok").in_("stock_code", part))
            rows += self._paged(chain.order("stock_code"))
        return rows

    def segments_of(self, fiscal_year: int, profile_version: str, codes: Iterable[str]) -> list[dict]:
        """The segments of ``codes`` (each once), by code and number."""
        rows: list[dict] = []
        for part in self._code_parts(codes):
            chain = self._key(self._table(SEGMENTS).select("*"), fiscal_year, profile_version).in_("stock_code", part)
            rows += self._paged(chain.order("stock_code").order("seg_no"))
        return rows

    def profiles_with_terms(self, fiscal_year: int, profile_version: str, keys: Sequence[str],
                            columns: str = "stock_code, terms") -> list[dict]:
        """``columns`` of the ``ok`` profiles whose ``terms`` hold at least one of ``keys`` exactly
        (an array overlap), by code; none without keys."""
        if not keys:
            return []
        chain = (self._key(self._table(PROFILES).select(columns), fiscal_year, profile_version)
                 .eq("status", "ok").ov("terms", array_literal(keys)))
        return self._paged(chain.order("stock_code"))

    def replace_profile(self, row: dict, segments: Sequence[dict] = ()) -> None:
        """Write one company's profile in place of the row under the same key (see the module
        docstring for the order)."""
        key = {name: row[name] for name in PROFILE_KEY}
        chain = self._table(PROFILES).delete(returning=MINIMAL)
        for name, value in key.items():
            chain = chain.eq(name, value)
        chain.execute()
        status = row.get("status")
        if status != "ok":
            self._table(PROFILES).insert(row, returning=MINIMAL).execute()
            return
        self._table(PROFILES).insert({**row, "status": None}, returning=MINIMAL).execute()
        if segments:
            self._table(SEGMENTS).insert(list(segments), returning=MINIMAL).execute()
        chain = self._table(PROFILES).update({"status": "ok"}, returning=MINIMAL)
        for name, value in key.items():
            chain = chain.eq(name, value)
        chain.execute()

    def set_terms(self, fiscal_year: int, profile_version: str, stock_code: str, terms: list[str]) -> None:
        """Rewrite one profile's ``terms`` (an UPDATE of that column only)."""
        (self._key(self._table(PROFILES).update({"terms": terms}, returning=MINIMAL), fiscal_year,
                   profile_version)
         .eq("stock_code", stock_code).execute())

    def profile_keys(self) -> set[tuple[int, str]]:
        """Every (fiscal_year, profile_version) that has profile rows."""
        rows = self.all_profiles("fiscal_year, profile_version")
        return {(r["fiscal_year"], r["profile_version"]) for r in rows}

    def delete_profiles(self, fiscal_year: int, profile_version: str) -> None:
        """Every profile of the key, with its segments and embeddings (cascade)."""
        self._key(self._table(PROFILES).delete(returning=MINIMAL), fiscal_year, profile_version).execute()

    # ── embeddings ───────────────────────────────────────────────────────────

    def _embedding_rows(self, table: str, fiscal_year: int, profile_version: str, model: str,
                        columns: str, order: Sequence[str], page: int = PAGE) -> list[dict]:
        chain = self._key(self._table(table).select(columns), fiscal_year, profile_version).eq("embed_model", model)
        for column in order:
            chain = chain.order(column)
        return self._paged(chain, page)

    def company_embedding_codes(self, fiscal_year: int, profile_version: str, model: str) -> set[str]:
        rows = self._embedding_rows(COMPANY_EMBEDDINGS, fiscal_year, profile_version, model,
                                    "stock_code", ["stock_code"])
        return {r["stock_code"] for r in rows}

    def segment_embedding_keys(self, fiscal_year: int, profile_version: str, model: str) -> set[tuple[str, int]]:
        rows = self._embedding_rows(SEGMENT_EMBEDDINGS, fiscal_year, profile_version, model,
                                    "stock_code, seg_no", ["stock_code", "seg_no"])
        return {(r["stock_code"], r["seg_no"]) for r in rows}

    def company_vectors(self, fiscal_year: int, profile_version: str, model: str) -> list[tuple[str, list[float]]]:
        """``(stock_code, vector)`` of every company embedding of the model, by code."""
        rows = self._embedding_rows(COMPANY_EMBEDDINGS, fiscal_year, profile_version, model,
                                    "stock_code, embedding", ["stock_code"], VECTOR_PAGE)
        return [(r["stock_code"], parse_vector(r["embedding"])) for r in rows]

    def segment_vectors(self, fiscal_year: int, profile_version: str,
                        model: str) -> list[tuple[str, int, list[float]]]:
        """``(stock_code, seg_no, vector)`` of every segment embedding of the model."""
        rows = self._embedding_rows(SEGMENT_EMBEDDINGS, fiscal_year, profile_version, model,
                                    "stock_code, seg_no, embedding", ["stock_code", "seg_no"], VECTOR_PAGE)
        return [(r["stock_code"], r["seg_no"], parse_vector(r["embedding"])) for r in rows]

    def company_vector(self, fiscal_year: int, profile_version: str, model: str,
                       stock_code: str) -> Optional[list[float]]:
        """One company's embedding of the model, or None."""
        rows = (self._key(self._table(COMPANY_EMBEDDINGS).select("embedding"), fiscal_year, profile_version)
                .eq("embed_model", model).eq("stock_code", stock_code).execute().data or [])
        return parse_vector(rows[0]["embedding"]) if rows else None

    def segment_vector(self, fiscal_year: int, profile_version: str, model: str, stock_code: str,
                       seg_no: int) -> Optional[list[float]]:
        """One segment's embedding of the model, or None."""
        rows = (self._key(self._table(SEGMENT_EMBEDDINGS).select("embedding"), fiscal_year, profile_version)
                .eq("embed_model", model).eq("stock_code", stock_code).eq("seg_no", seg_no)
                .execute().data or [])
        return parse_vector(rows[0]["embedding"]) if rows else None

    def save_company_embeddings(self, fiscal_year: int, profile_version: str, model: str,
                                items: Sequence[tuple[str, Sequence[float]]]) -> None:
        rows = [{"fiscal_year": fiscal_year, "profile_version": profile_version, "stock_code": code,
                 "embed_model": model, "embedding": list(vector)} for code, vector in items]
        for chunk in _chunks(rows, WRITE_CHUNK):
            (self._table(COMPANY_EMBEDDINGS)
             .upsert(list(chunk), on_conflict="fiscal_year,profile_version,stock_code,embed_model",
                     returning=MINIMAL).execute())

    def save_segment_embeddings(self, fiscal_year: int, profile_version: str, model: str,
                                items: Sequence[tuple[str, int, Sequence[float]]]) -> None:
        rows = [{"fiscal_year": fiscal_year, "profile_version": profile_version, "stock_code": code,
                 "seg_no": seg_no, "embed_model": model, "embedding": list(vector)}
                for code, seg_no, vector in items]
        for chunk in _chunks(rows, WRITE_CHUNK):
            (self._table(SEGMENT_EMBEDDINGS)
             .upsert(list(chunk), on_conflict="fiscal_year,profile_version,stock_code,seg_no,embed_model",
                     returning=MINIMAL).execute())

    def embedding_keys(self) -> set[tuple[int, str, str]]:
        """Every (fiscal_year, profile_version, embed_model) that has company or segment embeddings."""
        keys: set[tuple[int, str, str]] = set()
        for table in (COMPANY_EMBEDDINGS, SEGMENT_EMBEDDINGS):
            chain = (self._table(table).select("fiscal_year, profile_version, embed_model")
                     .order("fiscal_year").order("profile_version").order("embed_model")
                     .order("stock_code"))
            if table == SEGMENT_EMBEDDINGS:
                chain = chain.order("seg_no")
            keys |= {(r["fiscal_year"], r["profile_version"], r["embed_model"]) for r in self._paged(chain)}
        return keys

    def delete_embeddings(self, fiscal_year: int, profile_version: str, model: str) -> None:
        """One model's company and segment embeddings of the key."""
        for table in (COMPANY_EMBEDDINGS, SEGMENT_EMBEDDINGS):
            (self._key(self._table(table).delete(returning=MINIMAL), fiscal_year, profile_version)
             .eq("embed_model", model).execute())

    # ── match functions ──────────────────────────────────────────────────────

    def _match(self, function: str, fiscal_year: int, profile_version: str, model: str,
               query: Sequence[float], limit: Optional[int]) -> list[dict]:
        params = {"p_fiscal_year": fiscal_year, "p_profile_version": profile_version,
                  "p_embed_model": model, "p_query": [float(x) for x in query], "p_limit": limit}
        return self._sb.rpc(function, params).execute().data or []

    def match_company_profiles(self, fiscal_year: int, profile_version: str, model: str,
                               query: Sequence[float], limit: Optional[int] = 200) -> list[dict]:
        """``[{"stock_code", "similarity"}]``: the model's company embeddings of ``ok`` profiles,
        nearest first (ties by code), at most ``limit`` (the function caps it at 200)."""
        return [{"stock_code": r["stock_code"], "similarity": float(r["similarity"])}
                for r in self._match("match_company_profiles", fiscal_year, profile_version, model,
                                     query, limit)]

    def match_company_segments(self, fiscal_year: int, profile_version: str, model: str,
                               query: Sequence[float], limit: Optional[int] = 200) -> list[dict]:
        """``[{"stock_code", "seg_no", "similarity"}]``: one row per company (its nearest segment), nearest first."""
        return [{"stock_code": r["stock_code"], "seg_no": int(r["seg_no"]), "similarity": float(r["similarity"])}
                for r in self._match("match_company_segments", fiscal_year, profile_version, model,
                                     query, limit)]
