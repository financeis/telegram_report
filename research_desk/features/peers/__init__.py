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

Importing this window loads neither FastAPI, the reports window, pymongo nor an AI SDK: the
commands load what they need when they run.
"""
from .jobs import register as register_jobs
