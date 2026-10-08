# 운영

모든 명령은 **저장소 루트**에서 실행한다(`sessions/`와 `.env`의 상대 경로가 현재 폴더 기준). 셸은 Windows PowerShell 5.1(`powershell`)이다. 이 PC에는 PowerShell 7(`pwsh`)이 없다. 아래 `python`은 저장소의 `.venv\Scripts\python.exe`다.

## 처음 설치 (순서대로)

1. 파이썬 가상 환경과 패키지. 수집·분류만 쓰면 `requirements.txt`, 웹앱까지 쓰면 `requirements-workspace.txt`(FastAPI·uvicorn 추가), 테스트·커밋 검사까지 쓰면 `requirements-dev.txt`(pytest 추가, 앞의 둘 포함).
   ```powershell
   python -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements-dev.txt
   ```
2. 커밋 검사 켜기. 1번(pytest 설치)보다 먼저 하면 첫 커밋이 "파이썬/pytest 없음"으로 막힌다.
   ```powershell
   git config core.hooksPath .githooks
   ```
3. Supabase에 표 만들기: `migrations/`의 파일을 **번호 순서대로** SQL 편집기에서 한 번씩 실행한다. 순서를 바꾸면 앞 번호가 만든 열·제약이 없어 실패한다. 이미 쓰고 있는 DB에는 아직 적용하지 않은 번호만 실행한다(적용한 파일을 다시 돌리지 않는다).
4. `.env` 만들기: `copy .env.example .env` 뒤 값 채우기(아래 설정 목록). `.env`는 커밋하지 않는다.
5. 첫 수집: `python -m research_desk collect`. 처음 한 번 텔레그램 전화번호와 문자 인증 코드를 터미널에서 묻고, 성공하면 `sessions/<TELEGRAM_SESSION_NAME>.session`이 생긴다. 이 파일이 지워지면 다음 수집에서 다시 묻는다 — 사람이 입력할 수 없는 실행(예약 작업)은 거기서 멈추므로, 예약 수집을 붙이기 전에 세션 파일이 있는지 확인한다.
6. 웹앱 화면 빌드에는 Node.js 22.12 이상(또는 20.19 이상)이 필요하다.

## 명령

| 명령 | 하는 일 |
|---|---|
| `python -m research_desk collect` | 마지막 번호 다음부터 새 PDF 수집. `--dry-run`(쓰지 않고 목록만, 텔레그램 접속은 함), `--cutoff-days N`(첫 실행 전용), `--backfill-days N`(N일 되감기, 이미 받은 번호는 건너뜀), `-v` |
| `python -m research_desk tag run` | `pending` 행을 한 배치 분류하고 JSON 보고(`worker_id` 포함). `--batch-size`, `--model`, `--dry-run`(AI는 부르고 쓰지 않음), `--row-ids 1,2,3`(상태 무시하고 그 행만), `--max-concurrent-llm`, `--worker-id` |
| `python -m research_desk tag inspect` | 큐 분포 JSON: `pending`, `processing`, `auto`, `review_needed`, `verified`, `oos_total`, `last_24h` |
| `python -m research_desk tag escalate --since <ISO 시각>` | 그 시각 이후 분류된 `review_needed` 행을 `LLM_MODEL_ESCALATION`으로 다시 분류. 시각에 오프셋이 없으면 UTC로 읽는다(한국 시간이면 `+09:00`을 붙인다) |
| `python -m research_desk tag reset-worker --worker-id W` | 그 작업자의 `processing` 행을 `pending`으로 되돌림 |
| `python -m research_desk web [--view reports\|market\|review]` | 웹앱을 `http://127.0.0.1:8520/?view=<view>`에서 실행(Ctrl+C로 종료) |
| `python -m research_desk stocks set-version --as-of YYYY-MM-DD` | 종목표 버전 정보 파일 갱신 |

종료 코드: `collect`는 0 성공 / 1 전체 실패 / 2 일부 실패. `tag`·`stocks`는 0 정상 / 4 준비 문제(안내대로 고친 뒤 다시 실행 — 재시도로는 안 풀림) / 1 그 밖의 오류. 인자 오류는 모두 2.

## 웹앱 (Research Desk)

