"""Compare work: two reports of the same company side by side, and their narrative (spec §6, §9.6).

Both addresses read the two reports through ``reports.get_report`` (the public shape with the
saved summary): 404 ``기업 보고서를 찾을 수 없습니다.`` for a report that is not in scope, and
``NotReady("리포트", …)`` without DB settings, so the reports feature's name shows (spec §9.9).
Then ``logic.comparison`` checks the pair (422) and lays it out, the earlier report on the left.

The narrative (``POST /api/compare/analyze``):

1. both reports need a saved analysis, else 422 ``선택한 두 보고서를 먼저 분석해 주세요.``;
2. a narrative saved for exactly this pair comes back as it is: no AI call, no key needed;
3. otherwise, inside ``analysis.ai_slot()`` (the web server's 2 AI calls, shared with analysis):
   ``analysis.phase2_llm()`` gives the AI client, model and per-call time limit (``NotReady("분석",
   …)`` when the key or the codex CLI is missing); ``diff_one`` writes the narrative with the
   earlier report as the previous one, ``same_publisher`` or ``cross_publisher``, and ``미상`` for
   an unknown publisher; ``analysis.save_comparison`` stores it on the later report's summary with
   ``comparison_details`` {metrics, previous_publisher, previous_published_at}. A later report keeps
   one narrative: another pair replaces it.

Compare reads no Phase 2 setting and no table itself; the reads and the save run in worker threads.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import HTTPException

from research_desk.features import analysis, reports

from .llm import diff_one
from .logic import comparison

NOT_ANALYZED = '선택한 두 보고서를 먼저 분석해 주세요.'
UNKNOWN_PUBLISHER = '미상'


def compare_reports(left_id: int, right_id: int) -> dict[str, Any]:
    """``GET /api/compare``: the comparison of two in-scope reports. Never calls the AI."""
    return comparison(reports.get_report(left_id), reports.get_report(right_id))


async def analyze_comparison(left_id: int, right_id: int) -> dict[str, Any]:
    """``POST /api/compare/analyze``: the comparison plus the narrative of this exact pair."""
    left = await asyncio.to_thread(reports.get_report, left_id)
    right = await asyncio.to_thread(reports.get_report, right_id)
    result = comparison(left, right)
    left, right = result['left'], result['right']
    if not left['summary'] or not right['summary']:
        raise HTTPException(422, NOT_ANALYZED)
    if result['narrative']:
        return result
    match = 'same_publisher' if result['same_publisher'] else 'cross_publisher'
    async with analysis.ai_slot():
        llm, model, timeout_s = analysis.phase2_llm()
        async with llm as client:
            diff, _, _ = await diff_one(
                client=client, model=model, prev_summary=left['summary'],
                curr_summary=right['summary'], prev_match_type=match,
                prev_report_id=left['id'],
                prev_publisher=left['publisher'] or UNKNOWN_PUBLISHER,
                curr_publisher=right['publisher'] or UNKNOWN_PUBLISHER,
                timeout_s=timeout_s,
            )
        details = {'metrics': result['metrics'], 'previous_publisher': left['publisher'],
                   'previous_published_at': left['published_at']}
        await asyncio.to_thread(analysis.save_comparison, right['id'], left['id'], match,
                                diff.diff_narrative, details)
    return result | {'narrative': diff.diff_narrative}
