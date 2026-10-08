"""Stand-ins for what compare uses, for the compare tests.

- ``World``: reports and their saved summaries behind the two window functions compare calls.
  ``get_report`` answers like ``reports.get_report``: the in-scope rule of ``domain.reports``, the
  public shape of ``reports.public_report``, else 404 ``기업 보고서를 찾을 수 없습니다.``.
  ``save_comparison`` writes like ``analysis.save_comparison``: the four comparison fields of an
  existing summary.
- ``FakeAI``: ``analysis.phase2_llm`` handing out a client whose ``parse`` records each call and
  answers a DiffResult (optionally held by a gate, failing, or refusing).
- ``FakeSupabase``: an in-memory supabase-py client for the real reports and analysis windows
  (select / update / upsert with eq, in_ and is_('null')).
- ``until`` / ``never``: wait on a condition by wall-clock time, not by loop ticks.
- ``build_app``: this feature's router plus the NotReady → 503 answer the web app adds.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from research_desk.core.llm import StructuredResult
from research_desk.core.settings import NotReady
from research_desk.domain.reports import is_in_scope
from research_desk.features import reports
from research_desk.features.compare import router
from research_desk.features.compare.schemas import DiffResult

REPORT_NOT_FOUND = '기업 보고서를 찾을 수 없습니다.'


def build_app() -> FastAPI:
    """This feature's router plus the NotReady → 503 answer the web app adds (spec §6, §9.9)."""
    app = FastAPI()
    app.include_router(router)

    @app.exception_handler(NotReady)
    async def not_ready(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=503)

    return app


def tagged(rid: int, published: Optional[str], *, publisher: Optional[str] = 'KB',
           codes=('016360',), report_type: Optional[str] = '단일종목', status: str = 'auto',
           reason: Optional[str] = None) -> dict:
    """A reports row as the tagger leaves it (the 15 columns the reports feature reads)."""
    return {'id': rid, 'published_at': published, 'sent_at': '2026-05-11T01:00:00+00:00',
            'report_type': report_type, 'publisher': publisher, 'stock_codes': list(codes),
            'company_names': ['삼성증권'], 'sectors_major': ['금융'], 'sectors_minor': ['증권'],
            'products': ['증권'], 'tagging_status': status, 'out_of_scope_reason': reason,
            'file_path': f'2026/{rid}.pdf', 'file_name': f'{rid}_report.pdf',
            'title': f'report {rid}'}


def metric(value: float, **changes) -> dict:
    """An estimate the comparison can pair (영업이익 2026, 연결, 기본)."""
    return {'metric': '영업이익', 'fiscal_period': '2026', 'value': value, 'previous_value': None,
            'unit': '십억원', 'currency': 'KRW', 'accounting_basis': '연결', 'value_type': '추정',
            'scenario': '기본', 'evidence': {'page': 2, 'quote': f'2026E 영업이익 {value}'},
            **changes}


def analysed(rid: int, target: Optional[int] = None, *metrics: dict, **fields) -> dict:
    """A saved summary (report_summaries row) of the active version."""
    return {'report_id': rid, 'summary_version': 'llm-summary@1.0',
            'one_line_summary': f'saved {rid}', 'target_price_new': target,
            'financial_details': {'metrics': list(metrics)}, 'prev_report_id': None,
            'prev_match_type': None, 'diff_narrative': None, 'comparison_details': None,
            **fields}


class World:
    """Report rows and saved summaries, served as reports.get_report / analysis.save_comparison."""

    def __init__(self) -> None:
        self.rows: dict[int, dict] = {}
        self.summaries: dict[int, dict] = {}
        self.reads: list[int] = []
        self.saved: list[tuple] = []

    def add(self, row: dict, summary: Optional[dict] = None) -> dict:
        self.rows[row['id']] = row
        if summary is not None:
            self.summaries[row['id']] = summary
        return row

    def public(self, rid: int) -> dict:
        """What reports.get_report gives for ``rid`` now."""
        return reports.public_report(self.rows[rid], deepcopy(self.summaries.get(rid)))

    def get_report(self, rid: int) -> dict:
        self.reads.append(rid)
        row = self.rows.get(rid)
        if row is None or not is_in_scope(row):
            raise HTTPException(404, REPORT_NOT_FOUND)
        return self.public(rid)

    def save_comparison(self, report_id: int, prev_report_id: Optional[int], match_type: str,
                        narrative: Optional[str], comparison_details: Optional[dict] = None) -> None:
        self.saved.append((report_id, prev_report_id, match_type, narrative,
                           deepcopy(comparison_details)))
        if report_id in self.summaries:
            self.summaries[report_id].update(
                prev_report_id=prev_report_id, prev_match_type=match_type,
                diff_narrative=narrative, comparison_details=deepcopy(comparison_details))


