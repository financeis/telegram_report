-- migrations/008_peers_prices_freshness.sql
--
-- 사업보고서 기반 유사 기업 찾기: 표 7개와 벡터 탐색 함수 2개.
--   주가 (주인 features/prices)      : stock_price_snapshot, price_update_runs
--   유사 기업 (주인 features/peers)  : company_profiles, company_segments, company_embeddings,
--                                      segment_embeddings, peer_builds,
--                                      함수 match_company_profiles, match_company_segments
--   다른 기능은 이 표들을 주인 기능의 공개 창구로만 읽는다. 자료 기준일 상태 줄
--   (features/freshness)은 표를 갖지 않고 주가·리포트 기능의 창구로 읽는다.
--
-- 적용: Supabase SQL 편집기에서 한 번 실행한다(자동 적용 장치 없음). 데이터는 바꾸지 않는다.
-- 다시 실행해도 깨지지 않는다: create ... if not exists, create or replace function,
-- RLS·권한 문은 몇 번 실행해도 결과가 같다.
--
-- RLS: 모든 새 표에서 켜고 정책은 두지 않는다 = anon/authenticated 거부, service_role(서비스 키)만
-- 우회한다. 기존 표와 같다.
-- CHECK 값 집합은 값이 있을 때만 검사한다(비어 있는 값은 통과).

-- pgvector 확장. Supabase 기본 위치인 extensions 스키마에 둔다.
-- 아래 표와 함수는 벡터 타입을 extensions.vector로 적는다.
create extension if not exists vector with schema extensions;

begin;

-- ============================================================
-- 1. 주가 (주인: features/prices)
-- ============================================================

-- 종목당 최신 주가 스냅샷 한 행. 일별 이력은 두지 않는다. prices update가 KIS 자료로 갱신한다.
-- 못 받은 종목·이력이 짧은 종목은 값이 빌 수 있어 키·flags·source·updated_at 밖의 열은 null을 허용한다.
create table if not exists public.stock_price_snapshot (
  stock_code     text        primary key,
  market         text,                                -- KOSPI / KOSDAQ (KOSDAQ GLOBAL은 KOSDAQ)
  as_of          date,                                -- 받은 자료의 마지막 거래일
  close          numeric,                             -- 수정 종가
  market_cap     bigint,                              -- 시가총액(원)
  avg_value_20d  bigint,                              -- 최근 20거래일 평균 거래대금(원)
  traded         boolean,                             -- 거래 중 여부 (거래정지면 false)
  ret_1w         double precision,                    -- 수익률: 5·21·63·126거래일 전 수정 종가 대비
  ret_1m         double precision,
  ret_3m         double precision,
  ret_6m         double precision,
  xret_1w        double precision,                    -- 초과수익률: ret_* − 같은 실행·같은 시장의 중앙값
  xret_1m        double precision,
  xret_3m        double precision,
  xret_6m        double precision,
  flags          text[]      not null default '{}',   -- no_data / short_history / halted / admin_issue
  source         text        not null default 'KIS',
  updated_at     timestamptz not null default now(),

  constraint chk_price_snapshot_market check (market in ('KOSPI','KOSDAQ'))
);

-- prices update 실행 기록. 실행마다 한 행: running으로 시작해 ok / partial / failed로 마감한다.
-- 상태 줄이 마지막 실행과 마지막 성공 실행을 읽는다.
create table if not exists public.price_update_runs (
  run_id         bigserial   primary key,
  started_at     timestamptz not null default now(),
  finished_at    timestamptz,
  status         text        not null default 'running',
  as_of          date,
  stocks_total   int,
  stocks_ok      int,
  stocks_failed  int,
  message        text,

  constraint chk_price_runs_status check (status in ('running','ok','partial','failed'))
);

-- 마지막 실행 찾기
create index if not exists ix_price_update_runs_started
  on public.price_update_runs (started_at desc);

-- ============================================================
-- 2. 유사 기업 (주인: features/peers)
-- ============================================================

