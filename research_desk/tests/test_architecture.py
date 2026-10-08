"""구조 규칙 검사 (spec §3, R1~R11).

첫 테스트가 진짜 검사다: research_desk/ 아래 모든 파이썬 파일을 실행하지 않고 읽어서
칸 사이 경계(import, 외부 도구, 옛 코드, 표 주인)를 확인한다. 규칙과 판정 방법은
같은 폴더의 architecture_rules.py에 있다.

나머지 테스트는 검사기 자체를 확인한다. 임시 폴더에 가짜 research_desk 패키지를 만들고
규칙마다 위반을 넣어 잡히는지, 위반이 없는 트리는 통과하는지 본다.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from research_desk.tests.architecture_rules import (
    check_tree,
    format_violations,
    resolve_relative,
)

PACKAGE_DIR = Path(__file__).resolve().parents[1]


def test_research_desk_follows_architecture_rules():
    violations = check_tree(PACKAGE_DIR)
    if violations:
        pytest.fail(format_violations(violations), pytrace=False)


# ── 도우미 ────────────────────────────────────────────────────────────────────


def make_tree(tmp_path: Path, files: dict[str, str]) -> Path:
    """tmp_path/research_desk 아래에 가짜 패키지를 만든다.

    키는 research_desk 기준 경로, 값은 파일 내용이다(들여쓰기와 맨 앞 줄바꿈은 지운다).
    파일 내용의 첫 줄이 1번 줄이다. __init__.py가 없는 폴더에는 빈 __init__.py를 채운다.
    """
    root = tmp_path / "research_desk"
    root.mkdir()
    for rel, source in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source).lstrip("\n"), encoding="utf-8")
    for folder in [root, *(p for p in root.rglob("*") if p.is_dir())]:
        init = folder / "__init__.py"
        if not init.exists():
            init.write_text("", encoding="utf-8")
    return root


def hits(violations, rule=None):
    return {(v.path, v.line, v.rule) for v in violations if rule is None or v.rule == rule}


def at(path, *lines, rule):
    return {(f"research_desk/{path}", line, rule) for line in lines}


# ── 위반이 없는 트리 ──────────────────────────────────────────────────────────

CLEAN_TREE = {
    "__init__.py": '"""Research Desk."""\n',
    "__main__.py": """
        import sys

        from research_desk.cli import main

        sys.exit(main())
    """,
    "cli.py": """
        from research_desk.collector.cli import register as register_collect
        from research_desk.core.settings import load_env
        from research_desk.domain.stocks import write_version
        from research_desk.features.analysis.jobs import register as register_analysis_jobs
        from research_desk.tagger.cli import register as register_tag
        from research_desk.web.server import register as register_web


        def main(argv=None):
            load_env()
            return 0
    """,
    "conftest.py": """
        import pytest

        from research_desk.core import settings


        @pytest.fixture(autouse=True)
        def _no_env(monkeypatch):
            monkeypatch.setattr(settings, "ENV_LOADING_ENABLED", False)
    """,
    "core/settings.py": """
        import os

        from dotenv import load_dotenv


        class NotReady(Exception):
            pass


        def load_env():
            load_dotenv(override=False)
    """,
    "core/db.py": '''
        """Supabase REST and Postgres pool. No table names here: UPDATE reports is the tagger's job."""
        import asyncpg
        from supabase import create_client

        from .settings import NotReady
    ''',
    "core/llm.py": """
        import anthropic
        import openai
        from langsmith import traceable
    """,
    "core/pdf.py": """
        import fitz
        import pymupdf
    """,
    "domain/reports.py": """
        from pathlib import Path

        import yaml

        IN_SCOPE_STATUSES = ("auto", "verified")
    """,
    "domain/stocks.py": """
        from research_desk.core.settings import NotReady

        from . import reports
    """,
    "collector/cli.py": """
        from .run import run


        def register(subparsers):
            subparsers.add_parser("collect")
    """,
    "collector/run.py": """
        from research_desk.core import db
        from research_desk.domain.stocks import lookup

        from . import storage
        from .telegram import TelegramClient
    """,
    "collector/storage.py": '''
        def max_message_id(sb):
            """Return max(message_id) from reports ∪ failed_attempts for this chat."""
            sb.table("reports").select("message_id").execute()
            sb.table("failed_attempts").select("message_id").execute()
            return "DELETE FROM failed_attempts WHERE id = $1"
    ''',
    "collector/telegram.py": """
        from telethon import TelegramClient
    """,
    "tagger/cli.py": """
        from research_desk.core.db import connect
        from research_desk.domain import reports, stocks

        from .graph import build_graph
    """,
    "tagger/graph.py": """
        from langgraph.graph import END, START, StateGraph

        from .nodes import extract_pdf
    """,
    "tagger/nodes/extract_pdf.py": """
        from ...core.pdf import page_texts
        from ..state import RowState
    """,
    "tagger/state.py": "",
    "tagger/sql.py": '''
        TAGGER_VERSION = "langgraph-tagger@2.0"
        CLAIM_SQL = """
        UPDATE reports
           SET tagging_status = 'processing'
         WHERE id IN (SELECT id FROM reports WHERE tagging_status = 'pending')
        """
    ''',
    "tagger/tests/test_sql.py": """
        import openai
        from research_desk.collector import storage
        from research_desk.features.reports.store import report_rows

        assert "UPDATE report_summaries" not in "x"
    """,
    "features/__init__.py": '"""Web app features."""\n',
    "features/analysis/__init__.py": """
        from .service import ai_slot, analyze_report, phase2_llm, save_comparison, summaries_for
    """,
    "features/analysis/service.py": """
        from research_desk.core.llm import with_retry
        from research_desk.domain.reports import is_in_scope

        from .store import fetch_summaries
    """,
    "features/analysis/store.py": """
        def fetch_summaries(sb, ids):
            return sb.table("report_summaries").select("*").in_("report_id", ids).execute()
    """,
    "features/analysis/jobs.py": """
        def register(subparsers):
            return None
    """,
    "features/reports/__init__.py": """
        from .router import router
        from .service import get_report, period_rows, public_report, report_row, stock_rows
    """,
    "features/reports/router.py": """
        from fastapi import APIRouter

        from .service import get_report

        router = APIRouter()
    """,
    "features/reports/service.py": """
        from research_desk.features.analysis import analyze_report, summaries_for

        from ..analysis import ai_slot
        from . import store
    """,
    "features/reports/store.py": '''
        """Rows come from reports (분석 대상만)."""


        def report_rows(sb):
            return sb.table("reports").select("*").execute()
    ''',
    "features/compare/__init__.py": """
        from .router import router
    """,
    "features/compare/router.py": """
        from research_desk.features import analysis, reports
        from research_desk.features.reports import get_report

        from .logic import compare_financials
    """,
    "features/compare/logic.py": '''
        def compare_financials(left, right):
            """Never reads from report_summaries; analysis.save_comparison does the UPDATE report_summaries."""
            return []
    ''',
    "features/coverage/__init__.py": """
        from .router import router
        from .service import invalidate
    """,
    "features/coverage/router.py": """
        from .service import invalidate
    """,
    "features/coverage/service.py": '''
        """Coverage counts rows it gets from reports.period_rows; it never selects FROM reports itself."""
        from research_desk.domain.stocks import listing
        from research_desk.features.reports import period_rows, stock_rows


        def invalidate():
            return None
    ''',
    "features/review/__init__.py": """
        from .router import router
    """,
    "features/review/router.py": """
        from research_desk.core.pdf import resolve_in_storage
        from research_desk.features.coverage import invalidate

        from .store import ReviewStore
    """,
    "features/review/store.py": """
        class ReviewStore:
            def queue(self, sb):
                return sb.table("reports").select("*").eq("tagging_status", "review_needed").execute()
    """,
    "features/review/tests/test_store.py": """
        from research_desk.features.analysis.store import fetch_summaries
        from research_desk.tagger.sql import CLAIM_SQL

        SQL = "UPDATE report_summaries SET diff_narrative = NULL"
    """,
    "features/companies/__init__.py": """
        from .router import router
    """,
    "features/companies/router.py": """
        from research_desk.core.settings import NotReady
        from research_desk.domain.stocks import listing

        from .favorites import load_favorites
    """,
    "features/companies/favorites.py": "",
    "web/__init__.py": """
        from .app import create_app
    """,
    "web/app.py": """
        import research_desk.features.analysis
        from research_desk.core.settings import NotReady
        from research_desk.domain import stocks
        from research_desk.features import companies, compare, coverage, review
        from research_desk.features import reports
        from research_desk.features.companies import router as companies_router
    """,
    "web/server.py": """
        import uvicorn

        from .app import create_app


        def register(subparsers):
            return None
    """,
    "tests/test_entry.py": """
        from research_desk.cli import main
        from research_desk.features.reports.store import report_rows
    """,
}


def test_clean_tree_passes(tmp_path):
    violations = check_tree(make_tree(tmp_path, CLEAN_TREE))
    assert violations == [], format_violations(violations)


def test_partial_tree_with_only_package_markers_passes(tmp_path):
    root = make_tree(tmp_path, {
        "__init__.py": '"""Research Desk."""\n',
        "features/__init__.py": '"""Web app features."""\n',
    })
    assert check_tree(root) == []


# ── R1~R4: 공용 설비·공용 기준·수집기·분류기 ──────────────────────────────────


def test_r1_core_imports_nothing_outside_core(tmp_path):
    root = make_tree(tmp_path, {
        "core/settings.py": """
            import os
            from research_desk.core import db
            from . import db as same_area
            from research_desk.domain import reports
            from ..domain.stocks import lookup
            import research_desk.features.reports
            from research_desk import web
        """,
        "core/db.py": "",
        "domain/reports.py": "",
        "domain/stocks.py": "",
    })
    assert hits(check_tree(root)) == at("core/settings.py", 4, 5, 6, 7, rule="R1 core")


def test_r2_domain_imports_only_core_and_domain(tmp_path):
    root = make_tree(tmp_path, {
        "domain/stocks.py": """
            from research_desk.core.settings import NotReady
            from research_desk.domain import reports
            from .reports import IN_SCOPE_STATUSES
            from research_desk.collector import storage
            from research_desk.features.reports import get_report
            from research_desk.web import app
        """,
        "domain/reports.py": "",
    })
    assert hits(check_tree(root)) == at("domain/stocks.py", 4, 5, 6, rule="R2 domain")


def test_r3_collector_imports_only_core_domain_and_collector(tmp_path):
    root = make_tree(tmp_path, {
        "collector/run.py": """
            from research_desk.core import db
            from research_desk.domain.stocks import lookup
            from research_desk.collector.storage import Storage
            from .telegram import TelegramClient
            from research_desk.tagger import graph
            from research_desk.features.reports import get_report
            from research_desk.web.app import create_app
        """,
        "collector/storage.py": "",
        "collector/telegram.py": "",
    })
    assert hits(check_tree(root)) == at("collector/run.py", 5, 6, 7, rule="R3 collector")


def test_r4_tagger_imports_only_core_domain_and_tagger(tmp_path):
    root = make_tree(tmp_path, {
        "tagger/graph.py": """
            from research_desk.core.llm import LLMClient
            from research_desk.domain import reports
            from .state import RowState
            from research_desk.collector import storage
            from research_desk.features.review import router
            import research_desk.web
        """,
        "tagger/state.py": "",
    })
    assert hits(check_tree(root)) == at("tagger/graph.py", 4, 5, 6, rule="R4 tagger")


# ── R5·R6: 기능 ───────────────────────────────────────────────────────────────


def test_r5_other_features_only_through_their_public_window(tmp_path):
    root = make_tree(tmp_path, {
        "features/reports/__init__.py": """
            from .router import router
            from .service import get_report
        """,
        "features/reports/router.py": "router = object()\n",
        "features/reports/service.py": "def get_report(rid):\n    return rid\n",
        "features/reports/store.py": "",
        "features/compare/logic.py": "",
        "features/compare/service.py": """
            from research_desk.core.settings import NotReady
            from research_desk.domain import reports as domain_reports
            from research_desk.features.compare import logic
            from . import logic as same_feature
            from research_desk.features.reports import get_report
            from research_desk.features.reports import router
            import research_desk.features.reports
            from research_desk.features import reports
            from ..reports import get_report as via_relative
            from research_desk.features.reports.store import fetch_rows
            from research_desk.features.reports import store
            import research_desk.features.reports.service
            from ..reports.store import fetch_rows as via_relative_store
            import research_desk.features.reports.router
        """,
    })
    violations = check_tree(root)
    assert hits(violations) == at("features/compare/service.py", 10, 11, 12, 13, 14, rule="R5 기능")
    assert all("research_desk.features.reports" in v.message for v in violations)


def test_r5_features_do_not_import_collector_tagger_or_web(tmp_path):
    root = make_tree(tmp_path, {
        "features/coverage/service.py": """
            import research_desk.collector
            from research_desk.tagger.graph import build_graph
            from research_desk.web import app
            from research_desk.web.app import create_app
            from research_desk import tests
        """,
    })
    assert hits(check_tree(root)) == at("features/coverage/service.py", 1, 2, 3, 4, 5, rule="R5 기능")


def test_star_import_in_a_window_publishes_no_submodule_names(tmp_path):
    root = make_tree(tmp_path, {
        # 실행하면 `from .service import *`가 service가 묶은 store(하위 모듈)까지 창구로 옮긴다.
        "features/reports/__init__.py": "from .service import *\n",
        "features/reports/service.py": "from . import store\n\n\ndef get_report(rid):\n    return rid\n",
        "features/reports/store.py": "",
        "features/compare/service.py": """
            from research_desk.features.reports import get_report
            from research_desk.features.reports import store
        """,
        "web/app.py": "from research_desk.features.reports import store\n",
    })
    assert hits(check_tree(root)) == (
        at("features/compare/service.py", 2, rule="R5 기능")
        | at("web/app.py", 1, rule="R7 web")
    )


def test_names_the_window_does_not_really_bind_are_not_public(tmp_path):
    root = make_tree(tmp_path, {
        # __all__에만 있는 이름, 하위 모듈 import의 부수효과로 생기는 이름, 값 없는 주석 선언은
        # 창구가 묶은 이름이 아니다.
        "features/reports/__init__.py": """
            __all__ = ["store", "cache", "router"]

            from .router import router
            from .store import fetch_rows
            store.loaded = True
            cache: dict
        """,
        "features/reports/router.py": "router = object()\n",
        "features/reports/store.py": "def fetch_rows():\n    return []\n",
        "features/reports/cache.py": "",
        "features/compare/service.py": """
            from research_desk.features.reports import fetch_rows
            from research_desk.features.reports import router
            from research_desk.features.reports import store
            from research_desk.features.reports import cache
        """,
        "web/app.py": "from research_desk.features.reports import store\n",
    })
    assert hits(check_tree(root)) == (
        at("features/compare/service.py", 3, 4, rule="R5 기능")
        | at("web/app.py", 1, rule="R7 web")
    )


def test_names_bound_explicitly_in_a_window_are_public(tmp_path):
    root = make_tree(tmp_path, {
        "features/reports/__init__.py": """
            from . import store
            from .service import get_report as service
            import research_desk.features.reports.jobs as jobs
            settings = object()
            if True:
                logic = None


            def schemas():
                return None


            class cache:
                pass
        """,
        "features/reports/store.py": "",
        "features/reports/service.py": "def get_report(rid):\n    return rid\n",
        "features/reports/jobs.py": "",
        "features/reports/settings.py": "",
        "features/reports/logic.py": "",
        "features/reports/schemas.py": "",
        "features/reports/cache.py": "",
        "features/compare/service.py": """
            from research_desk.features.reports import store
            from research_desk.features.reports import service
            from research_desk.features.reports import jobs
            from research_desk.features.reports import settings
            from research_desk.features.reports import logic
            from research_desk.features.reports import schemas
            from research_desk.features.reports import cache
        """,
    })
    violations = check_tree(root)
    assert violations == [], format_violations(violations)


def test_r6_feature_dependency_cycle_is_detected(tmp_path):
    root = make_tree(tmp_path, {
        "features/alpha/__init__.py": "from research_desk.features.beta import helper\n",
        "features/beta/__init__.py": "def helper():\n    return 1\n",
        "features/beta/service.py": "from research_desk.features.gamma import thing\n",
        "features/gamma/__init__.py": "from ..alpha import helper\nthing = 1\n",
    })
    violations = check_tree(root)
    assert hits(violations) == at("features/alpha/__init__.py", 1, rule="R6 순환 금지")
    (cycle,) = violations
    assert "alpha → beta → gamma → alpha" in cycle.message


def test_r6_cycle_report_names_every_import_in_the_loop(tmp_path):
    root = make_tree(tmp_path, {
        "features/reports/service.py": "from research_desk.features.analysis import analyze_report\n",
        "features/analysis/service.py": "import os\nfrom research_desk.features.reports import report_row\n",
    })
    violations = check_tree(root)
    assert hits(violations) == at("features/analysis/service.py", 2, rule="R6 순환 금지")
    (cycle,) = violations
    assert "analysis → reports → analysis" in cycle.message
    assert "research_desk/features/analysis/service.py:2" in cycle.message
    assert "research_desk/features/reports/service.py:1" in cycle.message


def test_r6_acyclic_features_and_test_only_back_edges_pass(tmp_path):
    root = make_tree(tmp_path, {
        "features/analysis/__init__.py": "def analyze_report(row):\n    return row\n",
        "features/reports/__init__.py": "from research_desk.features.analysis import analyze_report\n",
        "features/compare/__init__.py": "from research_desk.features import analysis, reports\n",
        "features/coverage/__init__.py": "from research_desk.features.reports import period_rows\n",
        "features/review/__init__.py": "from research_desk.features.coverage import invalidate\n",
        "features/analysis/tests/test_back_edge.py": "from research_desk.features.review import router\n",
    })
    assert check_tree(root) == []


# ── R7·R8: 웹 조립·입구 ───────────────────────────────────────────────────────


def test_r7_web_imports_only_core_domain_and_feature_windows(tmp_path):
    root = make_tree(tmp_path, {
        "features/companies/__init__.py": "from .router import router\n",
        "features/companies/router.py": "router = object()\n",
        "features/companies/favorites.py": "",
        "web/server.py": "from .app import create_app\n",
        "web/app.py": """
            from research_desk.core.settings import NotReady
            from research_desk.domain import stocks
            from research_desk.features import companies
            from research_desk.features.companies import router as companies_router
            from . import server
            from research_desk.features.companies.favorites import load
            import research_desk.collector
            from research_desk.tagger import graph
            from research_desk.features.companies import favorites
            import research_desk.features
        """,
    })
    assert hits(check_tree(root)) == at("web/app.py", 6, 7, 8, 9, 10, rule="R7 web")


def test_r8_only_the_entry_imports_cli_and_package_roots_import_nothing(tmp_path):
    root = make_tree(tmp_path, {
        "__init__.py": '"""Research Desk."""\nfrom research_desk.core import settings\n',
        "__main__.py": "from research_desk.cli import main\nfrom .cli import main as again\n",
        "cli.py": """
            from research_desk.collector.cli import register as register_collect
            from research_desk.tagger import cli as tag_cli
            from research_desk.features.reports.store import report_rows
            from research_desk.web.server import register as register_web
            from research_desk.domain.stocks import write_version
            from research_desk.core.settings import load_env
            from research_desk import __main__
        """,
        "conftest.py": "from research_desk.core import settings\n",
        "core/settings.py": "from research_desk import __main__\n",
        "collector/cli.py": "def register(subparsers):\n    return None\n",
        "tagger/cli.py": "",
        "domain/stocks.py": "",
        "features/__init__.py": "from . import reports\n",
        "features/reports/store.py": "",
        "features/reports/service.py": "from research_desk.cli import main\n",
        "features/reports/tests/test_cli.py": "from research_desk.cli import main\n",
        "web/server.py": "from .. import cli\n",
    })
    violations = check_tree(root)
    assert hits(violations) == (
        at("__init__.py", 2, rule="R8 입구")
        | at("core/settings.py", 1, rule="R8 입구")
        | at("features/__init__.py", 1, rule="R8 입구")
        | at("features/reports/service.py", 1, rule="R8 입구")
        | at("web/server.py", 1, rule="R8 입구")
    )
    (relative,) = [v for v in violations if v.path == "research_desk/web/server.py"]
    assert "research_desk.cli" in relative.message


# ── R9·R10: 외부 도구·옛 코드 ─────────────────────────────────────────────────


def test_r9_external_tools_stay_in_their_area(tmp_path):
    root = make_tree(tmp_path, {
        "core/db.py": """
            import asyncpg
            import supabase
            from anthropic import Anthropic
            from dotenv import load_dotenv
            from openai import OpenAI
            import fitz
            import pymupdf
        """,
        "collector/telegram.py": "from telethon import TelegramClient\n",
        "tagger/graph.py": "from langgraph.graph import StateGraph\n",
        "features/analysis/llm.py": """
            import openai
            from anthropic import Anthropic


            def connect():
                from supabase import create_client
                return create_client
        """,
        "domain/stocks.py": "import fitz.utils\n",
        "web/app.py": "from dotenv import load_dotenv\n",
        "collector/run.py": "import asyncpg\nfrom langgraph.graph import END\n",
        "tagger/nodes/extract_pdf.py": "import pymupdf\nfrom telethon.tl import types\n",
        "cli.py": "import supabase\n",
        "features/analysis/tests/test_llm.py": "import openai\nimport fitz\nfrom telethon import TelegramClient\n",
    })
    assert hits(check_tree(root)) == (
        at("features/analysis/llm.py", 1, 2, 6, rule="R9 외부 도구")
        | at("domain/stocks.py", 1, rule="R9 외부 도구")
        | at("web/app.py", 1, rule="R9 외부 도구")
        | at("collector/run.py", 1, 2, rule="R9 외부 도구")
        | at("tagger/nodes/extract_pdf.py", 1, 2, rule="R9 외부 도구")
        | at("cli.py", 1, rule="R9 외부 도구")
    )


def test_r10_old_code_is_never_imported_even_by_tests(tmp_path):
    root = make_tree(tmp_path, {
        "core/settings.py": "import config\n",
        "collector/run.py": """
            from storage import Storage
            import telegram_client
            from . import storage as new_storage
            from research_desk.collector import storage as also_new
            import research_desk.collector
        """,
        "collector/storage.py": "",
        "tagger/cli.py": "from langgraph_tagger.cli import main\nimport langgraph_tagger\n",
        "features/reports/tests/test_old.py": "import collector\nfrom main import compute_exit_code\n",
        "tests/test_old_paths.py": "import langgraph_tagger.workspace.api\n",
        "conftest.py": "import config\n",
    })
    assert hits(check_tree(root)) == (
        at("core/settings.py", 1, rule="R10 옛 코드")
        | at("collector/run.py", 1, 2, rule="R10 옛 코드")
        | at("tagger/cli.py", 1, 2, rule="R10 옛 코드")
        | at("features/reports/tests/test_old.py", 1, 2, rule="R10 옛 코드")
        | at("tests/test_old_paths.py", 1, rule="R10 옛 코드")
        | at("conftest.py", 1, rule="R10 옛 코드")
    )


def test_tests_folders_are_exempt_from_every_rule_but_r10(tmp_path):
    root = make_tree(tmp_path, {
        "features/reports/tests/test_store.py": """
            import openai
            import research_desk.cli
            from research_desk.collector.storage import Storage
            from research_desk.features.analysis.store import fetch_summaries
            from research_desk.tagger.sql import CLAIM_SQL

            SQL = "UPDATE report_summaries SET diff_narrative = NULL"


            def test_store(sb):
                sb.table("failed_attempts")
        """,
        "core/tests/conftest.py": "from research_desk.web.app import create_app\nimport telethon\n",
        "tagger/tests/golden/_synthesize.py": "import fitz\nimport langgraph_tagger\n",
    })
    assert hits(check_tree(root)) == at("tagger/tests/golden/_synthesize.py", 2, rule="R10 옛 코드")


def test_root_conftest_that_imports_core_settings_is_not_flagged(tmp_path):
    """research_desk/conftest.py(T1): core.settings의 .env 스위치를 끄고 켜는 준비. 테스트 코드다."""
    root = make_tree(tmp_path, {
        "__init__.py": '"""Research Desk."""\n',
        "conftest.py": '''
            import pytest

            from research_desk.core import settings
            from research_desk.core.settings import load_env


            @pytest.fixture(autouse=True)
            def _env_loading_off(monkeypatch):
                monkeypatch.setattr(settings, "ENV_LOADING_ENABLED", False)


            @pytest.fixture
            def temp_env_file(tmp_path, monkeypatch):
                env = tmp_path / ".env"
                env.write_text("SUPABASE_URL=https://example.invalid\\n", encoding="utf-8")
                monkeypatch.setattr(settings, "ENV_LOADING_ENABLED", True)
                load_env(env)
                return env
        ''',
        "core/settings.py": "ENV_LOADING_ENABLED = True\n\n\ndef load_env(path=None):\n    return None\n",
    })
    assert check_tree(root) == []


def test_conftest_files_are_exempt_from_every_rule_but_r10(tmp_path):
    root = make_tree(tmp_path, {
        # 패키지 뿌리는 conftest와 같은 import라도 잡힌다(R8).
        "__init__.py": "from research_desk.core import settings\n",
        "conftest.py": """
            import dotenv
            import research_desk.cli
            from research_desk.collector.storage import Storage
            from research_desk.core import settings
            from research_desk.features.reports.store import report_rows
            import langgraph_tagger

            SQL = "UPDATE reports SET tagging_status = 'pending'"


            def rows(sb):
                return sb.table("report_summaries").select("*").execute()
        """,
        "features/reports/__init__.py": "from research_desk.features.analysis import analyze_report\n",
        "features/reports/store.py": "",
        "features/analysis/__init__.py": "def analyze_report(row):\n    return row\n",
        # 기능 폴더 안의 conftest: 하위 모듈 import도, 거꾸로 가는 기능 의존(순환)도 잡지 않는다.
        "features/analysis/conftest.py": """
            import openai
            from research_desk.features.reports.store import report_rows
            from research_desk.tagger.graph import build_graph
            from main import compute_exit_code
        """,
    })
    assert hits(check_tree(root)) == (
        at("__init__.py", 1, rule="R8 입구")
        | at("conftest.py", 6, rule="R10 옛 코드")
        | at("features/analysis/conftest.py", 4, rule="R10 옛 코드")
    )


# ── R11: 표 주인 ──────────────────────────────────────────────────────────────


def test_r11_table_access_outside_owner_areas_is_detected(tmp_path):
    root = make_tree(tmp_path, {
        "features/coverage/service.py": """
            def load(sb):
                return sb.table("reports").select("*").execute()


            QUERY = "select id from reports where id = 1"
            DIFF = f"UPDATE report_summaries SET diff_narrative = {QUERY}"
            RETRY = '''
            INSERT INTO failed_attempts (message_id)
            VALUES ($1)
            '''
            JOINED = "SELECT * FROM x JOIN report_summaries s ON s.report_id = x.id"
            QUALIFIED = 'SELECT count(*) FROM public."reports"'
        """,
        "collector/storage.py": """
            def save(sb):
                sb.table("reports").upsert({}).execute()
                sb.table("failed_attempts").insert({}).execute()
                sb.table("report_summaries").select("*").execute()
                return "UPDATE failed_attempts SET attempt_count = attempt_count + 1"
        """,
        "tagger/sql.py": '''
            CLAIM = """UPDATE reports SET tagging_status = 'processing'"""
            OOPS = "INSERT INTO failed_attempts VALUES ($1)"
        ''',
        "features/review/store.py": 'def queue(sb):\n    return sb.table("reports").select("*").execute()\n',
        "features/reports/store.py": """
            def rows(sb):
                sb.table("reports").select("*").execute()
                return sb.table("report_summaries").select("*").execute()
        """,
        "features/analysis/store.py": """
            def save(sb):
                sb.table("report_summaries").upsert({}).execute()
                sb.table(table_name="reports").select("*").execute()
                return "LEFT JOIN report_summaries rs ON rs.report_id = r.id"
        """,
        "core/db.py": 'HEALTH = "SELECT 1 FROM reports LIMIT 1"\n',
        "domain/reports.py": 'def rows(sb):\n    return sb.table("reports")\n',
        "web/app.py": 'def rows(sb):\n    return sb.table("failed_attempts")\n',
        "cli.py": 'SQL = "delete from failed_attempts"\n',
        "features/compare/tests/test_store.py": """
            SQL = "UPDATE report_summaries SET x = 1"


            def test_rows(sb):
                sb.table("reports")
        """,
    })
    violations = check_tree(root)
    assert hits(violations) == (
        at("features/coverage/service.py", 2, 5, 6, 8, 11, 12, rule="R11 표 주인")
        | at("collector/storage.py", 4, rule="R11 표 주인")
        | at("tagger/sql.py", 2, rule="R11 표 주인")
        | at("features/reports/store.py", 3, rule="R11 표 주인")
        | at("features/analysis/store.py", 3, rule="R11 표 주인")
        | at("core/db.py", 1, rule="R11 표 주인")
        | at("domain/reports.py", 2, rule="R11 표 주인")
        | at("web/app.py", 2, rule="R11 표 주인")
        | at("cli.py", 1, rule="R11 표 주인")
    )
    (retry,) = [v for v in violations if v.path.endswith("coverage/service.py") and v.line == 8]
    assert "failed_attempts" in retry.message


def test_r11_ignores_docstrings_but_not_other_strings(tmp_path):
    root = make_tree(tmp_path, {
        "features/coverage/service.py": '''
            """Coverage reads rows from reports via reports.period_rows; never SELECT * FROM reports."""


            class Cache:
                """Holds rows loaded from reports. UPDATE reports never happens here."""


            def aggregate(rows):
                """Counts rows. INSERT INTO report_summaries is analysis' job, JOIN failed_attempts too."""
                return rows


            async def refresh():
                """Reload from reports."""
                return None


            def helper():
                count = 1
                "UPDATE reports SET tagging_status = 'verified'"
                return count


            NOTE = "rows come from reports"
        ''',
    })
    assert hits(check_tree(root)) == at("features/coverage/service.py", 20, 24, rule="R11 표 주인")


def test_r11_matches_whole_table_names_only(tmp_path):
    root = make_tree(tmp_path, {
        "features/coverage/logic.py": """
            A = "FROM reports_archive"
            B = "the reports feature"
            C = "reports"
            D = "from reportsx"
            E = "UPDATE report_summaries_old SET x = 1"
            F = "FROMreports"
            G = "failed_attempts_count"
        """,
    })
    assert check_tree(root) == []


# ── import 해석·보고 형식·검사 범위 ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("module", "is_package", "level", "name", "expected"),
    [
        ("research_desk.features.compare.service", False, 1, None, "research_desk.features.compare"),
        ("research_desk.features.compare.service", False, 1, "logic", "research_desk.features.compare.logic"),
        ("research_desk.features.compare.service", False, 2, None, "research_desk.features"),
        ("research_desk.features.compare.service", False, 2, "reports", "research_desk.features.reports"),
        ("research_desk.features.compare.service", False, 3, "core.settings", "research_desk.core.settings"),
        ("research_desk.features.compare", True, 1, "service", "research_desk.features.compare.service"),
        ("research_desk.features.compare", True, 2, "analysis", "research_desk.features.analysis"),
        ("research_desk.__main__", False, 1, "cli", "research_desk.cli"),
        ("research_desk", True, 1, "core", "research_desk.core"),
        ("research_desk.core.settings", False, 3, "x", None),
        ("research_desk", True, 2, None, None),
    ],
)
def test_relative_imports_resolve_to_absolute_names(module, is_package, level, name, expected):
    assert resolve_relative(module, is_package, level, name) == expected


def test_relative_import_violation_names_the_resolved_module(tmp_path):
    root = make_tree(tmp_path, {
        "core/settings.py": "from ..domain import reports\n",
        "domain/reports.py": "",
    })
    (violation,) = check_tree(root)
    assert (violation.path, violation.line, violation.rule) == ("research_desk/core/settings.py", 1, "R1 core")
    assert "research_desk.domain.reports" in violation.message
    assert str(violation).startswith("research_desk/core/settings.py:1 — R1 core: ")


def test_failure_report_lists_every_violation_as_file_line_rule(tmp_path):
    root = make_tree(tmp_path, {
        "core/settings.py": "import config\nfrom research_desk.web import app\n",
    })
    violations = check_tree(root)
    lines = format_violations(violations).splitlines()
    assert len(violations) == 2
    assert lines[1:] == [str(v) for v in violations]
    assert lines[1].startswith("research_desk/core/settings.py:1 — R10 옛 코드: ")
    assert lines[2].startswith("research_desk/core/settings.py:2 — R1 core: ")


def test_unparsable_file_fails_the_check(tmp_path):
    root = make_tree(tmp_path, {"core/broken.py": "def broken(:\n    pass\n"})
    (violation,) = check_tree(root)
    assert (violation.path, violation.rule) == ("research_desk/core/broken.py", "구문 오류")


def test_scan_stays_inside_research_desk(tmp_path):
    root = make_tree(tmp_path, {"core/settings.py": "import os\n"})
    outside = {
        tmp_path / "langgraph_tagger" / "cli.py": "import langgraph\nimport config\n",
        tmp_path / ".venv" / "Lib" / "site.py": "import langgraph_tagger\n",
        tmp_path / "tests" / "test_old.py": "import collector\n",
        tmp_path / "main.py": "import storage\n",
        root / ".pytest_cache" / "finish_collection.py": "import collector\n",
        root / "__pycache__" / "stray.py": "import config\n",
    }
    for path, source in outside.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    assert check_tree(root) == []


def test_missing_package_folder_is_an_error_not_a_pass(tmp_path):
    with pytest.raises(FileNotFoundError):
        check_tree(tmp_path / "research_desk")
