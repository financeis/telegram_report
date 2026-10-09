"""Peers (유사 기업): companies with a similar business, from their annual business reports.

A yearly batch builds an AI business profile per listed company from its business report
(MongoDB ``FS.A001_v2``), embeds the profile and its segments, and publishes a build: percentile
tables and a term table that the web side grades similarities with. This feature owns the tables
``company_profiles``, ``company_segments``, ``company_embeddings``, ``segment_embeddings``,
``peer_builds`` and the functions ``match_company_profiles`` / ``match_company_segments``.

Public names:

- ``register_jobs(subparsers)``: adds ``peers build [--fiscal-year N] [--codes …] [--limit n]
  [--pilot]`` and ``peers inspect`` (exit codes: 0 ok, 1 runtime failure / incomplete build /
  refused, 2 usage, 4 not ready).
- ``web_router()``: the FastAPI router with ``GET /api/stocks/{code}/peers`` and
  ``POST /api/peers/search`` (body model ``PeerSearchBody``), imported when called (spec §13).
  Its readiness failures are ``NotReady("유사 기업", <reason>)`` with the reasons
  ``DB 접속 설정(SUPABASE_URL, SUPABASE_SERVICE_KEY)이 없습니다``, ``종목표 파일을 읽을 수 없습니다``,
  ``아직 공개된 유사도 계산 결과가 없습니다(python -m research_desk peers build)`` and, on the search
  address only, ``OPENAI_API_KEY가 설정되지 않았습니다``; the reports, coverage and prices windows'
  NotReady passes through unchanged. The process-wide service is ``service.get_service()``.

Importing this window loads neither FastAPI, the router or the service, the reports, coverage or
prices windows, pymongo nor an AI SDK: the commands and ``web_router()`` load what they need when
they run.
"""
from .jobs import register as register_jobs


def web_router():
    """The FastAPI router of the two web addresses (loads FastAPI and the service now)."""
    from .router import router

    return router
