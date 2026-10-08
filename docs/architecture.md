# 시스템 구성

## 전체 모양

세 단계가 Supabase(클라우드 Postgres)의 `reports` 표를 차례로 채우고 읽는다. 단계 사이는 함수 호출로 이어지지 않는다. 표의 `tagging_status` 값이 다음 단계의 입력이다.

```
텔레그램 채널 ──(Telethon, 세션 파일 sessions/<이름>)──▶ collect
   collect ──▶ 로컬 PDF 폴더 STORAGE_BASE_DIR (<메시지번호>_<이름>.pdf)
   collect ──(Supabase REST, 서비스 키)──▶ reports 새 행 (pending) / failed_attempts

reports (pending) ──(Postgres 직접 연결 SUPABASE_DB_URL, asyncpg 풀 최대 10)──▶ tag run
   tag run ──▶ 로컬 PDF 첫 1~3쪽 글자 ──▶ LLM (Anthropic / OpenAI / Codex CLI)
   tag run ──(같은 직접 연결, UPDATE 한 번)──▶ reports (auto / review_needed)

브라우저 ──(HTTP 127.0.0.1:8520)──▶ web (FastAPI) ──▶ features/*
   features ──(Supabase REST, 서비스 키)──▶ reports 읽기·검토 쓰기, report_summaries 읽기·쓰기
   features ──▶ 로컬 PDF (보기·미리보기·분석 입력) ──▶ LLM (분석·비교 버튼을 눌렀을 때만)
```

- **수집기와 웹앱은 Supabase REST**(supabase-py, `SUPABASE_URL` + `SUPABASE_SERVICE_KEY`)로, **분류기는 Postgres 직접 연결**(`SUPABASE_DB_URL`)로 DB에 닿는다. 분류기만 직접 연결을 쓰는 이유: 가져가기(`FOR UPDATE SKIP LOCKED`)와 오래된 잠금 되돌리기가 한 문장 안에서 원자적으로 돌아야 하는데 REST로는 그렇게 쓸 수 없다.
- **LLM은 두 곳에서만 부른다.** 분류기(행마다, 반복 실행)와 웹앱의 재무 분석·비교 해석문(사용자가 버튼을 눌렀을 때만). 목록 보기·선택·커버리지·검토는 LLM을 부르지 않는다.
- **PDF 파일은 로컬 디스크에만 있다.** DB에는 저장 폴더 기준 상대 경로(`file_path`)만 있다. 그래서 분류기와 웹앱은 수집기와 같은 PC, 같은 `STORAGE_BASE_DIR`에서 돌아야 한다.
- **화면(`frontend/`, React + Vite)** 은 빌드 결과(`frontend/dist`)를 웹 서버가 `/`와 `/assets`로 내준다. 개발 중에는 Vite 개발 서버(5173)가 `/api`를 8520으로 넘긴다. 화면은 `/api/*` 주소와 응답 모양에만 의존하고 파이썬 코드를 모른다.

## 대표 흐름 — 리포트 분석 버튼 (`POST /api/reports/{rid}/analyze`)

1. 브라우저 → `web`: 허용 호스트 검사(127.0.0.1·localhost만) → 다른 출처의 쓰기 거절 검사 → `features/reports` 라우터.
2. `features/reports`: 처음 쓰일 때 `.env`를 다시 읽고 REST 연결을 만든다(없으면 "리포트 기능 사용 불가" 503). 번호로 분석 대상 행 하나를 읽는다(없으면 404).
3. `features/reports` → `features/analysis.analyze_report(row)`: 같은 리포트 분석 중이면 409 → 웹 서버 전체 AI 자리 2개 중 하나를 잡음 → 단일종목이 아니면 422 → 재무 상세가 있는 저장 결과가 있으면 그대로 반환 → 모델 키·codex CLI 확인(없으면 "분석 기능 사용 불가" 503) → `core.pdf`로 저장 폴더 안 PDF를 찾아 쪽마다 글자를 뽑음 → `core.llm`으로 AI 호출(시간 한도 180초, 일시 오류면 5초 뒤 1회 재시도) → 숫자 근거 확인 → `report_summaries`에 저장.
4. `features/reports`: 저장 결과를 붙인 리포트 공개 모양(`file_path` 없음) + `analysis_reused`를 돌려준다.

분류 흐름(`tag run`)은 행 하나마다 노드 8개짜리 그래프를 돈다: PDF 첫 쪽 읽기 → LLM 메타데이터 추출 → 세 갈래 분기. ① 글자를 못 읽었거나 LLM이 거부함 → 읽기 실패 상태 → 쓰기. ② IR자료, 또는 해외·펀드·디지털자산 신호, 또는 비상장 신호인데 원문 코드가 종목표에 없음 → 분석 대상 외 사유 정하기 → 상태 정하기 → 쓰기. ③ 나머지 → 종목표 매칭 → 상태 정하기 → 쓰기. 행들은 `MAX_CONCURRENT_LLM`(2)개씩 동시에 돌고, 행마다 90초 한도가 있다.

## 코드 칸 지도 (`research_desk/`)