-- 회사별 AI 사업 요약 카드(프로필). (회계연도, 프로필 버전, 종목코드)당 한 행.
-- 추출에 실패한 회사도 status='failed'와 fail_reason으로 남는다. 실패 행은 대부분의 열이 비고
-- 공개 빌드는 terms 열만 다시 쓰므로, 키와 created_at 밖의 열은 null을 허용한다.
create table if not exists public.company_profiles (
  fiscal_year      int         not null,
  profile_version  text        not null,
  stock_code       text        not null,

  -- 원천 사업보고서
  corp_code        text,
  corp_name        text,
  rcept_no         text,
  report_name      text,
  fiscal_end       date,
  parser_version   text,

  -- 추출 결과
  status           text,                      -- ok / failed
  fail_reason      text,
  source_sections  text[],
  source_chars     int,
  input_truncated  boolean,
  profile          jsonb,                     -- 근거 검사 뒤 AI 응답 (원래 표기 그대로)
  one_line         text,
  is_holding       boolean,
  is_financial     boolean,
  info_quality     text,                      -- 충분 / 부족
  terms            text[],                    -- 비교 열쇠 목록(키워드+제품+부문 키워드·제품). 공개 빌드가 다시 쓴다

  -- AI 호출 기록
  llm_model        text,
  input_tokens     int,
  output_tokens    int,
  grounding_ratio  real,                      -- 근거율 = 남긴 말 수 ÷ AI가 낸 말 수
  created_at       timestamptz not null default now(),

  primary key (fiscal_year, profile_version, stock_code),
  constraint chk_company_profiles_status       check (status in ('ok','failed')),
  constraint chk_company_profiles_info_quality check (info_quality in ('충분','부족'))
);

-- 프로필 안의 사업부문. 프로필이 지워지면 함께 지워진다.
-- AI 응답 모양처럼 빈 값 대신 빈 목록과 -1을 쓴다.
create table if not exists public.company_segments (
  fiscal_year        int     not null,
  profile_version    text    not null,
  stock_code         text    not null,
  seg_no             int     not null,
  name               text    not null,
  products           text[]  not null default '{}',
  keywords           text[]  not null default '{}',
  revenue_share_pct  real    not null default -1,     -- 매출 비중 %. -1 = 미상

  primary key (fiscal_year, profile_version, stock_code, seg_no),
  constraint fk_company_segments_profile
    foreign key (fiscal_year, profile_version, stock_code)
    references public.company_profiles (fiscal_year, profile_version, stock_code)
    on delete cascade
);

-- 회사 임베딩. 임베딩 모델마다 따로 둔다: 모델을 바꿔도 AI 추출을 다시 하지 않고,
-- 이미 공개된 빌드의 임베딩을 덮어쓰지 않는다. 1536차원 고정. 프로필이 지워지면 함께 지워진다.
create table if not exists public.company_embeddings (
  fiscal_year      int                      not null,
  profile_version  text                     not null,
  stock_code       text                     not null,
  embed_model      text                     not null,
  embedding        extensions.vector(1536)  not null,
  created_at       timestamptz              not null default now(),

  primary key (fiscal_year, profile_version, stock_code, embed_model),
  constraint fk_company_embeddings_profile
    foreign key (fiscal_year, profile_version, stock_code)
    references public.company_profiles (fiscal_year, profile_version, stock_code)
    on delete cascade
);

-- 사업부문 임베딩. 부문마다, 임베딩 모델마다 한 행. 부문이 지워지면 함께 지워진다.
create table if not exists public.segment_embeddings (
  fiscal_year      int                      not null,
  profile_version  text                     not null,
  stock_code       text                     not null,
  seg_no           int                      not null,
  embed_model      text                     not null,
  embedding        extensions.vector(1536)  not null,
  created_at       timestamptz              not null default now(),

  primary key (fiscal_year, profile_version, stock_code, seg_no, embed_model),
  constraint fk_segment_embeddings_segment
    foreign key (fiscal_year, profile_version, stock_code, seg_no)
    references public.company_segments (fiscal_year, profile_version, stock_code, seg_no)
    on delete cascade
);

-- 빌드 기록. 빌드 하나 = (회계연도, 프로필 버전, 임베딩 모델)의 한 회 계산.
-- running으로 시작해 done / incomplete / failed로 마감하고, 시험 실행은 pilot이다.
-- 화면은 status='done'인 가장 최근 빌드만 쓴다.
create table if not exists public.peer_builds (
  build_id            bigserial   primary key,
  fiscal_year         int         not null,
  profile_version     text        not null,
  embed_model         text        not null,
  embed_dims          int         not null default 1536,
  parser_version      text,
  synonyms_version    text,                                -- 동의어표 지문
  stock_list_version  text,                                -- 종목표 버전
  status              text        not null default 'running',
  eligible            int,                                 -- 대상 회사 수
  profiled            int,                                 -- 프로필 성공 수
  failed              int,                                 -- 프로필 실패 수
  company_quantiles   jsonb,                               -- 회사 쌍 코사인 백분위표 [[백분위, 코사인], …]
  segment_quantiles   jsonb,                               -- 부문 쌍 코사인 백분위표
  term_table          jsonb,                               -- {비교 열쇠: {"display": 화면 표기, "companies": 보유 회사 수}}
  started_at          timestamptz not null default now(),
  heartbeat_at        timestamptz not null default now(),  -- 마지막 진행 시각
  finished_at         timestamptz,
  message             text,

  constraint chk_peer_builds_status     check (status in ('running','done','incomplete','failed','pilot')),
  constraint chk_peer_builds_embed_dims check (embed_dims = 1536)
);

