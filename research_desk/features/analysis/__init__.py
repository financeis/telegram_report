"""Report analysis: one selected report's financial analysis, and the report_summaries table.

No web routes. The reports feature reads the row and calls ``analyze_report``;
compare uses ``ai_slot``, ``phase2_llm`` and ``save_comparison``. Public names:

- ``analyze_report(row)`` (async) → ``(saved summary, reused)``: checks in
  flight (409) → 단일종목 (422) → reuse of a saved summary with
  financial_details → model key / codex CLI → PDF (404 / 422) → AI call → save.
- ``summaries_for(ids)`` → ``{report_id: summary}``, active summary version only.
- ``save_comparison(report_id, prev_report_id, match_type, narrative, comparison_details)``.
- ``ai_slot()``: async context manager; at most 2 AI calls at once in the web
  server, shared by analysis and compare.
- ``phase2_llm()`` → ``(client, model, timeout_s)``; re-reads ``.env`` and raises
  ``NotReady("분석", …)`` when the model's API key or the codex CLI is missing.
"""
from .service import ai_slot, analyze_report, phase2_llm, save_comparison, summaries_for
