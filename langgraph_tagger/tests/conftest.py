"""Shared test fixtures for langgraph_tagger."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from langgraph_tagger.llm_provider import StructuredResult
from langgraph_tagger.llm_schemas import LLMExtraction, OOSSignals
from langgraph_tagger.vocabulary.krx import KRXIndex


@pytest.fixture(scope="session")
def krx() -> KRXIndex:
    """Real KRX index loaded once per session."""
    return KRXIndex.load(Path("docs/stock_data/KRX_stocks_data.csv"))


@pytest.fixture
def mock_llm_client():
    """LLMClient stand-in whose ``parse`` returns a configurable LLMExtraction.

    Usage:
        mock_llm_client.set_response(LLMExtraction(...))
        # or
        mock_llm_client.set_refusal("policy violation")
        # or
        from openai import RateLimitError
        mock_llm_client.set_exception(RateLimitError("rate limited"))
    """
    client = MagicMock()
    parse = AsyncMock()
    client.parse = parse

    def _set_response(parsed: LLMExtraction):
        parse.return_value = StructuredResult(parsed=parsed)

    def _set_refusal(reason: str):
        parse.return_value = StructuredResult(parsed=None, refusal=reason)

    def _set_exception(exc: Exception):
        parse.side_effect = exc

    client.set_response = _set_response
    client.set_refusal = _set_refusal
    client.set_exception = _set_exception
    return client


def make_llm_extraction(**overrides) -> LLMExtraction:
    """Factory for tests — v2 sane defaults overridable per test."""
    defaults = dict(
        report_type="단일종목",
        title="삼성전자 1Q26 Preview",
        published_at="2026-05-01",
        stock_codes_raw=["005930"],
        company_names_raw=["삼성전자"],
        publisher_canon="키움증권",
        publisher_type="broker",
        analysts=["홍길동"],
        oos_signals=OOSSignals(
            foreign_primary_coverage=False, etf_or_fund=False,
            digital_asset=False, private_company_likely=False,
        ),
        self_confidence="high",
        notes=None,
    )
    defaults.update(overrides)
    return LLMExtraction(**defaults)


@pytest.fixture
def mock_supabase():
    """In-memory mock for SupabaseSQL: records UPDATE/REVERT calls, replays SELECT."""
    class MockSupabase:
        def __init__(self):
            self.executed: list[tuple[str, tuple]] = []
            self.fetched: list[tuple[str, tuple]] = []
            self._fetch_responses: list[list[dict]] = []

        def queue_fetch(self, rows: list[dict]):
            self._fetch_responses.append(rows)

        async def fetch(self, sql, args=()):
            self.fetched.append((sql, tuple(args)))
            if self._fetch_responses:
                return self._fetch_responses.pop(0)
            return []

        async def execute(self, sql, args=()):
            self.executed.append((sql, tuple(args)))

        async def close(self):
            pass

    return MockSupabase()
