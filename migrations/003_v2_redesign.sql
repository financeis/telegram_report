-- migrations/003_v2_redesign.sql
--
-- v2 redesign: reset all v1-tagged data and apply v2 schema (6 report_types,
-- 5 OOS reasons, 4 publisher_types, raw audit columns, topics drop).
-- 설계 문서: 2026-05의 옛 설계 노트(git에 없음). 지금 규칙은 docs/business-rules.md

BEGIN;

-- ============================================================
-- 1. 기존 CHECK 제약 DROP (reset에서 NULL 허용 + 새 enum value 도입에 필요)
-- ============================================================
ALTER TABLE reports DROP CONSTRAINT IF EXISTS chk_report_type;
ALTER TABLE reports DROP CONSTRAINT IF EXISTS chk_out_of_scope_reason;
ALTER TABLE reports DROP CONSTRAINT IF EXISTS chk_publisher_type;

-- ============================================================
-- 2. 모든 태깅 메타데이터를 비우고 pending 상태로 reset.
--    PDF 파일·메시지 메타(file_path, sent_at, caption 등)는 그대로 보존.
-- ============================================================
UPDATE reports SET
    published_at        = NULL,
    report_type         = NULL,
    publisher           = NULL,
    publisher_type      = NULL,
    analysts            = '{}',
    title               = NULL,
    stock_codes         = '{}',
    company_names       = '{}',
    sectors_major       = '{}',
    sectors_minor       = '{}',
    products            = '{}',
    -- topics는 step 3에서 DROP COLUMN
    out_of_scope_reason = NULL,
    tagging_status      = 'pending',
    tagging_locked_at   = NULL,
    tagging_worker_id   = NULL,
    tagger_version      = NULL,
    taxonomy_version    = NULL,
    tagging_confidence  = NULL,
    tagging_notes       = NULL,
    tagged_at           = NULL;

-- ============================================================
-- 3. 컬럼 ADD/DROP
-- ============================================================
ALTER TABLE reports
  ADD COLUMN IF NOT EXISTS stock_codes_raw   text[] NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS company_names_raw text[] NOT NULL DEFAULT '{}';

DROP INDEX IF EXISTS ix_reports_topics_gin;
ALTER TABLE reports DROP COLUMN IF EXISTS topics;

-- ============================================================
-- 4. 새 CHECK 제약 ADD (v2 enum)
-- ============================================================
ALTER TABLE reports ADD CONSTRAINT chk_report_type CHECK (
  report_type IS NULL OR report_type IN
  ('단일종목','산업','섹터','IR자료','전략·시황','기타')
);

ALTER TABLE reports ADD CONSTRAINT chk_out_of_scope_reason CHECK (
  out_of_scope_reason IS NULL OR out_of_scope_reason IN
  ('foreign','fund','digital','private','ir_self')
);

ALTER TABLE reports ADD CONSTRAINT chk_publisher_type CHECK (
  publisher_type IS NULL OR publisher_type IN
  ('broker','data_provider','ir_agency','other')
);

-- ============================================================
-- 5. audit raw 컬럼 GIN 인덱스 (시나리오 G — KRX 미매칭 분석)
-- ============================================================
CREATE INDEX IF NOT EXISTS ix_reports_stocks_raw_gin
  ON reports USING gin (stock_codes_raw);
CREATE INDEX IF NOT EXISTS ix_reports_companies_raw_gin
  ON reports USING gin (company_names_raw);

COMMIT;
