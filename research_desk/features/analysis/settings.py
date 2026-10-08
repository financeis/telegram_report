"""Analysis settings (spec §7): the Phase 2 model and its limits.

Read when called, never at import. The model name picks the provider (see
``research_desk.core.llm``); API keys are checked by ``phase2_llm()`` right
before an AI call, not here. ``PHASE2_MAX_CONCURRENT`` and ``SUPABASE_DB_URL``
are not read: the web server's AI concurrency is fixed at 2 (``ai_slot``) and
analysis reaches the DB over Supabase REST only.
"""
from __future__ import annotations

from dataclasses import dataclass

from research_desk.core import settings as core_settings

DEFAULT_MODEL = "gpt-6-luna"
DEFAULT_PER_REPORT_TIMEOUT_S = 180
DEFAULT_MAX_INPUT_TOKENS = 30000
DEFAULT_SUMMARY_VERSION = "llm-summary@1.0"


@dataclass(frozen=True)
class AnalysisSettings:
    llm_model: str             # LLM_MODEL_PHASE2 (legacy OPENAI_MODEL_PHASE2)
    per_report_timeout_s: int  # limit for each AI call
    max_input_tokens: int      # cap on the page text sent to the model
    summary_version: str       # stored with each summary; only this version is shown


def summary_version() -> str:
    """The active summary version (``PHASE2_SUMMARY_VERSION``)."""
    return core_settings.optional("PHASE2_SUMMARY_VERSION", DEFAULT_SUMMARY_VERSION)


def load_settings() -> AnalysisSettings:
    """Current values from the process environment (callers run ``load_env()`` first)."""
    return AnalysisSettings(
        llm_model=core_settings.model_name("PHASE2", DEFAULT_MODEL),
        per_report_timeout_s=core_settings.get_int("PHASE2_PER_REPORT_TIMEOUT_S",
                                                   DEFAULT_PER_REPORT_TIMEOUT_S),
        max_input_tokens=core_settings.get_int("PHASE2_MAX_INPUT_TOKENS",
                                               DEFAULT_MAX_INPUT_TOKENS),
        summary_version=summary_version(),
    )
