"""Pydantic schema for the compare AI call's structured output: ``DiffResult`` (narrative only).

Moved unchanged from the old analytics/llm_summary/schemas.py. The class has no docstring on
purpose: a docstring would become the schema's description and change what the model is sent.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class DiffResult(BaseModel):
    diff_narrative: Optional[str] = None
