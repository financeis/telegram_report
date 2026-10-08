"""Lazy config — LLM API 키는 첫 analyze 호출 시점에만 검증.

메타데이터 탭은 LLM 키 없이도 정상 동작해야 하므로 load 시 검증 X.
모델 이름이 provider를 정한다: claude-* → ANTHROPIC_API_KEY,
codex:* → 로컬 codex CLI 로그인, 그 외 → OPENAI_API_KEY.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

from langgraph_tagger.llm_provider import LLMClient, api_key_env


@dataclass(frozen=True)
class LLMSummaryConfig:
    llm_model: str
    max_concurrent: int
    per_report_timeout_s: int
    max_input_tokens: int
    summary_version: str
    supabase_db_url: str
    openai_api_key: Optional[str]     # lazy: load 시점엔 None일 수 있음
    anthropic_api_key: Optional[str] = None


def load_llm_summary_config() -> LLMSummaryConfig:
    load_dotenv()

    db_url = os.getenv('SUPABASE_DB_URL')
    if not db_url:
        raise SystemExit("Missing required env var: SUPABASE_DB_URL")

    return LLMSummaryConfig(
        llm_model=(os.getenv('LLM_MODEL_PHASE2') or os.getenv('OPENAI_MODEL_PHASE2')
                   or 'gpt-6-luna'),
        max_concurrent=int(os.getenv('PHASE2_MAX_CONCURRENT', '2')),
        per_report_timeout_s=int(os.getenv('PHASE2_PER_REPORT_TIMEOUT_S', '180')),
        max_input_tokens=int(os.getenv('PHASE2_MAX_INPUT_TOKENS', '30000')),
        summary_version=os.getenv('PHASE2_SUMMARY_VERSION', 'llm-summary@1.0'),
        supabase_db_url=db_url,
        openai_api_key=os.getenv('OPENAI_API_KEY'),  # None OK at load time
        anthropic_api_key=os.getenv('ANTHROPIC_API_KEY'),
    )


def make_llm_client(cfg: LLMSummaryConfig) -> LLMClient:
    """첫 analyze 호출 시점에 호출. 모델에 맞는 키 없으면 RuntimeError."""
    client = LLMClient(openai_api_key=cfg.openai_api_key,
                       anthropic_api_key=cfg.anthropic_api_key)
    try:
        client.require_key(cfg.llm_model)
    except RuntimeError:
        env = api_key_env(cfg.llm_model)
        if env is None:
            raise RuntimeError(
                "codex CLI를 찾을 수 없습니다. 설치 후 `codex login`으로 로그인하세요."
            ) from None
        raise RuntimeError(
            f"{env}가 설정되지 않았습니다. .env에 추가 후 분석 다시 시도하세요."
        ) from None
    return client
