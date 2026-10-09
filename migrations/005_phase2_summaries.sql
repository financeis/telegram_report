-- migrations/005_phase2_summaries.sql
--
-- Phase 2: lazy on-demand LLM 요약 결과를 영구 캐시하는 별도 테이블.
-- 1:1 with reports.id (단일종목만 처리되지만 PK는 report_id로 단순화).
-- 설계 문서: 2026-05의 옛 설계 노트(git에 없음). 지금 규칙은 docs/business-rules.md

BEGIN;

CREATE TABLE IF NOT EXISTS report_summaries (
  report_id           bigint PRIMARY KEY REFERENCES reports(id) ON DELETE CASCADE,

  -- per-report 구조화 추출
  target_price_new    integer,
  target_price_old    integer,
  target_price_dir    text NOT NULL,
  recommendation      text NOT NULL,
  recommendation_dir  text NOT NULL,
  one_line_summary    text NOT NULL,
  positive_points     jsonb NOT NULL DEFAULT '[]'::jsonb,
  risk_points         jsonb NOT NULL DEFAULT '[]'::jsonb,

  -- evidence / 신뢰성
  target_price_raw      text,
  recommendation_raw    text,
  source_pages          integer[] NOT NULL DEFAULT '{}',
  extraction_confidence text NOT NULL,

  -- diff vs prev (Pass2에서 채움; NULL=Pass2 미시도, 'none'=시도했으나 prev 없음)
  prev_report_id      bigint REFERENCES reports(id) ON DELETE SET NULL,
  prev_match_type     text,
  diff_narrative      text,

  -- input audit
  input_truncated     boolean NOT NULL DEFAULT FALSE,
  input_pages_used    integer NOT NULL DEFAULT 0,
  input_total_pages   integer NOT NULL DEFAULT 0,

  -- audit / versioning
  summary_version     text NOT NULL,
  llm_model           text NOT NULL,
  llm_tokens_input    integer NOT NULL DEFAULT 0,
  llm_tokens_output   integer NOT NULL DEFAULT 0,
  generated_at        timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT chk_summary_target_price_dir
    CHECK (target_price_dir IN ('상향','불변','하향','신규','N/A')),
  CONSTRAINT chk_summary_recommendation
    CHECK (recommendation IN ('매수','중립','매도','N/A')),
  CONSTRAINT chk_summary_recommendation_dir
    CHECK (recommendation_dir IN ('유지','상향','하향','신규','N/A')),
  CONSTRAINT chk_summary_extraction_confidence
    CHECK (extraction_confidence IN ('high','medium','low')),
  CONSTRAINT chk_summary_prev_match_type
    CHECK (prev_match_type IS NULL OR
           prev_match_type IN ('same_publisher','cross_publisher','none')),
  CONSTRAINT chk_summary_source_pages_positive
    CHECK (1 <= ALL (source_pages))
);

-- RLS: 기존 reports/failed_attempts 관례와 일치 — anon/authenticated 거부, service_role만 우회.
ALTER TABLE report_summaries ENABLE ROW LEVEL SECURITY;

-- prev cascade는 reports 기존 인덱스(ix_reports_stocks_gin, ix_reports_publisher_pub) 활용.
-- report_id PK가 cache lookup·Pass2 JOIN 모두 커버. 새 인덱스 추가 없음.

COMMIT;