- 빌드와 실행을 한 번에: `powershell -File scripts\start-workspace.ps1`. `frontend/node_modules`가 없으면 `npm ci`, 그다음 `npm run build`, 마지막으로 `python -m research_desk web`. 빌드를 건너뛰려면 `-SkipBuild`.
- 이미 빌드했으면 `python -m research_desk web`만. 커버리지·검토 화면으로 바로 열려면 `--view market` / `--view review`.
- 화면 개발 중에는 `npm --prefix frontend run dev`(5173)를 파이썬 서버와 같이 띄운다. Vite가 `/api`를 8520으로 넘긴다.
- 서버를 켤 때 `frontend/dist`가 있어야 `/assets`가 연결된다. 빌드 없이 켜면 `/`가 "프론트엔드를 먼저 빌드해 주세요" 503이다 — 빌드 후 서버를 다시 켠다.
- 한 기능의 준비가 실패하면(DB 설정 없음, 종목표를 못 읽음, 분석 모델 키 없음) 그 기능 화면만 "○○ 기능을 지금 쓸 수 없습니다: 이유"로 막히고 나머지는 동작한다. 빠진 파일을 채우거나 `.env`에 빠진 값을 넣으면 다음 요청에 회복된다. 이미 있던 `.env` 값을 바꾼 것은 서버를 다시 켜야 한다.
- 검토 되돌리기는 서버 메모리에만 있어서, 서버를 다시 켜면 직전 처리를 되돌릴 수 없다.

## 운영 원칙 (위반 금지)

### 1. `MAX_CONCURRENT_LLM=2`가 분류의 운영 천장이다
- 실제 병목은 분당 요청 수가 아니라 **분당 토큰 한도(TPM)** 다. gpt-5.4-mini 실측: 한도 200K TPM, 동시성 2에서 피크 ≈134K(67%). 동시성 10이면 TPM 한도를 넘어 429가 잦아지고, 토큰 비용이 쌓이면서 처리량은 오히려 떨어진다.
- 위 수치는 **gpt-5.4-mini(OpenAI) 기준**이다. 태깅은 이제 `claude-haiku-5-5`(Anthropic)라 한도 체계(입력·출력 토큰 한도가 따로)와 행당 토큰(입력 ≈6K / 출력 ≈0.4K)이 다르다. 재검증 전까지 천장 2를 유지한다.
- 올리려면: Anthropic Console(Limits)에서 Haiku 5.5의 토큰 한도를 확인하고 → LangSmith 기록에서 토큰 추이를 검증한 뒤 → 한 단계씩(예: 3 → 4) 시도한다. 임의로 올리지 않는다.

### 2. 백필 배치 크기는 10이 정석이다
- `TAGGER_BATCH_SIZE_DEFAULT=10`. 반복 실행 스크립트가 이 단위로 처음부터 끝까지 검증됐다.
- 동시성 2가 묶는 조건이라 배치를 100으로 키워도 총 처리량은 같다. 10의 이점: 한 배치가 실패해도 되돌릴 범위가 10건으로 작고, 배치마다 JSON 보고가 자주 찍혀 지켜보기 쉽다.
- 임의 값(100, 200 등)을 쓰지 않는다.

### 3. 백필 실행
```powershell
powershell -File scripts\run-batches.ps1 -Iterations N -BatchSize 10
```
- 한 배치 ≈22~25초. 1,500회 ≈ 10시간이 약 15,000건 백필의 실측 추산이다(gpt-5.4-mini 기준). Haiku 5.5 시험 실행(dry-run)에서는 행당 2~11초였다 — 첫 실운영 백필에서 다시 잰다.
- Ctrl+C 해도 안전하다. 다시 실행하면 남은 `pending`부터 이어 간다(같은 행을 두 번 하지 않음).
- 스크립트는 저장소의 `.venv` 파이썬을 스스로 찾는다(순서: `-Python <경로>` → `$env:RESEARCH_DESK_PY` → 스크립트 위 폴더의 `.venv` → 기본 작업 폴더의 `.venv`). 마지막 줄은 `Summary: success=<성공 배치 수> failed_attempts=<실패 시도 수>`다.
- 스크립트 종료 코드: 0 모두 완료 / 1 한 배치가 3번 다 실패 / 2 파이썬 못 찾음 / 3 되돌리기(reset-worker) 자체 실패 / 4 준비 문제.

