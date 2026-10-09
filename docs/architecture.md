# 시스템 구성

## 전체 모양

세 단계가 Supabase(클라우드 Postgres)의 `reports` 표를 차례로 채우고 읽는다. 단계 사이는 함수 호출로 이어지지 않는다. 표의 `tagging_status` 값이 다음 단계의 입력이다. 여기에 두 일괄 작업이 따로 붙는다: 매일 주가를 받는 `prices update`와, 1년에 한 번 사업보고서로 유사 기업 자료를 만드는 `peers build`. 웹앱은 두 작업이 저장한 표만 읽는다.

```
텔레그램 채널 ──(Telethon, 세션 파일 sessions/<이름>)──▶ collect
   collect ──▶ 로컬 PDF 폴더 STORAGE_BASE_DIR (<메시지번호>_<이름>.pdf)
   collect ──(Supabase REST, 서비스 키)──▶ reports 새 행 (pending) / failed_attempts

reports (pending) ──(Postgres 직접 연결 SUPABASE_DB_URL, asyncpg 풀 최대 10)──▶ tag run
   tag run ──▶ 로컬 PDF 첫 1~3쪽 글자 ──▶ LLM (Anthropic / OpenAI / Codex CLI)
   tag run ──(같은 직접 연결, UPDATE 한 번)──▶ reports (auto / review_needed)

윈도우 작업 스케줄러 (평일 장 마감 뒤) ──▶ scripts/run-prices.ps1 ──▶ prices update
   prices update ──(HTTPS, 앱키·시크릿 → 실행마다 접근 토큰 한 번)──▶ KIS Open API (수정주가 일봉, 현재가)
   prices update ──(Supabase REST)──▶ stock_price_snapshot (종목당 한 행) / price_update_runs

로컬 MongoDB FS.A001_v2 (별도 저장소의 DART 수집 프로그램이 채움) ──(pymongo, 읽기만)──▶ peers build (연 1회, 사람이 실행)
   peers build ──▶ AI (회사마다 사업 요약 카드) + OpenAI 임베딩 (1536차원)
   peers build ──(Supabase REST)──▶ company_profiles / company_segments / company_embeddings / segment_embeddings / peer_builds
   peers build ──(리포트 창구, 개수 조회)──▶ reports (분류 작업이 도는 중인지)

브라우저 ──(HTTP 127.0.0.1:8520)──▶ web (FastAPI) ──▶ features/*
   features ──(Supabase REST, 서비스 키)──▶ reports 읽기·검토 쓰기, report_summaries 읽기·쓰기, 주가·유사 기업 표 읽기
   features/peers ──(RPC: match_company_profiles / match_company_segments, pgvector 정확 탐색)──▶ Supabase
   features ──▶ 로컬 PDF (보기·미리보기·분석 입력) ──▶ LLM (분석·비교 버튼을 눌렀을 때만)
   features/peers ──▶ OpenAI 임베딩 (테마 검색의 새 질의 하나)
```

- **수집기·웹앱·두 일괄 작업은 Supabase REST**(supabase-py, `SUPABASE_URL` + `SUPABASE_SERVICE_KEY`)로, **분류기는 Postgres 직접 연결**(`SUPABASE_DB_URL`)로 DB에 닿는다. 분류기만 직접 연결을 쓰는 이유: 가져가기(`FOR UPDATE SKIP LOCKED`)와 오래된 잠금 되돌리기가 한 문장 안에서 원자적으로 돌아야 하는데 REST로는 그렇게 쓸 수 없다.
- **AI는 네 곳에서만 부른다.**
  - 분류기: 행마다, 반복 실행.
  - 웹앱의 재무 분석·비교 해석문: 사용자가 버튼을 눌렀을 때만, 웹 서버 전체 동시 2개.
  - 유사도 계산(`peers build`): 회사마다 사업 요약 카드 추출(동시 2개)과 임베딩(100개씩).
  - 웹앱의 테마 검색: 새 질의마다 임베딩 하나(같은 질의는 캐시, 유사 기업 기능의 동시 2개 자리).
  - 목록 보기·선택·커버리지·검토·유사 기업 탭·상태 줄은 AI를 부르지 않는다. 유사 기업 탭은 저장된 임베딩으로 DB 함수가 계산한다.