| 칸 | 역할 | 쓸 수 있는 칸 |
|---|---|---|
| `__main__.py`, `cli.py` | 명령 입구. 모든 명령을 여기서 등록 | 모든 칸의 등록 함수, `core`·`domain` 함수, 기능의 공개 창구 |
| `core` | 공용 설비: `.env`·설정 값, Supabase REST·Postgres 연결, LLM 공급자 연결·재시도, PDF 열기·글자·그림. 업무 개념을 모른다 | 없음 (외부 도구만) |
| `domain` | 공용 기준: 분류 체계 값(`vocabulary.yaml`), "분석 대상"·"분석 대상 외 행 모양" 규칙, 종목표 읽기·조회·버전 | `core` |
| `collector` | 텔레그램 → PDF + `pending` 행, 실패 기록 | `core`, `domain` |
| `tagger` | `pending` → `auto`/`review_needed` (LangGraph 행 그래프) | `core`, `domain` |
| `features/companies` | 기업 목록·관심 기업 | `core`, `domain` |
| `features/analysis` | 리포트 1건 재무 분석, `report_summaries` 표, 웹 AI 자리 2개 | `core`, `domain` |
| `features/reports` | 분류가 끝난 리포트 조회, 목록·PDF·분석 실행 주소 | `core`, `domain`, 기능 `analysis` |
| `features/compare` | 두 보고서 비교 | `core`, `domain`, 기능 `reports`·`analysis` |
| `features/coverage` | 커버리지 집계, 집계용 행 캐시 | `core`, `domain`, 기능 `reports` |
| `features/review` | 수동 검토·되돌리기 | `core`, `domain`, 기능 `coverage` (처리 뒤 캐시 비우기) |
| `web` | 웹 서버 조립: 공통 보안 장치, 오류 응답, 화면 파일, 기능 등록 목록. 업무 처리를 하지 않는다 | `core`, `domain`, 기능의 공개 창구 |

- 기능끼리는 상대 기능의 `__init__.py`가 내보낸 이름으로만 주고받는다. 기능 사이 의존은 위 표에 적힌 방향(`reports → analysis`, `compare → reports·analysis`, `coverage → reports`, `review → coverage`)뿐이고, 거꾸로나 순환은 없다.
- `analysis`에 웹 주소가 없는 이유: 리포트 목록이 분석 결과를 붙이려면 `reports`가 `analysis`를 써야 한다. `analysis`가 리포트 행을 직접 읽으면 서로 물고 물린다. 그래서 `analysis`는 받은 행을 분석만 하고, 분석 주소는 `reports`가 맡는다.
- `review`가 `coverage`를 쓰는 이유: 검토 직후 커버리지 숫자가 바로 바뀌어야 한다. 웹 조립부는 등록만 하므로 이 연결을 거기에 두지 않는다.
- 이 칸 경계와 아래 표 주인은 `research_desk/tests/test_architecture.py`가 모든 테스트 실행(그리고 모든 커밋)에서 검사한다.

## DB 표와 주인

| 표 | 주인 칸 | 하는 일 |
|---|---|---|
| `reports` | `collector` | 새 행 만들기, 이미 받은 메시지 번호·마지막 번호 조회 |
| `reports` | `tagger` | 대기 행 가져가기, 오래된 잠금 되돌리기, 분류 결과 쓰기, 작업자 단위 되돌리기, 현황 집계, 재처리 대상 조회 |
| `reports` | `features/review` | 검토 대기열 조회, 검토 결과 쓰기, 되돌리기 |
| `reports` | `features/reports` | 분류가 끝난 리포트 조회(목록·상세·PDF·집계 재료) |
| `failed_attempts` | `collector` | 실패 기록·재시도·정리 |
| `report_summaries` | `features/analysis` | 분석 결과 저장·조회, 비교 결과 저장 |

`compare`·`coverage`는 표에 직접 닿지 않고 `reports`의 공개 창구로 읽는다. 비교 결과 저장도 `analysis`의 공개 창구를 거친다.

## 외부 의존

| 대상 | 쓰는 곳 | 연결 |
|---|---|---|
| 텔레그램 | `collector` | Telethon. `TELEGRAM_API_ID`/`HASH`, 세션 파일 `sessions/<TELEGRAM_SESSION_NAME>` |
| Supabase REST | `collector`, `features/*` | supabase-py, 서비스 키(행 수준 보안을 우회) |
| Supabase Postgres 직접 연결 | `tagger` | asyncpg, `statement_cache_size=0` (트랜잭션 풀러 6543 포트 대응) |
| Anthropic API | 모델 이름이 `claude-*` | `ANTHROPIC_API_KEY` |
| OpenAI API | 그 밖의 모델 이름 | `OPENAI_API_KEY` |
| Codex CLI | 모델 이름이 `codex:<모델>` | 로컬 `codex exec` (ChatGPT 로그인 한도, API 키 없음) |
| LangSmith | 선택 | `LANGSMITH_TRACING=true`면 LangGraph 실행과 LLM 호출 기록을 보냄 |
| 윈도우 작업 스케줄러 | 예약 작업(아직 없음) | 저장소 폴더를 현재 폴더로 맞추는 `.ps1`을 거쳐 `python -m research_desk <명령>`을 부르는 방식 |