### 4. 자동 처리되는 사건 (수동 개입 금지)
| 사건 | 처리 |
|---|---|
| LLM 429·5xx·시간 초과·네트워크 오류 (Anthropic/OpenAI) | 분류기가 그 행을 `pending`으로 되돌림 → 다음 배치에서 재시도 |
| 행 하나가 90초(`PER_ROW_DEADLINE_S`) 초과 | 위와 같음 |
| 실행 자체가 비정상 종료(크래시) | 스크립트가 그 작업자 ID의 행만 되돌리고(`tag reset-worker`) 최대 2회 재시도 |
| **3회 연속 실패** | 스크립트가 종료 코드 1로 멈춤. **사람이 원인을 본다** — 사람이 개입하는 경우는 이것과 아래 두 줄(종료 코드 4, 3)뿐이다 |
| **준비 문제** (설정 누락, codex CLI 없음, 종목표를 못 읽음, 종목표 버전 불일치) | `tag run`이 행을 하나도 가져가지 않고 4로 끝남 → 스크립트가 되돌리기·재시도 없이 바로 4로 멈추고 `준비 문제로 멈춥니다. 위 안내를 따른 뒤 다시 실행하세요.`를 출력. **위에 찍힌 안내대로 고친 뒤 다시 실행한다** |
| 되돌리기(`tag reset-worker`) 자체 실패 | 스크립트가 3으로 멈춤 → 사람이 DB 연결을 확인하고 `tag inspect`로 `processing` 행을 본다 |

### 5. 알려진 무해한 경고 (디버깅하지 말 것)
- Pydantic serializer warning (`LLMExtraction` 직렬화 시) — 기능 영향 없음.
- Windows native crash `exit=-1073741569` — 스크립트가 자동 복구한다. 데이터 손실 없음이 검증됐다(3회 반복 테스트 중 1회 발생, 5건 자동 되돌림 후 재시도 성공).
- `tag run`·`tag escalate` 시작 때 LangGraph의 `LangChainPendingDeprecationWarning` — 기능 영향 없음.

## 모니터링

```powershell
# 큐 분포 (Supabase)
python -m research_desk tag inspect

# LLM 호출 오류 기록 (LangSmith — 로그인 필요, 프로젝트 이름은 .env의 LANGSMITH_PROJECT, 운영은 telegram_report)
langsmith trace list --project telegram_report --error --last-n-minutes 30
```

**정상 기준 (2026-05, 이전 분류 모델 기준):**
- 신뢰도: high ≈79% / medium ≈18% / low ≈3%
- `review_needed` ≈2.6%
- 분석 대상 외 ≈6% (`ir_self`가 가장 많음)
- 검토 사유 분포: `krx_unmatched_in_scope` > `type_indeterminate` > `first_page_unreadable`

이 비율이 짧은 시간에 크게 흔들리면 백필을 멈추고 원인을 찾는다. 태깅 모델이 2026-10-08부터 `claude-haiku-5-5`로 바뀌어 위 기준(이전 모델 기준)과 다소 다를 수 있다. 행에 모델 이름은 저장되지 않으므로, 모델별로 나눠 볼 때는 `tagged_at`으로 구분한다.

**배치 보고에서 볼 것.** 키가 틀리거나 만료돼 모든 LLM 호출이 거절돼도 `tag run`은 0으로 끝나고 행은 모두 `pending`으로 돌아간다. 반복 실행 스크립트는 멈추지 않고 다음 배치를 계속 돈다. 배치 JSON의 `auto`·`review_needed`가 0이고 `unhandled_errors`나 `transient_errors`가 배치 크기만큼이면 스크립트를 Ctrl+C로 멈추고 키·로그인·공급자 상태를 확인한다. 같은 행이 매번 오류를 내면 되돌려진 뒤 다음 배치에서 다시 먼저 잡혀 배치마다 한 자리를 차지한다(자동으로 검토로 넘기는 장치는 없다).

## AI 모델 구성

| 용도 | 설정 변수 | 코드 기본값 | 공급자 |
|---|---|---|---|
| 태깅 | `LLM_MODEL_DEFAULT` | `claude-haiku-5-5` | Anthropic (`ANTHROPIC_API_KEY`) |
| 태깅 재처리(`tag escalate`) | `LLM_MODEL_ESCALATION` | `gpt-5.4` | OpenAI (`OPENAI_API_KEY`) |
| 재무 분석·리포트 비교(웹앱) | `LLM_MODEL_PHASE2` | `gpt-6-luna` (운영 `.env`는 `codex:gpt-6-luna`) | `codex:` 접두사 → 로컬 `codex exec` |

