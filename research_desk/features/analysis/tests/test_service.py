"""Analysis service — the window reports and compare use (DB, AI and files faked).

Ported: the analysis part of
langgraph_tagger/workspace/tests/test_migration.py::test_selected_cached_reports_only_and_non_company_rejection
(reuse, rejection of non-단일종목 reports, concurrent request 409). Reading the
row (404) is the reports feature's part.

New (spec §9.5, §9.9, §13 item 3):
- the model key / codex CLI is checked only right before the AI call, so reuse,
  summaries_for, 409 and 422 work without keys;
- ai_slot() lets 2 AI calls run at a time and analysis waits for a free slot;
- the saved summary has today's fields; DB readiness gives NotReady("분석", …).
Every environment variable involved is set or deleted here.
"""
import asyncio
import json
from types import SimpleNamespace

import pymupdf
import pytest
from fastapi import HTTPException

from research_desk.core import db as core_db
from research_desk.core import llm as core_llm
from research_desk.core.llm import LLMClient, StructuredResult
from research_desk.core.settings import NotReady
from research_desk.features import analysis
from research_desk.features.analysis import service
from research_desk.features.analysis.financials import FinancialDetails
from research_desk.features.analysis.schemas import ExtractionResult

ENV = ('ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'LLM_MODEL_PHASE2', 'OPENAI_MODEL_PHASE2',
       'PHASE2_PER_REPORT_TIMEOUT_S', 'PHASE2_MAX_INPUT_TOKENS', 'PHASE2_SUMMARY_VERSION',
       'PHASE2_MAX_CONCURRENT', 'SUPABASE_URL', 'SUPABASE_SERVICE_KEY', 'SUPABASE_DB_URL',
       'STORAGE_BASE_DIR', 'CODEX_BIN', 'ANTHROPIC_EFFORT', 'CODEX_REASONING_EFFORT',
       'LANGSMITH_TRACING', 'LANGSMITH_API_KEY')

LIMIT_S = 5  # bound on awaits that would hang if the behaviour under test broke

IN_FLIGHT = '이 보고서는 분석 중입니다. 잠시 후 새로고침해 주세요.'
NOT_COMPANY = '금융 정보 분석은 단일종목 보고서를 선택해 주세요.'
NO_TEXT = 'PDF에서 읽을 수 있는 텍스트가 없습니다.'
DB_MISSING = 'DB 접속 설정(SUPABASE_URL, SUPABASE_SERVICE_KEY)이 없습니다'


# ── fakes ────────────────────────────────────────────────────────────────────

class FakeQuery:
    """Chainable stand-in for one supabase-py query on report_summaries."""

    def __init__(self, db, table):
        self.db, self.table = db, table
        self.kind, self.payload, self.on_conflict = None, None, None
        self.ids, self.filters = None, {}

    def select(self, cols):
        self.kind = 'select'
        return self

    def upsert(self, payload, on_conflict=None):
        self.kind, self.payload, self.on_conflict = 'upsert', payload, on_conflict
        return self

    def update(self, payload):
        self.kind, self.payload = 'update', payload
        return self

    def in_(self, col, values):
        assert col == 'report_id'
        self.ids = list(values)
        return self

    def eq(self, col, value):
        self.filters[col] = value
        return self

    def execute(self):
        assert self.table == 'report_summaries'
        self.db.executed.append(self)
        rows = self.db.rows
        if self.kind == 'select':
            data = [dict(rows[i]) for i in self.ids if i in rows
                    and rows[i].get('summary_version') == self.filters['summary_version']]
        elif self.kind == 'upsert':
            rows[self.payload['report_id']] = dict(self.payload)
            data = [dict(self.payload)]
        else:
            rid = self.filters['report_id']
            if rid in rows:
                rows[rid].update(self.payload)
            data = [dict(rows[rid])] if rid in rows else []
        return SimpleNamespace(data=data)


