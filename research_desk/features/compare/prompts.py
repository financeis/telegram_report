"""The compare AI prompt: the narrative of two reports on the same company.

Two framings (spec §9.6): from the **same publisher** it is that desk's revision; from **different
publishers** it is a comparison between two desks' views, never a revision or a market consensus.
The system prompt keeps the defensive line "no previous summary → diff_narrative=null".

Moved unchanged from the old analytics/llm_summary/prompts.py (the extraction prompt went to the
analysis feature). The prompt text lives here (single source of truth).
"""
from __future__ import annotations

import json
from typing import Any, Literal


_DIFF_SAME_PUB_TASK = """Compare two equity research reports from the **same publisher** —
they are sequential coverage by the same desk. Write a Korean narrative explaining
what changed in their view:
- target price change
- investment rating change
- earnings estimate direction
- key product/service demand change
- margin/cost assumption change
- macro or industry environment change
- valuation method or target multiple change
- newly emphasized risks or removed risks
Use financial_details to discuss same-period earnings estimates, valuation
assumptions, explicit rating labels, investment mechanisms and catalysts.
Missing mention in a later report is not proof that an earlier thesis was withdrawn.
"""

_DIFF_CROSS_PUB_TASK = """Compare two equity research reports from **different publishers** —
previous: {prev_publisher}, current: {curr_publisher}.
This is NOT a revision by the same analyst — it is a **comparison between two desks'
views**. Write a Korean narrative comparing how they differ in:
- target price level
- investment rating
- earnings outlook
- key strengths each emphasizes
- key risks each emphasizes
Use financial_details when available. Different fiscal periods, units, accounting
bases or scenarios are not directly comparable. Label this as disagreement between
desks, never as an analyst revision or a market-wide consensus.
Use language like "{prev_publisher}은 ... {curr_publisher}은 ..." or "이전 {prev_publisher} 리포트에선 ..., 이번 {curr_publisher} 리포트는 ...".
DO NOT use language implying the same analyst revised their view.
"""


_DIFF_SYSTEM_TEMPLATE = """You are an equity research report comparison engine.

Use only the provided current and previous data. Do not use outside knowledge.

Return only a JSON object. No markdown. No commentary.

{task_block}

If there is no previous summary in input, return diff_narrative=null.

<style_rules>
- 2 to 4 Korean sentences.
- Be specific and factual.
- Prefer concrete changes over vague language.
- Do not say "크게 변화했다" unless data supports it.
- Do not invent numbers.
- Compare only facts available in both inputs. If older financial_details are
  absent, do not infer their estimates, valuation drivers or catalysts.
- Prefer exact matching fiscal periods; never treat a FY rollover as an upgrade.
</style_rules>
"""


def render_diff_messages(
    prev_summary: dict[str, Any],
    curr_summary: dict[str, Any],
    prev_match_type: Literal['same_publisher', 'cross_publisher'],
    prev_report_id: int,
    prev_publisher: str,
    curr_publisher: str,
) -> list[dict[str, str]]:
    if prev_match_type == 'same_publisher':
        task_block = _DIFF_SAME_PUB_TASK
    else:
        task_block = _DIFF_CROSS_PUB_TASK.format(
            prev_publisher=prev_publisher,
            curr_publisher=curr_publisher,
        )
    system_msg = _DIFF_SYSTEM_TEMPLATE.format(task_block=task_block)
    comparison_ctx = {
        'prev_report_id': prev_report_id,
        'prev_match_type': prev_match_type,
        'prev_publisher': prev_publisher,
        'curr_publisher': curr_publisher,
    }
    user_payload = (
        f"<comparison_context>\n{json.dumps(comparison_ctx, ensure_ascii=False, indent=2)}\n</comparison_context>\n\n"
        f"<previous_summary>\n{json.dumps(prev_summary, ensure_ascii=False, indent=2)}\n</previous_summary>\n\n"
        f"<current_summary>\n{json.dumps(curr_summary, ensure_ascii=False, indent=2)}\n</current_summary>\n\n"
        # v1에선 발췌 빈 채로 (refinement #4)
        f"<optional_previous_excerpt></optional_previous_excerpt>\n\n"
        f"<optional_current_excerpt></optional_current_excerpt>"
    )
    return [
        {'role': 'system', 'content': system_msg},
        {'role': 'user', 'content': user_payload},
    ]