- **바깥 데이터는 각자 한 명령만 가져온다.** KIS는 `prices update`만, MongoDB는 `peers build`만 부른다. 웹앱은 둘 다 부르지 않고 저장된 표만 읽는다. MongoDB의 사업보고서 텍스트는 이 저장소 밖의 DART 수집 프로그램이 채우고, 이 앱은 쓰지 않는다.
- **PDF 파일은 로컬 디스크에만 있다.** DB에는 저장 폴더 기준 상대 경로(`file_path`)만 있다. 그래서 분류기와 웹앱은 수집기와 같은 PC, 같은 `STORAGE_BASE_DIR`에서 돌아야 한다.
- **화면(`frontend/`, React + Vite)** 은 빌드 결과(`frontend/dist`)를 웹 서버가 `/`와 `/assets`로 내준다. 개발 중에는 Vite 개발 서버(5173)가 `/api`를 8520으로 넘긴다. 화면은 `/api/*` 주소와 응답 모양에만 의존하고 파이썬 코드를 모른다. 유사 기업 탭·테마 검색은 `frontend/src/peers/`, 상태 줄은 `frontend/src/freshness/`에 있다.

## 대표 흐름 — 리포트 분석 버튼 (`POST /api/reports/{rid}/analyze`)

1. 브라우저 → `web`: 허용 호스트 검사(127.0.0.1·localhost만) → 다른 출처의 쓰기 거절 검사 → `features/reports` 라우터.
2. `features/reports`: 처음 쓰일 때 `.env`를 다시 읽고 REST 연결을 만든다(없으면 "리포트 기능 사용 불가" 503). 번호로 분석 대상 행 하나를 읽는다(없으면 404).
3. `features/reports` → `features/analysis.analyze_report(row)`: 같은 리포트 분석 중이면 409 → 웹 서버 전체 AI 자리 2개 중 하나를 잡음 → 단일종목이 아니면 422 → 재무 상세가 있는 저장 결과가 있으면 그대로 반환 → 모델 키·codex CLI 확인(없으면 "분석 기능 사용 불가" 503) → `core.pdf`로 저장 폴더 안 PDF를 찾아 쪽마다 글자를 뽑음 → `core.llm`으로 AI 호출(시간 한도 180초, 일시 오류면 5초 뒤 1회 재시도) → 숫자 근거 확인 → `report_summaries`에 저장.
4. `features/reports`: 저장 결과를 붙인 리포트 공개 모양(`file_path` 없음) + `analysis_reused`를 돌려준다.

분류 흐름(`tag run`)은 행 하나마다 노드 8개짜리 그래프를 돈다: PDF 첫 쪽 읽기 → LLM 메타데이터 추출 → 세 갈래 분기. ① 글자를 못 읽었거나 LLM이 거부함 → 읽기 실패 상태 → 쓰기. ② IR자료, 또는 해외·펀드·디지털자산 신호, 또는 비상장 신호인데 원문 코드가 종목표에 없음 → 분석 대상 외 사유 정하기 → 상태 정하기 → 쓰기. ③ 나머지 → 종목표 매칭 → 상태 정하기 → 쓰기. 행들은 `MAX_CONCURRENT_LLM`(2)개씩 동시에 돌고, 행마다 90초 한도가 있다.

## 대표 흐름 — 유사 기업 탭 (`GET /api/stocks/{code}/peers`)

1. 브라우저 → `web`: 허용 호스트 검사 → `features/peers` 라우터(창구의 `web_router()`로 붙음). 코드 형식(`^[0-9A-Z]{6}$`)·기간·부문 번호 형식이 틀리면 아무것도 준비하지 않고 422.
2. `features/peers` 서비스: 처음 쓰일 때 DB 연결과 종목표를 준비(실패하면 "유사 기업 기능 사용 불가" 503) → 요청마다 최신 공개 빌드(`peer_builds`의 `done` 중 가장 최근, 표 없이 한 행)를 조회(없으면 503) → 종목표에 없으면 404 → 그 빌드의 (회계연도, 프로필 버전)에서 시드의 `ok` 프로필과 그 빌드 임베딩 모델의 회사 임베딩을 읽음(없으면 404) → 시드 사업부문 중 고른 것(없는 번호면 422).
3. 빌드의 백분위표·용어표(빌드 번호마다 한 번 읽어 둠) → DB 함수 `match_company_profiles`(시드 회사 벡터)와 `match_company_segments`(고른 부문 벡터)를 각 최대 200행으로 부름 → 시드·종목표에 없는 회사를 빼고 각 상위 100 → 회사 단위로 합쳐 등급(95 백분위 미만은 뺌)과 순위 → 상위 50.
4. `coverage.report_counts(시드+피어, 365일)` → 리포트 창구 `rows_for_stocks`(분석 대상 행, 종목 배열 겹침, 1000행씩)로 증권사 리포트 수. `prices.snapshots(시드+피어)` → 주가 스냅샷.
5. 시드의 초과수익률이 +10%p 이상이면 피어마다 반응 판정, 후보 여섯 조건 판정 → 응답. AI는 부르지 않는다.

상태 줄(`GET /api/freshness`)은 요청마다 `prices.latest_run()`과 `reports.latest_report_sent_at()`을 읽어 밀림을 판정한다. 자기 표·설정이 없다.