class FakeSupabase:
    def __init__(self):
        self.rows: dict[int, dict] = {}
        self.executed: list[FakeQuery] = []

    def table(self, name):
        return FakeQuery(self, name)

    def add_summary(self, rid, **fields):
        row = {'report_id': rid, 'summary_version': 'llm-summary@1.0',
               'one_line_summary': f'saved {rid}', 'financial_details': {'metrics': []},
               **fields}
        self.rows[rid] = row
        return dict(row)

    def of(self, kind):
        return [q for q in self.executed if q.kind == kind]


async def until(condition, tries=200):
    for _ in range(tries):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError('condition not reached')


def row(rid, report_type='단일종목', file_path='r.pdf', **extra):
    return {'id': rid, 'report_type': report_type, 'file_path': file_path, 'publisher': 'KB',
            'stock_codes': ['016360'], 'published_at': '2026-05-11', 'title': f'report {rid}',
            'tagging_status': 'auto', **extra}


def write_pdf(folder, name, pages):
    doc = pymupdf.open()
    for body in pages:
        doc.new_page().insert_text((72, 72), body)
    doc.save(str(folder / name))
    doc.close()
    return name


FD_INPUT = {
    'metrics': [
        {'metric': '영업이익', 'fiscal_period': '2026', 'value': 1234, 'previous_value': 1100,
         'unit': '십억원', 'currency': 'KRW', 'accounting_basis': '연결', 'value_type': '추정',
         'scenario': '기본', 'evidence': {'page': 1, 'quote': 'OP 2026F 1,234'},
         'previous_evidence': {'page': 2, 'quote': 'Revision OP 1,100 to 1,234'}},
        {'metric': '매출액', 'fiscal_period': '2026', 'value': 999, 'previous_value': None,
         'unit': '십억원', 'currency': 'KRW', 'accounting_basis': '연결', 'value_type': '추정',
         'scenario': '기본', 'evidence': {'page': 1, 'quote': 'Sales 999'}},
    ],
    'valuation': {'method': 'PER', 'target_horizon': '12개월', 'explanation': 'EPS x PER',
                  'assumptions': [],
                  'change_drivers': [{'category': '배수 변경', 'explanation': '배수 상향',
                                      'evidence': {'page': 1, 'quote': 'Target 85,000'}}]},
    'theses': [], 'catalysts': [],
    'rating': {'current_label': 'Buy', 'previous_label': None, 'definition': None,
               'horizon': None, 'evidence': None},
}


def extraction(**changes):
    values = dict(
        financial_details=FD_INPUT, target_price_new=85000, target_price_old=70000,
        target_price_dir='불변',  # wrong on purpose: arithmetic makes it 상향
        recommendation='매수', recommendation_dir='유지', one_line_summary='목표주가 상향',
        positive_points=['수주 증가'], risk_points=[], target_price_raw='85,000원',
        recommendation_raw='Buy', source_pages=[2, 1], extraction_confidence='high',
    )
    values.update(changes)
    return ExtractionResult.model_validate(values)


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def storage(monkeypatch, tmp_path):
    """No keys, no DB settings, no codex CLI; PDFs live in a temp storage folder."""
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    folder = tmp_path / 'reports'
    folder.mkdir()
    monkeypatch.setenv('STORAGE_BASE_DIR', str(folder))
    monkeypatch.setattr(core_llm, '_codex_bin', lambda: None)
    monkeypatch.setattr(service, '_supabase_client', None)  # prepare afresh in each test
    monkeypatch.setattr(service, '_analyzing', set())
    return folder


@pytest.fixture
def db(monkeypatch):
    fake = FakeSupabase()
    fake.made = []

    def supabase_client(url, key):
        fake.made.append((url, key))
        return fake

    monkeypatch.setenv('SUPABASE_URL', 'https://example.supabase.test')
    monkeypatch.setenv('SUPABASE_SERVICE_KEY', 'service-key')
    monkeypatch.setattr(core_db, 'supabase_client', supabase_client)
    return fake