-- 최신 공개 빌드 찾기 (status='done' 중 finished_at이 가장 늦은 것)
create index if not exists ix_peer_builds_status_finished
  on public.peer_builds (status, finished_at desc);

-- ============================================================
-- 3. RLS: 정책 없음 = anon/authenticated 거부, service_role만 우회 (기존 표와 같음)
-- ============================================================
alter table public.stock_price_snapshot enable row level security;
alter table public.price_update_runs    enable row level security;
alter table public.company_profiles     enable row level security;
alter table public.company_segments     enable row level security;
alter table public.company_embeddings   enable row level security;
alter table public.segment_embeddings   enable row level security;
alter table public.peer_builds          enable row level security;

-- ============================================================
-- 4. 벡터 탐색 함수 (주인: features/peers, 서비스 키 전용)
-- ============================================================
-- 그 임베딩 모델의 임베딩 중 status='ok'인 프로필에 이어진 행만 보고, 질의 벡터와 가까운 순으로
-- 돌려준다. 유사도 = 1 - 코사인 거리(<=>).
-- 벡터 인덱스를 두지 않으므로 늘 정확 탐색이다(근사 탐색 없음).
-- 결과 수는 p_limit이고 최대 200이다(null이면 200, 음수면 0).
-- 거리가 같으면 종목코드(와 부문 번호) 순이라 결과 순서가 늘 같다.

create or replace function public.match_company_profiles(
  p_fiscal_year      int,
  p_profile_version  text,
  p_embed_model      text,
  p_query            extensions.vector(1536),
  p_limit            int
)
returns table (stock_code text, similarity double precision)
language sql
stable
security invoker
set search_path = public, extensions
as $$
  select e.stock_code,
         1 - (e.embedding <=> p_query) as similarity
    from public.company_embeddings e
    join public.company_profiles p
      on p.fiscal_year     = e.fiscal_year
     and p.profile_version = e.profile_version
     and p.stock_code      = e.stock_code
   where e.fiscal_year     = p_fiscal_year
     and e.profile_version = p_profile_version
     and e.embed_model     = p_embed_model
     and p.status          = 'ok'
   order by e.embedding <=> p_query, e.stock_code
   limit greatest(least(coalesce(p_limit, 200), 200), 0);
$$;

create or replace function public.match_company_segments(
  p_fiscal_year      int,
  p_profile_version  text,
  p_embed_model      text,
  p_query            extensions.vector(1536),
  p_limit            int
)
returns table (stock_code text, seg_no int, similarity double precision)
language sql
stable
security invoker
set search_path = public, extensions
as $$
  select e.stock_code,
         e.seg_no,
         1 - (e.embedding <=> p_query) as similarity
    from public.segment_embeddings e
    join public.company_profiles p
      on p.fiscal_year     = e.fiscal_year
     and p.profile_version = e.profile_version
     and p.stock_code      = e.stock_code
   where e.fiscal_year     = p_fiscal_year
     and e.profile_version = p_profile_version
     and e.embed_model     = p_embed_model
     and p.status          = 'ok'
   order by e.embedding <=> p_query, e.stock_code, e.seg_no
   limit greatest(least(coalesce(p_limit, 200), 200), 0);
$$;

-- 서비스 역할만 실행한다. Postgres는 새 함수의 실행 권한을 PUBLIC에, Supabase는 anon·authenticated에도
-- 주므로 거둬들인다.
revoke execute on function public.match_company_profiles(int, text, text, extensions.vector, int)
  from public, anon, authenticated;
revoke execute on function public.match_company_segments(int, text, text, extensions.vector, int)
  from public, anon, authenticated;
grant execute on function public.match_company_profiles(int, text, text, extensions.vector, int)
  to service_role;
grant execute on function public.match_company_segments(int, text, text, extensions.vector, int)
  to service_role;

-- PostgREST가 새 표와 함수를 바로 보게 한다.
notify pgrst, 'reload schema';

commit;
