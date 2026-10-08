"""Shared infrastructure: settings, DB connections, AI calls, PDF files.

Knows no business concept (reports, stocks, tagging) and imports no other
``research_desk`` area. Import the modules directly, e.g.
``from research_desk.core import settings`` or
``from research_desk.core.llm import LLMClient``.
"""