@pytest.fixture
def ai(monkeypatch):
    """The AI boundary: LLMClient.parse records each call and returns ``ai.reply``."""
    state = SimpleNamespace(calls=[], reply=extraction(), gate=None, error=None)

    async def parse(self, **kwargs):
        state.calls.append(kwargs)
        if state.gate is not None:
            await state.gate.wait()
        if state.error is not None:
            raise state.error
        return StructuredResult(parsed=state.reply, input_tokens=1234, output_tokens=567)

    monkeypatch.setattr(LLMClient, 'parse', parse)
    return state


# ── the window ───────────────────────────────────────────────────────────────

def test_window_exposes_the_public_names():
    for name in ('analyze_report', 'summaries_for', 'save_comparison', 'ai_slot', 'phase2_llm'):
        assert callable(getattr(analysis, name))
    assert asyncio.iscoroutinefunction(analysis.analyze_report)


# ── ported: reuse, 422, 409 ──────────────────────────────────────────────────

async def test_selected_cached_reports_only_and_non_company_rejection(db, ai):
    db.add_summary(1)
    db.add_summary(3)
    db.add_summary(2)
    results = await asyncio.gather(analysis.analyze_report(row(1)), analysis.analyze_report(row(3)))
    assert [summary['report_id'] for summary, _ in results] == [1, 3]
    assert all(reused for _, reused in results)
    # only the selected reports' summaries are read
    assert sorted(q.ids for q in db.of('select')) == [[1], [3]]
    with pytest.raises(HTTPException) as exc:
        await analysis.analyze_report(row(9, report_type='산업'))
    assert exc.value.status_code == 422
    assert exc.value.detail == NOT_COMPANY
    assert not service._analyzing
    assert ai.calls == [] and db.of('upsert') == []


@pytest.mark.parametrize('report_type', ['산업', '섹터', 'IR자료', '전략·시황', '기타', None])
async def test_only_single_company_reports_are_analyzed(report_type):
    # no DB settings, no model key: the 422 comes first
    with pytest.raises(HTTPException) as exc:
        await analysis.analyze_report(row(9, report_type=report_type))
    assert (exc.value.status_code, exc.value.detail) == (422, NOT_COMPANY)
    assert not service._analyzing


async def test_concurrent_request_for_the_same_report_is_409(db):
    db.add_summary(1)
    async with analysis.ai_slot(), analysis.ai_slot():  # both AI slots busy
        first = asyncio.create_task(analysis.analyze_report(row(1)))
        await until(lambda: 1 in service._analyzing)
        with pytest.raises(HTTPException) as exc:
            # refused at once, without waiting for a slot (a timeout here = no 409)
            await asyncio.wait_for(analysis.analyze_report(row(1)), LIMIT_S)
        assert (exc.value.status_code, exc.value.detail) == (409, IN_FLIGHT)
        # a different report is not refused, it waits for a slot
        other = asyncio.create_task(analysis.analyze_report(row(2, report_type='산업')))
        await until(lambda: 2 in service._analyzing)
        assert not first.done() and not other.done()
    summary, reused = await asyncio.wait_for(first, LIMIT_S)
    assert reused is True and summary['report_id'] == 1
    with pytest.raises(HTTPException):
        await asyncio.wait_for(other, LIMIT_S)
    assert not service._analyzing
    # once finished, the same report can be requested again
    assert (await asyncio.wait_for(analysis.analyze_report(row(1)), LIMIT_S))[1] is True


# ── model key only right before the AI call ──────────────────────────────────

async def test_reuse_and_summaries_for_work_without_a_model_key(db, ai):
    saved = db.add_summary(7, financial_details={'metrics': [], 'unsupported_numeric_values': 0})
    summary, reused = await analysis.analyze_report(row(7, file_path='missing.pdf'))
    assert reused is True and summary == saved
    assert analysis.summaries_for([7, 8]) == {7: saved}
    assert ai.calls == [] and db.of('upsert') == []