class FakeClient:
    """The AI client phase2_llm hands out; closed when the ``async with`` block ends."""

    def __init__(self, ai: "FakeAI") -> None:
        self.ai = ai
        self.entered = self.closed = False

    async def __aenter__(self) -> "FakeClient":
        self.entered = True
        return self

    async def __aexit__(self, *exc) -> None:
        self.closed = True

    async def parse(self, **kwargs) -> StructuredResult:
        self.ai.calls.append(kwargs)
        self.ai.active += 1
        self.ai.peak = max(self.ai.peak, self.ai.active)
        try:
            if self.ai.gate is not None:
                await self.ai.gate.wait()
            if self.ai.error is not None:
                raise self.ai.error
            if self.ai.refusal is not None:
                return StructuredResult(parsed=None, refusal=self.ai.refusal)
            return StructuredResult(parsed=DiffResult(diff_narrative=self.ai.narrative),
                                    input_tokens=100, output_tokens=20)
        finally:
            self.ai.active -= 1


class FakeAI:
    """``analysis.phase2_llm`` stand-in: ``(client, model, timeout_s)``; records what happens."""

    def __init__(self, model: str = 'codex:gpt-6-luna', timeout_s: float = 180) -> None:
        self.model, self.timeout_s = model, timeout_s
        self.narrative: Optional[str] = 'KB는 2026년 영업이익 추정을 높였다.'
        self.gate: Optional[asyncio.Event] = None
        self.error: Optional[BaseException] = None
        self.refusal: Optional[str] = None
        self.prepared = 0                 # phase2_llm() calls
        self.calls: list[dict] = []       # parse() kwargs
        self.clients: list[FakeClient] = []
        self.active = self.peak = 0

    def phase2_llm(self):
        self.prepared += 1
        client = FakeClient(self)
        self.clients.append(client)
        return client, self.model, self.timeout_s


# ── an in-memory Supabase for the real windows ───────────────────────────────

class FakeQuery:
    """One chained supabase-py query on one table."""

    def __init__(self, db: "FakeSupabase", table: str) -> None:
        self.db, self.table = db, table
        self.kind, self.columns = 'select', None
        self.payload: Optional[dict] = None
        self.on_conflict: Optional[str] = None
        self.filters: list[tuple[str, str, Any]] = []

    def select(self, columns: str = '*') -> "FakeQuery":
        self.kind, self.columns = 'select', columns
        return self

    def update(self, payload: dict) -> "FakeQuery":
        self.kind, self.payload = 'update', deepcopy(payload)
        return self

    def upsert(self, payload: dict, on_conflict: Optional[str] = None) -> "FakeQuery":
        self.kind, self.payload, self.on_conflict = 'upsert', deepcopy(payload), on_conflict
        return self

    def eq(self, column: str, value: Any) -> "FakeQuery":
        self.filters.append(('eq', column, value))
        return self

    def in_(self, column: str, values) -> "FakeQuery":
        self.filters.append(('in', column, list(values)))
        return self

    def is_(self, column: str, value: str) -> "FakeQuery":
        assert value == 'null', f"only is_(col, 'null') is supported, got {value!r}"
        self.filters.append(('is', column, None))
        return self

    def values(self, operator: str, column: str) -> list:
        return [value for op, col, value in self.filters if (op, col) == (operator, column)]

    def _passes(self, row: dict) -> bool:
        for operator, column, value in self.filters:
            cell = row.get(column)
            if operator == 'is':
                ok = cell is None
            elif cell is None:
                ok = False
            elif operator == 'eq':
                ok = cell == value
            else:  # in
                ok = cell in value
            if not ok:
                return False
        return True

    def _shape(self, row: dict) -> dict:
        if self.columns is None or self.columns.strip() == '*':
            return deepcopy(row)
        return {name.strip(): deepcopy(row.get(name.strip())) for name in self.columns.split(',')}

    def execute(self) -> SimpleNamespace:
        self.db.executed.append(self)
        rows = self.db.tables.setdefault(self.table, [])
        if self.kind == 'upsert':
            key = self.on_conflict
            for row in rows:
                if row.get(key) == self.payload[key]:
                    row.clear()
                    row.update(deepcopy(self.payload))
                    break
            else:
                rows.append(deepcopy(self.payload))
            return SimpleNamespace(data=[deepcopy(self.payload)])
        matched = [row for row in rows if self._passes(row)]
        if self.kind == 'update':
            for row in matched:
                row.update(deepcopy(self.payload))
            return SimpleNamespace(data=[deepcopy(row) for row in matched])
        return SimpleNamespace(data=[self._shape(row) for row in matched])


class FakeSupabase:
    """``table(name)`` starts a FakeQuery over ``tables[name]`` (a list of row dicts)."""

    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {'reports': [], 'report_summaries': []}
        self.executed: list[FakeQuery] = []

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self, name)

    def queries(self, table: str, kind: str = 'select') -> list[FakeQuery]:
        return [q for q in self.executed if q.table == table and q.kind == kind]


# ── waiting by wall-clock time ───────────────────────────────────────────────

async def until(condition, timeout_s: float = 5.0) -> None:
    """Wait until ``condition()`` holds (reads run in worker threads, so ticks are not enough)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while not condition():
        if loop.time() > deadline:
            raise AssertionError('condition not reached')
        await asyncio.sleep(0.005)


async def never(condition, for_s: float = 0.2) -> None:
    """Fail if ``condition()`` holds at any point during the next ``for_s`` seconds."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + for_s
    while loop.time() < deadline:
        assert not condition(), 'condition reached'
        await asyncio.sleep(0.005)
    assert not condition(), 'condition reached'