## 코드 칸 지도 (`research_desk/`)

| 칸 | 역할 | 쓸 수 있는 칸 |
|---|---|---|
| `__main__.py`, `cli.py` | 명령 입구. 모든 명령을 여기서 등록 | 모든 칸의 등록 함수, `core`·`domain` 함수, 기능의 공개 창구 |
| `core` | 공용 설비: `.env`·설정 값, Supabase REST·Postgres 연결, LLM 공급자 연결·재시도·임베딩, KIS Open API 클라이언트, MongoDB 읽기, PDF 열기·글자·그림. 업무 개념을 모른다 | 없음 (외부 도구만) |
| `domain` | 공용 기준: 분류 체계 값(`vocabulary.yaml`), "분석 대상"·"분석 대상 외 행 모양" 규칙, 종목표 읽기·조회·버전 | `core` |
| `collector` | 텔레그램 → PDF + `pending` 행, 실패 기록 | `core`, `domain` |
| `tagger` | `pending` → `auto`/`review_needed` (LangGraph 행 그래프) | `core`, `domain` |
| `features/companies` | 기업 목록·관심 기업 | `core`, `domain` |
| `features/analysis` | 리포트 1건 재무 분석, `report_summaries` 표, 웹 AI 자리 2개 | `core`, `domain` |
| `features/reports` | 분류가 끝난 리포트 조회, 목록·PDF·분석 실행 주소. 다른 기능용으로 여러 종목 리포트 읽기, 가장 최근 리포트 시각, 분류 진행 확인 | `core`, `domain`, 기능 `analysis` |
| `features/compare` | 두 보고서 비교 | `core`, `domain`, 기능 `reports`·`analysis` |
| `features/coverage` | 커버리지 집계, 집계용 행 캐시, 종목별 증권사 리포트 수 세기 | `core`, `domain`, 기능 `reports` |
| `features/review` | 수동 검토·되돌리기 | `core`, `domain`, 기능 `coverage` (처리 뒤 캐시 비우기) |
| `features/prices` | 매일 주가 스냅샷 명령(`prices update`), 주가 표 두 개, 다른 기능이 읽는 주가 창구. 웹 주소 없음 | `core`, `domain` |
| `features/peers` | 연 1회 유사도 계산 명령(`peers build`·`peers inspect`), 유사 기업·테마 검색 주소, 유사 기업 표 다섯 개와 DB 함수 두 개 | `core`, `domain`, 기능 `coverage`·`prices`·`reports`(`reports`는 명령 함수 안에서만) |
| `features/freshness` | 모든 화면 맨 위 상태 줄의 주소(`/api/freshness`). 자기 표·설정 없음 | `core`, `domain`, 기능 `prices`·`reports` |
| `web` | 웹 서버 조립: 공통 보안 장치, 오류 응답, 화면 파일, 기능 등록 목록. 업무 처리를 하지 않는다 | `core`, `domain`, 기능의 공개 창구 |

- 기능끼리는 상대 기능의 `__init__.py`가 내보낸 이름으로만 주고받는다. 기능 사이 의존은 위 표에 적힌 방향(`reports → analysis`, `compare → reports·analysis`, `coverage → reports`, `review → coverage`, `peers → coverage·prices·reports`, `freshness → prices·reports`)뿐이고, 거꾸로나 순환은 없다. `prices`는 다른 기능을 쓰지 않는다.
- `analysis`에 웹 주소가 없는 이유: 리포트 목록이 분석 결과를 붙이려면 `reports`가 `analysis`를 써야 한다. `analysis`가 리포트 행을 직접 읽으면 서로 물고 물린다. 그래서 `analysis`는 받은 행을 분석만 하고, 분석 주소는 `reports`가 맡는다.
- `review`가 `coverage`를 쓰는 이유: 검토 직후 커버리지 숫자가 바로 바뀌어야 한다. 웹 조립부는 등록만 하므로 이 연결을 거기에 두지 않는다.
- `peers`가 `reports`를 명령 함수 안에서만 쓰는 이유: 명령 입구는 모든 명령에서 `peers` 창구를 import하는데, 리포트 창구는 FastAPI를 불러온다. 분류 진행 확인은 `peers build`가 실행될 때만 필요하다. 그래서 `peers build`는 실행될 때 웹 패키지가 있어야 한다.
- 명령이 있는 기능(`prices`, `peers`)의 창구는 FastAPI를 부르지 않는 이름만 묶는다. 웹 주소가 있으면(`peers`) 웹 조립부가 창구의 `web_router()`를 불러 라우터를 받는다. 명령 없이 `python -m research_desk`를 실행하면 FastAPI·uvicorn·웹 앱·LangGraph·pymongo·유사 기업 웹 쪽이 올라오지 않는다.
- 이 칸 경계와 아래 표 주인은 `research_desk/tests/test_architecture.py`가 모든 테스트 실행(그리고 모든 커밋)에서 검사한다.

