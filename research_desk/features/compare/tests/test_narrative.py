"""The narrative of a pair (POST /api/compare/analyze) at the service level.

Ported from langgraph_tagger/workspace/tests/test_migration.py::
test_explicit_pair_narrative_reads_only_selected_reports.

New (spec §9.3, §9.5, §9.6, §9.9, §13 item 3):
- what the AI gets: the earlier report's summary as the previous one and the later report's as the
  current one, same_publisher / cross_publisher, 미상 for an unknown publisher, and the model and
  per-call time limit analysis.phase2_llm gives (compare reads no Phase 2 setting itself); the
  client is closed afterwards;
- what is saved, on the later report: prev_report_id, prev_match_type, the narrative and
  comparison_details {metrics, previous_publisher, previous_published_at}; one narrative per later
  report (another pair replaces it); a null narrative is saved and asked for again next time;
- the 2-call AI limit is analysis.ai_slot(), shared with analysis: while two calls hold both slots
  a new narrative waits, and only once it has a slot does it check the key and call the AI; three
  narratives at once run two at a time; the slot is freed after a failure, a refusal or a missing
  key; a saved narrative, 404 and 422 do not wait for a slot;
- a missing key or codex CLI → NotReady("분석", …) from the real analysis.phase2_llm, with nothing
  saved, while the comparison and a saved narrative still work; a key added to .env counts on the
  next request.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from research_desk.core.llm import LLMClient, StructuredResult
from research_desk.core.settings import NotReady
from research_desk.features import analysis, reports
from research_desk.features.compare import service
from research_desk.features.compare.prompts import render_diff_messages
from research_desk.features.compare.schemas import DiffResult

from .fakes import analysed, metric, never, tagged, until

LIMIT_S = 5  # bound on awaits that would hang if the behaviour under test broke
NOT_ANALYZED = '선택한 두 보고서를 먼저 분석해 주세요.'


def pair(world, narrative=None, earlier='KB', later='KB'):
    """Two analysed reports on 삼성증권: 1 (February) and 3 (May, maybe with a narrative for 1)."""
    world.add(tagged(1, '2026-02-09', publisher=earlier), analysed(1, 70000, metric(100)))
    saved = ({'prev_report_id': 1, 'prev_match_type': 'same_publisher', 'diff_narrative': narrative}
             if narrative else {})
    world.add(tagged(3, '2026-05-11', publisher=later), analysed(3, 85000, metric(120), **saved))


async def slots_are_free():
    async with asyncio.timeout(LIMIT_S):   # a leaked slot would block here
        async with analysis.ai_slot(), analysis.ai_slot():
            pass


# ── ported ───────────────────────────────────────────────────────────────────

async def test_explicit_pair_narrative_reads_only_selected_reports(monkeypatch):
    # the old service read with self.report and saved with update_diff(sb, report, prev, ...)
    client = AsyncMock()
    monkeypatch.setattr(analysis, 'phase2_llm', lambda: (client, 'claude-haiku-5-5', 90))
    generate = AsyncMock(return_value=(SimpleNamespace(diff_narrative='Selected pair only'), 10, 10))
    save = Mock()
    monkeypatch.setattr(service, 'diff_one', generate)
    monkeypatch.setattr(analysis, 'save_comparison', save)
    rows = {rid: {'id': rid, 'report_type': '단일종목', 'stock_codes': ['016360'],
                  'publisher': 'KB', 'published_at': date, 'summary': {'one_liner': str(rid)}}
            for rid, date in [(1, '2026-01-01'), (3, '2026-03-01')]}
    get_report = Mock(side_effect=lambda rid: rows[rid])
    monkeypatch.setattr(reports, 'get_report', get_report)
    result = await service.analyze_comparison(3, 1)
    assert result['narrative'] == 'Selected pair only'
    assert {c.args[0] for c in get_report.call_args_list} == {1, 3}
    assert generate.call_args.kwargs['prev_report_id'] == 1
    assert generate.call_args.kwargs['curr_summary'] == rows[3]['summary']
    assert save.call_args.args[:2] == (3, 1)


# ── what the AI gets ─────────────────────────────────────────────────────────

@pytest.mark.parametrize('left, right', [(1, 3), (3, 1)])
async def test_the_earlier_summary_is_the_previous_one(world, ai, left, right):
    pair(world)
    previous, current = world.public(1)['summary'], world.public(3)['summary']
    await service.analyze_comparison(left, right)
    system, user = render_diff_messages(previous, current, 'same_publisher', 1, 'KB', 'KB')
    assert ai.calls == [{'model': ai.model, 'system': system['content'], 'user': user['content'],
                         'schema': DiffResult, 'constrained': True}]
    (client,) = ai.clients
    assert client.entered and client.closed


@pytest.mark.parametrize('earlier, later, match, shown', [
    ('KB', 'KB', 'same_publisher', ('KB', 'KB')),
    ('KB', 'NH', 'cross_publisher', ('KB', 'NH')),
    (None, None, 'cross_publisher', ('미상', '미상')),
    ('KB', None, 'cross_publisher', ('KB', '미상')),
    ('', 'KB', 'cross_publisher', ('미상', 'KB')),
])
async def test_the_publishers_decide_the_framing(world, ai, earlier, later, match, shown):
    pair(world, earlier=earlier, later=later)
    previous, current = world.public(1)['summary'], world.public(3)['summary']
    result = await service.analyze_comparison(3, 1)
    system, user = render_diff_messages(previous, current, match, 1, *shown)
    (call,) = ai.calls
    assert (call['system'], call['user']) == (system['content'], user['content'])
    assert result['same_publisher'] is (match == 'same_publisher')
    ((_, _, saved_match, _, details),) = world.saved
    assert saved_match == match
    assert details['previous_publisher'] == earlier   # stored as it is, not 미상


async def test_the_model_and_time_limit_come_from_phase2_llm(world, ai, monkeypatch):
    # compare reads no Phase 2 setting itself: these must not reach the call
    monkeypatch.setenv('LLM_MODEL_PHASE2', 'claude-haiku-5-5')
    monkeypatch.setenv('PHASE2_PER_REPORT_TIMEOUT_S', '5')
    ai.model, ai.timeout_s = 'codex:gpt-6-luna', 77
    seen = {}
    real = service.diff_one

    async def spy(**kwargs):
        seen.update(kwargs)
        return await real(**kwargs)

    monkeypatch.setattr(service, 'diff_one', spy)
    pair(world)
    await service.analyze_comparison(1, 3)
    assert (seen['model'], seen['timeout_s']) == ('codex:gpt-6-luna', 77)
    assert seen['client'] is ai.clients[0]
    assert ai.calls[0]['model'] == 'codex:gpt-6-luna'


# ── what is saved ────────────────────────────────────────────────────────────

async def test_the_narrative_is_saved_on_the_later_report(world, ai):
    pair(world)
    expected = service.compare_reports(1, 3)
    result = await service.analyze_comparison(3, 1)
    assert result == expected | {'narrative': ai.narrative}
    details = {'metrics': expected['metrics'], 'previous_publisher': 'KB',
               'previous_published_at': '2026-02-09'}
    assert world.saved == [(3, 1, 'same_publisher', ai.narrative, details)]
    assert [(m['previous'], m['current']) for m in details['metrics']] == [(100, 120)]
    # only the later report's four comparison fields change
    assert world.summaries[3] == analysed(3, 85000, metric(120)) | {
        'prev_report_id': 1, 'prev_match_type': 'same_publisher', 'diff_narrative': ai.narrative,
        'comparison_details': details}
    assert world.summaries[1] == analysed(1, 70000, metric(100))


async def test_one_narrative_per_later_report(world, ai):
    pair(world, narrative='1→3 해석')
    world.add(tagged(2, '2026-03-02', publisher='NH'), analysed(2, 80000))
    ai.narrative = '2→3 해석'
    result = await service.analyze_comparison(2, 3)   # another pair for the same later report
    assert result['narrative'] == '2→3 해석' and len(ai.calls) == 1
    assert world.saved[0][:4] == (3, 2, 'cross_publisher', '2→3 해석')
    # the later report keeps one narrative: the one for 1→3 is gone
    assert service.compare_reports(1, 3)['narrative'] is None
    assert service.compare_reports(3, 2)['narrative'] == '2→3 해석'


async def test_a_null_narrative_is_saved_and_asked_for_again(world, ai):
    pair(world)
    ai.narrative = None
    assert (await service.analyze_comparison(1, 3))['narrative'] is None
    assert world.saved[0][:4] == (3, 1, 'same_publisher', None)
    assert (await service.analyze_comparison(1, 3))['narrative'] is None
    assert ai.prepared == 2 and len(ai.calls) == 2


async def test_both_analyses_are_needed_before_any_key_check(world, ai):
    pair(world)
    del world.summaries[3]
    with pytest.raises(HTTPException) as exc:
        await service.analyze_comparison(1, 3)
    assert (exc.value.status_code, exc.value.detail) == (422, NOT_ANALYZED)
    assert ai.prepared == 0 and world.saved == []


# ── the shared 2-call AI limit ───────────────────────────────────────────────

async def test_a_new_narrative_waits_while_two_analyses_hold_the_slots(world, ai):
    pair(world)
    release = asyncio.Event()
    holding = []

    async def analysis_ai_call():   # what analysis.analyze_report does around its AI call
        async with analysis.ai_slot():
            holding.append(1)
            await release.wait()

    holders = [asyncio.create_task(analysis_ai_call()) for _ in range(2)]
    await until(lambda: len(holding) == 2)
    task = asyncio.create_task(service.analyze_comparison(1, 3))
    await until(lambda: world.reads == [1, 3])   # the reports are read without a slot
    # no free slot: neither the key check (phase2_llm) nor the AI call happens
    await never(lambda: ai.prepared or ai.calls or task.done())
    release.set()
    result = await asyncio.wait_for(task, LIMIT_S)
    assert result['narrative'] == ai.narrative
    assert ai.prepared == 1 and len(ai.calls) == 1
    await asyncio.wait_for(asyncio.gather(*holders), LIMIT_S)


async def test_three_narratives_at_once_run_two_at_a_time(world, ai):
    pair(world)
    for rid, day in ((5, '2026-06-01'), (7, '2026-07-01')):
        world.add(tagged(rid, day), analysed(rid, 90000))
    ai.gate = asyncio.Event()
    tasks = [asyncio.create_task(service.analyze_comparison(1, rid)) for rid in (3, 5, 7)]
    await until(lambda: len(ai.calls) == 2)
    await never(lambda: len(ai.calls) > 2)
    assert ai.prepared == 2   # the third one waits for a slot before checking the key
    ai.gate.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks), LIMIT_S)
    assert [r['narrative'] for r in results] == [ai.narrative] * 3
    assert ai.peak == 2 and len(ai.calls) == 3
    await slots_are_free()


async def test_the_slot_is_freed_after_a_failure(world, ai):
    pair(world)
    ai.error = RuntimeError('provider rejected the key')
    for _ in range(3):
        with pytest.raises(RuntimeError, match='provider rejected'):
            await service.analyze_comparison(1, 3)
    assert world.saved == []
    assert all(client.closed for client in ai.clients)
    await slots_are_free()


async def test_a_refusal_is_an_error_and_nothing_is_saved(world, ai):
    pair(world)
    ai.refusal = 'refusal (cyber): x'
    with pytest.raises(RuntimeError, match='거부'):
        await service.analyze_comparison(1, 3)
    assert world.saved == []
    await slots_are_free()


async def test_a_saved_narrative_404_and_422_do_not_wait_for_a_slot(world):
    pair(world, narrative='저장된 해석')
    world.add(tagged(5, '2026-04-01', codes=('005930',)), analysed(5, 1000))
    async with analysis.ai_slot(), analysis.ai_slot():   # both slots busy
        result = await asyncio.wait_for(service.analyze_comparison(3, 1), LIMIT_S)
        assert result['narrative'] == '저장된 해석'
        for left, right, status in ((1, 9, 404), (1, 5, 422)):
            with pytest.raises(HTTPException) as exc:
                await asyncio.wait_for(service.analyze_comparison(left, right), LIMIT_S)
            assert exc.value.status_code == status


# ── a missing key or codex CLI (spec §9.9) ───────────────────────────────────

@pytest.mark.parametrize('model, reason', [
    (None, 'OPENAI_API_KEY가 설정되지 않았습니다. .env에 추가 후 분석 다시 시도하세요.'),
    ('claude-haiku-5-5', 'ANTHROPIC_API_KEY가 설정되지 않았습니다. .env에 추가 후 분석 다시 시도하세요.'),
    ('codex:gpt-6-luna', 'codex CLI를 찾을 수 없습니다. 설치 후 `codex login`으로 로그인하세요.'),
], ids=['default model', 'claude', 'codex'])
async def test_a_missing_key_is_analysis_not_ready(world, monkeypatch, model, reason):
    # the real analysis.phase2_llm, with no key and no codex CLI
    if model:
        monkeypatch.setenv('LLM_MODEL_PHASE2', model)
    pair(world)
    with pytest.raises(NotReady) as exc:
        await service.analyze_comparison(1, 3)
    assert (exc.value.area, exc.value.reason) == ('분석', reason)
    assert str(exc.value) == f'분석 기능을 지금 쓸 수 없습니다: {reason}'
    assert world.saved == []
    await slots_are_free()


async def test_without_a_key_everything_but_a_new_narrative_works(world):
    pair(world, narrative='저장된 해석')
    world.add(tagged(2, '2026-03-02'))   # not analysed yet
    assert service.compare_reports(1, 3)['narrative'] == '저장된 해석'
    assert (await service.analyze_comparison(1, 3))['narrative'] == '저장된 해석'
    with pytest.raises(HTTPException) as exc:
        await service.analyze_comparison(2, 3)
    assert (exc.value.status_code, exc.value.detail) == (422, NOT_ANALYZED)
    world.summaries[2] = analysed(2, 80000)
    with pytest.raises(NotReady):   # only now, right before an AI call
        await service.analyze_comparison(2, 3)
    assert world.saved == []


async def test_a_key_added_to_env_counts_on_the_next_request(world, env_file, monkeypatch):
    pair(world)
    with pytest.raises(NotReady):
        await service.analyze_comparison(1, 3)
    calls = []

    async def parse(self, **kwargs):
        calls.append(kwargs)
        return StructuredResult(parsed=DiffResult(diff_narrative='키를 넣은 뒤 작성'))

    monkeypatch.setattr(LLMClient, 'parse', parse)
    env_file.write_text('OPENAI_API_KEY=sk-from-env-file\n', encoding='utf-8')
    result = await service.analyze_comparison(1, 3)
    assert result['narrative'] == '키를 넣은 뒤 작성'
    assert [c['model'] for c in calls] == ['gpt-6-luna']
    assert world.saved[0][:4] == (3, 1, 'same_publisher', '키를 넣은 뒤 작성')