@pytest.mark.parametrize('model,reason', [
    ('gpt-6-luna', 'OPENAI_API_KEY가 설정되지 않았습니다. .env에 추가 후 분석 다시 시도하세요.'),
    ('claude-haiku-5-5', 'ANTHROPIC_API_KEY가 설정되지 않았습니다. .env에 추가 후 분석 다시 시도하세요.'),
    ('codex:gpt-6-luna', 'codex CLI를 찾을 수 없습니다. 설치 후 `codex login`으로 로그인하세요.'),
])
async def test_missing_model_key_is_not_ready_right_before_the_ai_call(db, ai, monkeypatch,
                                                                       model, reason):
    monkeypatch.setenv('LLM_MODEL_PHASE2', model)
    db.add_summary(5, financial_details=None)  # an older summary without details: not reused
    # The PDF does not exist either: NotReady (not 404) shows the key check comes first.
    with pytest.raises(NotReady) as exc:
        await analysis.analyze_report(row(5, file_path='missing.pdf'))
    assert (exc.value.area, exc.value.reason) == ('분석', reason)
    assert str(exc.value) == f'분석 기능을 지금 쓸 수 없습니다: {reason}'
    assert [q.ids for q in db.of('select')] == [[5]]  # the reuse check ran before
    assert ai.calls == [] and db.of('upsert') == []
    assert not service._analyzing


async def test_key_added_to_env_file_counts_on_the_next_request(db, ai, storage, env_file):
    name = write_pdf(storage, 'r.pdf', ['Target 85,000 OP 2026F 1,234', 'Revision OP 1,100 to 1,234'])
    with pytest.raises(NotReady):
        await analysis.analyze_report(row(4, file_path=name))
    env_file.write_text('OPENAI_API_KEY=sk-from-env-file\n', encoding='utf-8')
    summary, reused = await analysis.analyze_report(row(4, file_path=name))
    assert reused is False and summary['report_id'] == 4 and len(ai.calls) == 1


# ── fresh analysis ───────────────────────────────────────────────────────────