- **모델 이름이 공급자를 정한다:** `claude-*` → Anthropic API, `codex:<모델>` → 로컬 Codex CLI(ChatGPT 로그인 한도, API 키 불필요), 그 밖 → OpenAI API. 바꾸거나 되돌릴 때는 `.env`의 모델 이름만 고치고 워커·웹앱을 다시 켠다. 옛 이름 `OPENAI_MODEL_*`도 `LLM_MODEL_*`이 없을 때 읽는다.
- 분류 명령은 시작할 때 **실제로 쓸 모델**의 키만 확인한다(`tag run`은 태깅 모델, `tag escalate`는 재처리 모델, `--model`을 주면 그 모델). `tag inspect`·`tag reset-worker`는 키가 필요 없다.
- 웹앱은 분석·비교 해석문을 실제로 부르기 직전에만 키·codex를 확인한다. 없으면 `분석 기능을 지금 쓸 수 없습니다: …`로 막히고, 목록·재사용·검토는 그대로 동작한다.
- 재무 분석이 `codex:gpt-6-luna`인 이유: 사용자가 고른 리포트만 분석하므로 호출량이 적어 로그인 한도로 충분하다. Haiku는 재무 지표를 절반만 뽑고 형식 실패가 12.5%라서 뺐다(2026-10-08, 12건 비교).
- 재무 분석 응답(`ExtractionResult`)은 스키마가 커서, Claude 모델에서는 형식 강제(grammar) 대신 "스키마를 프롬프트에 넣고 받은 JSON 글자를 pydantic으로 검증"하는 경로(`constrained=False`)를 쓴다. 태깅과 비교는 형식 강제를 쓴다.
- 사고 깊이: Claude는 `ANTHROPIC_EFFORT`(기본 `medium`), Codex는 `CODEX_REASONING_EFFORT`(기본 `high`. `medium`은 빠르지만 재무 지표가 약 40% 적게 나온다). `codex` 실행 파일 위치는 `CODEX_BIN`(없으면 PATH).
- Codex 로그인이 만료되면 `codex login`으로 다시 로그인한다. 사용 한도를 넘으면 한도가 풀릴 때까지 기다리거나, `.env`의 `LLM_MODEL_PHASE2`를 API 모델(`gpt-6-luna`)로 바꾸고 웹앱을 다시 켠다.
- 모델을 바꿔도 저장된 분석은 원래 모델 정보를 유지한 채 재사용된다. 모델 변경이 일괄 재분석을 일으키지 않는다.

## 설정 목록 (`.env`)

