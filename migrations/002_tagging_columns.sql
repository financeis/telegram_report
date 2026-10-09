-- migrations/002_tagging_columns.sql
--
-- Phase 1: 리포트 메타데이터 태깅 routine을 위한 reports 테이블 확장.
-- 설계 문서: 2026-05의 옛 설계 노트(git에 없음). 지금 규칙은 docs/business-rules.md
--
-- 적용 전 사전 점검:
--   - tagged_at은 001_init.sql에 이미 존재. ADD COLUMN 하지 않음 (의미만 'tagging 완료 시각'으로 확장).
--   - tags / idx_reports_untagged 도 master 그대로. 이 마이그레이션이 건드리지 않음.

-- ============================================================
-- 1. 컬럼 추가 (모두 nullable 또는 default. 기존 row는 자동으로 'pending' 상태가 됨)
-- ============================================================

alter table reports
  add column published_at         date,
  add column report_type          text,
  add column publisher            text,
  add column publisher_type       text,
  add column analysts             text[]      not null default '{}',
  add column title                text,
  add column stock_codes          text[]      not null default '{}',
  add column company_names        text[]      not null default '{}',
  add column sectors_major        text[]      not null default '{}',
  add column sectors_minor        text[]      not null default '{}',
  add column products             text[]      not null default '{}',
  add column topics               text[]      not null default '{}',
  add column out_of_scope_reason  text,
  add column tagging_status       text        not null default 'pending',
  add column tagging_locked_at    timestamptz,
  add column tagging_worker_id    text,
  add column tagger_version       text,
  add column taxonomy_version     text,
  add column tagging_confidence   text,
  add column tagging_notes        text;

-- ============================================================
-- 2. CHECK 제약 (enum 강제)
-- ============================================================

alter table reports add constraint chk_report_type check (
  report_type is null or report_type in (
    '단일종목','산업','섹터','시황·데일리','거시·매크로','퀀트·전략','전략·테마',
    'IPO','ESG','부동산·리츠','파생·원자재','채권·크레딧','IR자료','기타'
  )
);

alter table reports add constraint chk_publisher_type check (
  publisher_type is null or publisher_type in (
    'broker','company','data_provider','ir_agency','other'
  )
);

alter table reports add constraint chk_out_of_scope_reason check (
  out_of_scope_reason is null or out_of_scope_reason in (
    'foreign','fund','digital','private'
  )
);

alter table reports add constraint chk_tagging_status check (
  tagging_status in ('pending','processing','auto','review_needed','verified')
);

alter table reports add constraint chk_tagging_confidence check (
  tagging_confidence is null or tagging_confidence in ('high','medium','low')
);

-- ============================================================
-- 3. 인덱스 (큐, 트레이싱, 배열 검색)
-- ============================================================

-- routine 큐 (claim 대상 / stale lock 회수)
create index ix_reports_pending     on reports (tagging_status, downloaded_at) where tagging_status = 'pending';
create index ix_reports_processing  on reports (tagging_locked_at)            where tagging_status = 'processing';

-- in-scope vs OOS 분리
create index ix_reports_in_scope_pub on reports (published_at desc)
                                     where out_of_scope_reason is null and published_at is not null;
create index ix_reports_oos          on reports (out_of_scope_reason)
                                     where out_of_scope_reason is not null;

-- 평탄 컬럼 트레이싱
create index ix_reports_publisher_pub on reports (publisher, published_at desc)        where publisher is not null;
create index ix_reports_pubtype_pub   on reports (publisher_type, published_at desc)   where publisher_type is not null;
create index ix_reports_type_pub      on reports (report_type, published_at desc)      where report_type is not null;

-- 배열 검색용 GIN — 트레이싱 쿼리는 모두 @> 또는 &&로 작성
create index ix_reports_stocks_gin    on reports using gin (stock_codes);
create index ix_reports_companies_gin on reports using gin (company_names);
create index ix_reports_smajor_gin    on reports using gin (sectors_major);
create index ix_reports_sminor_gin    on reports using gin (sectors_minor);
create index ix_reports_products_gin  on reports using gin (products);
create index ix_reports_topics_gin    on reports using gin (topics);
create index ix_reports_analysts_gin  on reports using gin (analysts);

-- ============================================================
-- 4. routine RPC 헬퍼 (atomic claim — Supabase에서 execute_sql 또는 RPC로 호출)
-- ============================================================
-- 본 마이그레이션에서는 컬럼·제약·인덱스만 깐다. claim/cleanup SQL은 SKILL.md의
-- 워크플로우에서 인라인으로 실행한다 (FOR UPDATE SKIP LOCKED 패턴, 설계 §6.7).
