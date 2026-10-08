"""Compare (두 보고서 비교): two reports of the same company side by side, and their narrative.

Public: ``router`` — ``GET /api/compare?left=&right=`` and ``POST /api/compare/analyze``
(spec §6, §9.6).

Compare reads reports only through ``reports.get_report``. For a new narrative it takes one of the
web server's 2 AI slots (``analysis.ai_slot``, shared with analysis), gets the AI connection,
model and time limit from ``analysis.phase2_llm`` and saves through ``analysis.save_comparison``;
it reads no Phase 2 setting and no table itself.
"""
from .router import router