| 변수 | 쓰는 곳 | 필수 / 기본값 | 뜻 |
|---|---|---|---|
| `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` | collect | 필수 | my.telegram.org에서 받은 앱 값 |
| `TELEGRAM_CHANNEL` | collect | 필수 | 채널 사용자 이름. DB의 채널 라벨(`chat_username`)이 된다 — 바꾸면 기존 행과 다른 채널로 취급돼 중복 수집된다 |
| `TELEGRAM_CHANNEL_ID` | collect | 선택 | 있으면 텔레그램 조회에만 이 숫자 ID를 쓴다(라벨은 그대로 `TELEGRAM_CHANNEL`) |
| `TELEGRAM_SESSION_NAME` | collect | `samstudy` | 세션 파일 `sessions/<이름>.session` |
| `INITIAL_CUTOFF_DAYS` | collect | 30 | DB가 비어 있을 때 첫 실행이 거슬러 가는 날 수 |
| `MAX_CONCURRENT_DOWNLOADS` | collect | 4 | 동시 다운로드 수. 재시도와 새 수집이 한 한도를 나눠 쓴다. FloodWait 경고가 없을 때 백필에서 8까지 |
| `LOG_LEVEL` | collect | INFO | `-v`면 DEBUG |
| `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` | collect(필수), 웹의 DB 기능 | — | 없으면 웹의 리포트·커버리지·검토·비교가 사용 불가 |
| `SUPABASE_DB_URL` | 모든 `tag` 명령 | 필수 | Postgres 직접 연결 주소. 없으면 `SUPABASE_DB_URL is required`, 4 |
| `STORAGE_BASE_DIR` | collect, tag, web | `./reports` | PDF 저장 폴더. 세 단계가 같은 값을 써야 한다 |
| `KRX_CSV_PATH` | tag run/escalate, web, stocks set-version | `docs/stock_data/KRX_stocks_data.csv` | 종목표. 버전 정보 파일은 같은 폴더의 `<이름>.version.json` |
| `LLM_MODEL_DEFAULT` / `LLM_MODEL_ESCALATION` / `LLM_MODEL_PHASE2` | tag run / tag escalate / 웹 분석·비교 | 위 모델 표 | 옛 이름 `OPENAI_MODEL_*`도 읽음 |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | 모델 이름이 정한 공급자 | 쓰는 공급자 것만 | |
| `ANTHROPIC_EFFORT` / `CODEX_REASONING_EFFORT` / `CODEX_BIN` | AI 호출 | medium / high / PATH의 codex | |
| `MAX_CONCURRENT_LLM` | tag | **2** | 위 운영 원칙 1 |
| `TAGGER_BATCH_SIZE_DEFAULT` | tag | **10** | 위 운영 원칙 2 |
| `LOCK_TTL_MINUTES` / `PER_ROW_DEADLINE_S` | tag | 30 / 90 | 오래된 잠금 기준(분) / 행 하나의 시간 한도(초). `LOCK_TTL_MINUTES × 60`이 배치 하나의 최장 시간(⌈배치 크기 ÷ 동시 처리 수⌉ × 행 한도, 기본 7.5분)보다 길어야 한다 |
| `PHASE2_PER_REPORT_TIMEOUT_S` | 웹 분석·비교 | 180 | 호출 한 번의 시간 한도. 재무 추출은 지표를 최대 48개 뽑아 gpt-6-luna 기준 최대 ≈105초 걸린다 |
| `PHASE2_MAX_INPUT_TOKENS` | 웹 분석 | 30000 | 넣을 PDF 글자의 토큰 어림 한도(글자 수 ÷ 3) |
| `PHASE2_SUMMARY_VERSION` | 웹 분석 | `llm-summary@1.0` | 화면에 보일 분석 결과 버전. 바꾸면 기존 분석이 화면에서 사라진다 |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, `LANGSMITH_ENDPOINT` | AI 호출 기록 | 선택 | `LANGSMITH_TRACING=true`이고 키가 있으면 LangGraph 실행과 LLM 호출을 LangSmith에 기록 |

`.env`에 남아 있는 `HEARTBEAT_ENABLED`·`HEARTBEAT_INTERVAL_S`·`PHASE2_MAX_CONCURRENT`는 아무도 읽지 않는다. 지워도 된다.

## 종목표 갱신

1. `docs/stock_data/KRX_stocks_data.csv`를 새 파일로 바꾼다.
2. `python -m research_desk stocks set-version --as-of <자료 기준일, YYYY-MM-DD>` — 새 버전 이름(`KRX@…`)이 찍히고 0이면 성공. 날짜·머리줄이 틀리면 이유를 찍고 4, 버전 정보 파일은 그대로다.
3. 이 명령을 실행하기 전까지 `tag run`·`tag escalate`는 4로 멈추고, 웹앱은 경고 로그만 남긴다.
4. CSV와 버전 정보 파일을 한 커밋에 올린다.

## DB 마이그레이션 적용

- `migrations/NNN_*.sql`을 Supabase SQL 편집기나 직접 연결로 **한 번** 실행한다. 자동 적용 장치는 없다.
- 데이터를 바꾸는 파일(예: `007_normalize_taxonomy_version.sql`)은 `BEGIN`/`COMMIT` 없이 쓰여 있다. 적용할 때 반복 분류·수집·웹앱을 멈추고, 바뀔 행을 백업한 뒤, 트랜잭션 안에서 실행하고 바뀐 행 수를 확인한 다음 커밋한다.
- 구조를 바꾸는 파일(001~006)은 파일 안에 트랜잭션이 들어 있는 것도 있다. 파일을 그대로 실행한다.

## 테스트

```powershell
.venv\Scripts\python.exe -m pytest
```
`research_desk/` 아래 테스트만 모은다(구조 검사 포함). DB·AI·텔레그램에 접속하지 않고 `.env`를 읽지 않는다. 커밋 검사가 같은 것을 돌린다. 파이프로 결과를 받아 읽을 때는 `$env:PYTHONIOENCODING='utf-8'`을 먼저 둔다(한국어 메시지가 `\uXXXX`로 깨지지 않게).
