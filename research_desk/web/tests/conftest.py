"""Safety net for every web test, and the shared fixtures.

- ``clean_env`` (autouse):
  - every setting a feature could read is deleted; a test that needs one sets it itself;
  - the current folder is a new temp folder, so a relative default (``./reports``,
    ``docs/stock_data/KRX_stocks_data.csv``) never reaches the real files;
  - ``Path.home()`` is a new temp folder, so even a companies service made without a favorites
    path cannot reach ``~/.review_viewer/favorites.json``;
  - ``core.db.supabase_client`` refuses: no real Supabase client, ever (a test that needs a DB
    hands out a stand-in);
  - the features' process-wide state starts fresh and is put back afterwards: the service of
    companies, reports, coverage, review, prices (read by peers and freshness through its
    window), peers and freshness, and analysis' Supabase client and reports in flight.
- ``dist``: a temp screen folder (``index.html``, ``assets/app.js``).
- ``app``: the assembled app on ``dist``; its dependency overrides are cleared afterwards.
- ``stock_csv``: a small stock list holding 016360, with its version file, at ``KRX_CSV_PATH``.
- ``favorites_path`` / ``companies``: the real companies service on a temp favorites file,
  standing in for the app's own (the real favorites file is never used).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from research_desk.core import db as core_db
from research_desk.domain.stocks import write_version
from research_desk.features.analysis import service as analysis_service
from research_desk.features.companies import service as companies_service
from research_desk.features.companies.service import CompaniesService
from research_desk.features.coverage import service as coverage_service
from research_desk.features.freshness import service as freshness_service
from research_desk.features.peers import service as peers_service
from research_desk.features.prices import service as prices_service
from research_desk.features.reports import service as reports_service
from research_desk.features.review import service as review_service
from research_desk.web.app import create_app

from .fakes import write_screen_files, write_stock_csv

ENV = ('SUPABASE_URL', 'SUPABASE_SERVICE_KEY', 'SUPABASE_DB_URL', 'STORAGE_BASE_DIR', 'KRX_CSV_PATH',
       'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'LLM_MODEL_PHASE2', 'OPENAI_MODEL_PHASE2',
       'PHASE2_SUMMARY_VERSION', 'PHASE2_PER_REPORT_TIMEOUT_S', 'PHASE2_MAX_INPUT_TOKENS',
       'CODEX_BIN', 'ANTHROPIC_EFFORT', 'CODEX_REASONING_EFFORT', 'LANGSMITH_TRACING',
       'LANGSMITH_API_KEY',
       # prices
       'KIS_APP_KEY', 'KIS_APP_SECRET', 'KIS_BASE_URL', 'PRICES_MAX_CALLS_PER_SEC',
       # peers
       'LLM_MODEL_PEERS', 'OPENAI_MODEL_PEERS', 'LLM_MODEL_PEERS_ESCALATION',
       'OPENAI_MODEL_PEERS_ESCALATION', 'PEERS_PROFILE_VERSION', 'PEERS_EMBED_MODEL',
       'PEERS_FISCAL_YEAR', 'PEERS_MAX_CONCURRENT_LLM', 'PEERS_PER_COMPANY_TIMEOUT_S',
       'DART_MONGO_URL', 'DART_MONGO_DB', 'DART_MONGO_COLLECTION')

FEATURE_SERVICES = (companies_service, reports_service, coverage_service, review_service, prices_service,
                    peers_service, freshness_service)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path_factory):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path_factory.mktemp('cwd'))
    home = tmp_path_factory.mktemp('home')
    monkeypatch.setenv('USERPROFILE', str(home))   # Windows
    monkeypatch.setenv('HOME', str(home))          # elsewhere
    assert Path.home() == home

    def refuse(url, key):
        raise AssertionError('no real Supabase client in the web tests')

    monkeypatch.setattr(core_db, 'supabase_client', refuse)
    for module in FEATURE_SERVICES:
        monkeypatch.setattr(module, '_service', None)
    monkeypatch.setattr(analysis_service, '_supabase_client', None)
    monkeypatch.setattr(analysis_service, '_analyzing', set())


@pytest.fixture
def dist(tmp_path) -> Path:
    return write_screen_files(tmp_path / 'dist')


@pytest.fixture
def app(dist):
    app = create_app(dist=dist)
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def stock_csv(tmp_path, monkeypatch) -> Path:
    path = write_stock_csv(tmp_path / 'stocks' / 'krx.csv')
    write_version(path, '2026-05-08')
    monkeypatch.setenv('KRX_CSV_PATH', str(path))
    return path


@pytest.fixture
def favorites_path(tmp_path) -> Path:
    return tmp_path / 'favorites' / 'favorites.json'


@pytest.fixture
def companies(app, favorites_path) -> CompaniesService:
    service = CompaniesService(favorites_path=favorites_path)
    app.dependency_overrides[companies_service.get_service] = lambda: service
    return service
