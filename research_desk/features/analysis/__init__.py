"""Report analysis: one selected report's financial analysis, and the report_summaries table.

No web routes. The reports feature reads the row and calls ``analyze_report``;
compare uses ``ai_slot``, ``phase2_llm`` and ``save_comparison``.
"""