async def test_fresh_analysis_saves_todays_payload_fields(db, ai, storage, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test')  # default model gpt-6-luna
    name = write_pdf(storage, 'r.pdf', ['Target 85,000 OP 2026F 1,234', 'Revision OP 1,100 to 1,234'])

    summary, reused = await analysis.analyze_report(row(11, file_path=name))

    assert reused is False
    grounded = FinancialDetails.model_validate({**FD_INPUT, 'metrics': FD_INPUT['metrics'][:1]})
    expected = {
        'financial_details': grounded.model_dump() | {'unsupported_numeric_values': 1},
        'target_price_new': 85000, 'target_price_old': 70000, 'target_price_dir': '상향',
        'recommendation': '매수', 'recommendation_dir': '유지', 'one_line_summary': '목표주가 상향',
        'positive_points': ['수주 증가'], 'risk_points': [], 'target_price_raw': '85,000원',
        'recommendation_raw': 'Buy', 'source_pages': [1, 2], 'extraction_confidence': 'high',
        'report_id': 11, 'input_truncated': False, 'input_pages_used': 2, 'input_total_pages': 2,
        'summary_version': 'llm-summary@1.0', 'llm_model': 'gpt-6-luna',
        'llm_tokens_input': 1234, 'llm_tokens_output': 567,
        'prev_report_id': None, 'prev_match_type': None, 'diff_narrative': None,
        'comparison_details': None,
    }
    assert summary == expected
    assert list(summary) == list(expected)  # same keys, same order
    upserts = db.of('upsert')
    assert [(q.payload, q.on_conflict) for q in upserts] == [(expected, 'report_id')]

    (call,) = ai.calls
    assert call['model'] == 'gpt-6-luna'
    assert call['schema'] is ExtractionResult and call['constrained'] is False
    user = call['user']
    metadata = user[user.index('<report_metadata>\n') + 18:user.index('\n</report_metadata>')]
    assert json.loads(metadata) == {'publisher': 'KB', 'stock_codes': ['016360'],
                                    'published_at': '2026-05-11', 'title': 'report 11'}
    pages = user[user.index('<report_pages>\n') + 15:user.index('\n</report_pages>')]
    assert pages.startswith('--- Page 1 ---\nTarget 85,000 OP 2026F 1,234')
    assert '\n--- Page 2 ---\nRevision OP 1,100 to 1,234' in pages
    assert not service._analyzing


async def test_settings_flow_into_the_call_and_the_saved_summary(db, ai, storage, monkeypatch):
    monkeypatch.setenv('LLM_MODEL_PHASE2', 'claude-haiku-5-5')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-test')
    monkeypatch.setenv('PHASE2_SUMMARY_VERSION', 'llm-summary@test')
    monkeypatch.setenv('PHASE2_PER_REPORT_TIMEOUT_S', '42')
    first = '--- Page 1 ---\nTarget 85,000 OP 2026F 1,234\n\n'
    monkeypatch.setenv('PHASE2_MAX_INPUT_TOKENS', str(len(first) // 3 + 1))  # page 2 is left out
    name = write_pdf(storage, 'r.pdf', ['Target 85,000 OP 2026F 1,234', 'Revision OP 1,100 to 1,234'])
    seen = {}
    real_extract = service.extract_one

    async def spy(**kwargs):
        seen.update(kwargs)
        return await real_extract(**kwargs)

    monkeypatch.setattr(service, 'extract_one', spy)

    summary, reused = await analysis.analyze_report(row(12, file_path=name))

    assert (seen['model'], seen['timeout_s']) == ('claude-haiku-5-5', 42)
    assert seen['pages_text'] == first
    assert (summary['input_truncated'], summary['input_pages_used'],
            summary['input_total_pages']) == (True, 1, 2)
    assert (summary['summary_version'], summary['llm_model']) == ('llm-summary@test',
                                                                  'claude-haiku-5-5')
    # page 2 is not in the text, so its previous value is not supported
    assert summary['financial_details']['metrics'][0]['previous_value'] is None
    assert db.of('select')[0].filters['summary_version'] == 'llm-summary@test'


async def test_summary_without_financial_details_is_analyzed_again(db, ai, storage, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test')
    db.add_summary(6, financial_details=None, diff_narrative='old pair', prev_report_id=2,
                   prev_match_type='same_publisher')
    name = write_pdf(storage, 'r.pdf', ['Target 85,000 OP 2026F 1,234'])
    summary, reused = await analysis.analyze_report(row(6, file_path=name))
    assert reused is False and len(ai.calls) == 1
    # re-analysis clears the saved comparison of this report
    assert db.rows[6]['diff_narrative'] is None and db.rows[6]['prev_report_id'] is None


@pytest.mark.parametrize('file_path,status,detail', [
    ('../outside.pdf', 404, 'PDF를 찾을 수 없습니다.'),
    ('notes.txt', 404, 'PDF를 찾을 수 없습니다.'),
    ('missing.pdf', 404, '로컬 PDF 파일이 없습니다. 수집 상태를 확인해 주세요.'),
    ('broken.pdf', 422, NO_TEXT),
])
async def test_pdf_problems(db, ai, storage, monkeypatch, file_path, status, detail):
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test')
    (storage.parent / 'outside.pdf').write_bytes(b'%PDF-1.4')
    (storage / 'notes.txt').write_text('x', encoding='utf-8')
    (storage / 'broken.pdf').write_bytes(b'not a pdf')
    with pytest.raises(HTTPException) as exc:
        await analysis.analyze_report(row(13, file_path=file_path))
    assert (exc.value.status_code, exc.value.detail) == (status, detail)
    assert ai.calls == [] and db.of('upsert') == []
    assert not service._analyzing


async def test_no_page_fits_the_cap_is_no_text(db, ai, storage, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test')
    monkeypatch.setenv('PHASE2_MAX_INPUT_TOKENS', '1')
    name = write_pdf(storage, 'r.pdf', ['Target 85,000'])
    with pytest.raises(HTTPException) as exc:
        await analysis.analyze_report(row(14, file_path=name))
    assert (exc.value.status_code, exc.value.detail) == (422, NO_TEXT)
    assert ai.calls == []


async def test_ai_failure_is_that_requests_error(db, ai, storage, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test')
    saved = db.add_summary(15, financial_details=None)
    name = write_pdf(storage, 'r.pdf', ['Target 85,000'])
    ai.error = RuntimeError('provider rejected the key')
    with pytest.raises(RuntimeError, match='provider rejected'):
        await analysis.analyze_report(row(15, file_path=name))
    assert db.rows[15] == saved and db.of('upsert') == []
    assert not service._analyzing


async def test_refusal_is_an_error_and_nothing_is_saved(db, ai, storage, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test')
    name = write_pdf(storage, 'r.pdf', ['Target 85,000'])
    ai.reply = None
    with pytest.raises(RuntimeError, match='거부'):
        await analysis.analyze_report(row(16, file_path=name))
    assert db.of('upsert') == []


# ── AI slot: 2 at a time, shared with compare ────────────────────────────────

async def test_ai_slot_allows_two_calls_at_a_time():
    inside = peak = 0
    entered = []
    release = asyncio.Event()

    async def call(i):
        nonlocal inside, peak
        async with analysis.ai_slot():
            inside += 1
            peak = max(peak, inside)
            entered.append(i)
            await release.wait()
            inside -= 1

    tasks = [asyncio.create_task(call(i)) for i in range(5)]
    await until(lambda: len(entered) == 2)
    for _ in range(20):
        await asyncio.sleep(0)
    assert len(entered) == 2  # the other three wait for a free slot
    release.set()
    await asyncio.wait_for(asyncio.gather(*tasks), LIMIT_S)
    assert peak == 2 and sorted(entered) == [0, 1, 2, 3, 4]


async def test_ai_slot_is_released_on_error():
    for _ in range(3):
        with pytest.raises(ValueError):
            async with analysis.ai_slot():
                raise ValueError('boom')
    async with asyncio.timeout(LIMIT_S):  # a leaked slot would block here
        async with analysis.ai_slot(), analysis.ai_slot():
            pass  # both slots are free again


async def test_analysis_waits_while_two_ai_calls_hold_the_slots(db, ai, storage, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test')
    name = write_pdf(storage, 'r.pdf', ['Target 85,000 OP 2026F 1,234'])
    release = asyncio.Event()
    holding = []

    async def other_ai_call():  # e.g. compare writing a narrative
        async with analysis.ai_slot():
            holding.append(1)
            await release.wait()

    holders = [asyncio.create_task(other_ai_call()) for _ in range(2)]
    await until(lambda: len(holding) == 2)
    task = asyncio.create_task(analysis.analyze_report(row(17, file_path=name)))
    await until(lambda: 17 in service._analyzing)
    for _ in range(20):
        await asyncio.sleep(0)
    # like before, the whole analysis after the 409 check runs inside a slot
    assert not task.done() and ai.calls == [] and db.executed == []
    release.set()
    summary, reused = await asyncio.wait_for(task, LIMIT_S)
    assert reused is False and len(ai.calls) == 1
    await asyncio.wait_for(asyncio.gather(*holders), LIMIT_S)


async def test_two_analyses_hold_both_slots(db, ai, storage, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-test')
    name = write_pdf(storage, 'r.pdf', ['Target 85,000 OP 2026F 1,234'])
    ai.gate = asyncio.Event()
    running = [asyncio.create_task(analysis.analyze_report(row(rid, file_path=name)))
               for rid in (21, 22)]
    await until(lambda: len(ai.calls) == 2)
    entered = []

    async def compare_like_call():
        async with analysis.ai_slot():
            entered.append(1)

    waiting = asyncio.create_task(compare_like_call())
    for _ in range(20):
        await asyncio.sleep(0)
    assert entered == []  # waits while two analyses are calling the AI
    ai.gate.set()
    await asyncio.wait_for(asyncio.gather(*running, waiting), LIMIT_S)
    assert entered == [1]


def test_ai_slot_works_in_each_event_loop():
    # Created lazily for the running loop: contention in a second loop must not
    # fail with "bound to a different event loop".
    async def scenario():
        inside = peak = 0

        async def call():
            nonlocal inside, peak
            async with analysis.ai_slot():
                inside += 1
                peak = max(peak, inside)
                await asyncio.sleep(0.01)
                inside -= 1

        await asyncio.gather(*(call() for _ in range(4)))
        return peak

    assert asyncio.run(scenario()) == 2
    assert asyncio.run(scenario()) == 2


# ── summaries_for / save_comparison / DB readiness ───────────────────────────

def test_summaries_for_reads_the_active_version_in_batches_of_100(db, monkeypatch):
    for rid in range(1, 251):
        db.add_summary(rid)
    db.add_summary(999, summary_version='llm-summary@0.9')

    result = analysis.summaries_for(list(range(1, 251)) + [999])

    assert sorted(result) == list(range(1, 251))  # another version is not shown
    assert result[1]['one_line_summary'] == 'saved 1'
    assert [len(q.ids) for q in db.of('select')] == [100, 100, 51]
    assert {q.filters['summary_version'] for q in db.of('select')} == {'llm-summary@1.0'}

    monkeypatch.setenv('PHASE2_SUMMARY_VERSION', 'llm-summary@0.9')
    assert list(analysis.summaries_for([999, 1])) == [999]


def test_summaries_for_nothing_needs_no_db():
    assert analysis.summaries_for([]) == {}


def test_save_comparison_writes_the_four_comparison_fields(db):
    db.add_summary(3, one_line_summary='kept')
    details = {'metrics': [{'previous': 100, 'current': 120}], 'previous_publisher': 'KB',
               'previous_published_at': '2026-01-01'}
    assert analysis.save_comparison(3, 2, 'same_publisher', '전망 상향', details) is None
    (update,) = db.of('update')
    assert update.payload == {'prev_report_id': 2, 'prev_match_type': 'same_publisher',
                              'diff_narrative': '전망 상향', 'comparison_details': details}
    assert update.filters == {'report_id': 3}
    assert db.rows[3]['one_line_summary'] == 'kept'


def test_missing_db_settings_is_not_ready_until_fixed(monkeypatch):
    fake = FakeSupabase()
    monkeypatch.setattr(core_db, 'supabase_client', lambda url, key: fake)
    for call in (lambda: analysis.summaries_for([1]),
                 lambda: analysis.save_comparison(3, 2, 'same_publisher', 'x', {})):
        with pytest.raises(NotReady) as exc:
            call()
        assert (exc.value.area, exc.value.reason) == ('분석', DB_MISSING)
    # fixed without a restart: the next call prepares again
    monkeypatch.setenv('SUPABASE_URL', 'https://example.supabase.test')
    monkeypatch.setenv('SUPABASE_SERVICE_KEY', 'service-key')
    assert analysis.summaries_for([1]) == {}


def test_db_preparation_reads_env_file_once(env_file, monkeypatch):
    made = []

    def supabase_client(url, key):
        made.append((url, key))
        return FakeSupabase()

    monkeypatch.setattr(core_db, 'supabase_client', supabase_client)
    env_file.write_text('SUPABASE_URL=https://from-env-file.test\nSUPABASE_SERVICE_KEY=k\n',
                        encoding='utf-8')
    assert analysis.summaries_for([1]) == {}
    analysis.summaries_for([2])
    analysis.save_comparison(2, 1, 'cross_publisher', None, None)
    assert made == [('https://from-env-file.test', 'k')]  # prepared once, then reused