## DB 표와 주인

| 표 | 주인 칸 | 하는 일 |
|---|---|---|
| `reports` | `collector` | 새 행 만들기, 이미 받은 메시지 번호·마지막 번호 조회 |
| `reports` | `tagger` | 대기 행 가져가기, 오래된 잠금 되돌리기, 분류 결과 쓰기, 작업자 단위 되돌리기, 현황 집계, 재처리 대상 조회 |
| `reports` | `features/review` | 검토 대기열 조회, 검토 결과 쓰기, 되돌리기 |
| `reports` | `features/reports` | 분류가 끝난 리포트 조회(목록·상세·PDF·집계 재료·여러 종목 리포트·가장 최근 리포트), 분류 진행 중인 행 개수 |
| `failed_attempts` | `collector` | 실패 기록·재시도·정리 |
| `report_summaries` | `features/analysis` | 분석 결과 저장·조회, 비교 결과 저장 |
| `stock_price_snapshot`, `price_update_runs` | `features/prices` | 종목당 최신 주가 스냅샷 쓰기·읽기, 주가 갱신 실행 기록 |
| `company_profiles`, `company_segments`, `company_embeddings`, `segment_embeddings`, `peer_builds` | `features/peers` | 회사별 사업 요약 카드와 사업부문, 임베딩 모델별 임베딩, 빌드 기록(백분위표·용어표) |
| DB 함수 `match_company_profiles`, `match_company_segments` | `features/peers` | 질의 벡터와 가까운 회사 / 회사마다 가장 가까운 사업부문(정확 탐색, 최대 200행) |

`compare`·`coverage`는 표에 직접 닿지 않고 `reports`의 공개 창구로 읽는다. 비교 결과 저장도 `analysis`의 공개 창구를 거친다. `peers`는 리포트 수를 `coverage`, 주가를 `prices`, 분류 진행 여부를 `reports` 창구로 받고, `freshness`는 `prices`·`reports` 창구만 읽는다.

## 외부 의존

| 대상 | 쓰는 곳 | 연결 |
|---|---|---|
| 텔레그램 | `collector` | Telethon. `TELEGRAM_API_ID`/`HASH`, 세션 파일 `sessions/<TELEGRAM_SESSION_NAME>` |
| Supabase REST | `collector`, `features/*` | supabase-py, 서비스 키(행 수준 보안을 우회) |
| Supabase Postgres 직접 연결 | `tagger` | asyncpg, `statement_cache_size=0` (트랜잭션 풀러 6543 포트 대응) |
| pgvector (Supabase의 `extensions` 스키마) | `features/peers`의 DB 함수 두 개 | 마이그레이션 008이 확장과 `vector(1536)` 열을 만든다. 벡터 인덱스 없이 정확 탐색 |
| Anthropic API | 모델 이름이 `claude-*` | `ANTHROPIC_API_KEY` |
| OpenAI API | 그 밖의 모델 이름, 그리고 모든 임베딩(`peers build`, 웹 테마 검색) | `OPENAI_API_KEY`. 유사 기업 기능은 프로필 모델이 Claude여도 임베딩 때문에 이 키가 필요하다 |
| Codex CLI | 모델 이름이 `codex:<모델>` | 로컬 `codex exec` (ChatGPT 로그인 한도, API 키 없음) |
| 한국투자증권 KIS Open API | `core.kis` ← `prices update` | httpx(HTTPS), 실전 서버 `KIS_BASE_URL`. `KIS_APP_KEY`·`KIS_APP_SECRET` → 실행마다 접근 토큰 한 번. 수정주가 일봉(한 번에 100행)과 현재가. 호출 시작 간격은 `PRICES_MAX_CALLS_PER_SEC` 이하 |
| 로컬 MongoDB `FS.A001_v2` | `core.mongo` ← `peers build` | pymongo, 읽기만. 사업보고서 섹션 하나 = 문서 하나이고 `parser_version` 0.2.0 이상인 문서만 읽는다. `DART_MONGO_URL`·`DART_MONGO_DB`·`DART_MONGO_COLLECTION` |
| LangSmith | 선택 | `LANGSMITH_TRACING=true`면 LangGraph 실행과 LLM 호출 기록을 보냄 |
| 윈도우 작업 스케줄러 | 매일 주가 갱신 | 평일 장 마감 뒤(기본 18:30) `scripts/run-prices.ps1` → `python -m research_desk prices update`. 저장소 폴더를 현재 폴더로 맞추는 `.ps1`을 거쳐 `python -m research_desk <명령>`을 부르는 방식 |
