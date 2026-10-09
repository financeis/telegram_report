# 운영

모든 명령은 **저장소 루트**에서 실행한다(`sessions/`와 `.env`의 상대 경로가 현재 폴더 기준). 셸은 Windows PowerShell 5.1(`powershell`)이다. 이 PC에는 PowerShell 7(`pwsh`)이 없다. 아래 `python`은 저장소의 `.venv\Scripts\python.exe`다.

## 처음 설치 (순서대로)

1. 파이썬 가상 환경과 패키지. 수집·분류·주가 갱신(`prices update`)·`peers inspect`만 쓰면 `requirements.txt`(MongoDB용 pymongo, KIS용 httpx 포함), 웹앱과 유사도 계산(`peers build`)까지 쓰면 `requirements-workspace.txt`(FastAPI·uvicorn 추가 — `peers build`는 실행 중에 리포트 기능을 거쳐 FastAPI를 불러온다), 테스트·커밋 검사까지 쓰면 `requirements-dev.txt`(pytest 추가, 앞의 둘 포함).
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
| `python -m research_desk tag requeue <조건…> [--apply]` | 이미 분류된 `auto`·`review_needed` 행 가운데 조건에 맞는 것을 `pending`으로 되돌림(다시 분류는 평소 백필). 조건 `--unreadable`, `--publisher-not-in-dictionary`, `--publisher-filename-mismatch`, `--publisher-type-mismatch`, `--krx-unmatched` 중 하나 이상. 기본은 미리 보기(아무것도 쓰지 않음), `--apply`면 백업 CSV를 먼저 쓰고 한 트랜잭션으로 되돌림. AI를 부르지 않는다. 아래 "다시 분류 대기" |
| `python -m research_desk web [--view reports\|market\|review]` | 웹앱을 `http://127.0.0.1:8520/?view=<view>`에서 실행(Ctrl+C로 종료) |
| `python -m research_desk stocks set-version --as-of YYYY-MM-DD` | 종목표 버전 정보 파일 갱신 |
| `python -m research_desk prices update` | 종목표 전 종목의 주가를 KIS에서 받아 주가 스냅샷과 실행 기록을 갱신(아래 "매일 주가 갱신"). `--codes 005930,080220`은 그 종목만 받아 계산 결과를 출력하고 아무것도 저장하지 않는 확인용 |
| `python -m research_desk peers build` | 사업보고서로 회사 프로필·임베딩을 만들고 유사도 계산 결과를 공개(아래 "유사 기업 계산"). `--fiscal-year N`, `--codes …`·`--limit n`(대상 줄이기), `--pilot`(시험 실행: 공개하지 않고 회사마다 비슷한 회사 상위 10 출력) |
| `python -m research_desk peers inspect` | 회계연도·프로필 버전·상태별 프로필 수와 토큰, 최근 빌드 10개를 JSON으로 |

종료 코드: `collect`는 0 성공 / 1 전체 실패 / 2 일부 실패. `tag`·`stocks`·`prices`·`peers`는 0 정상 / 4 준비 문제(아무것도 바꾸지 않았다 — 안내대로 고친 뒤 다시 실행한다. 재시도로는 안 풀린다) / 1 그 밖의 오류. `prices update`는 실행이 `failed`(받지 못한 종목이 20% 초과)여도 1, `peers build`는 `incomplete`·분류 작업 진행 중·다른 빌드 진행 중도 1, `tag requeue --apply`는 이 PC에서 백필·재처리·수집·웹앱이 돌고 있어 거절할 때 1이다(아무것도 바꾸지 않았다 — 그 작업이 끝나거나 끈 뒤 다시 실행한다). 인자 오류는 모두 2.

## 웹앱 (Research Desk)

- 빌드와 실행을 한 번에: `powershell -File scripts\start-workspace.ps1`. `frontend/node_modules`가 없으면 `npm ci`, 그다음 `npm run build`, 마지막으로 `python -m research_desk web`. 빌드를 건너뛰려면 `-SkipBuild`.
- 이미 빌드했으면 `python -m research_desk web`만. 커버리지·검토 화면으로 바로 열려면 `--view market` / `--view review`.
- 화면 개발 중에는 `npm --prefix frontend run dev`(5173)를 파이썬 서버와 같이 띄운다. Vite가 `/api`를 8520으로 넘긴다.
- 서버를 켤 때 `frontend/dist`가 있어야 `/assets`가 연결된다. 빌드 없이 켜면 `/`가 "프론트엔드를 먼저 빌드해 주세요" 503이다 — 빌드 후 서버를 다시 켠다.
- 한 기능의 준비가 실패하면(DB 설정 없음, 종목표를 못 읽음, 분석 모델 키 없음) 그 기능 화면만 "○○ 기능을 지금 쓸 수 없습니다: 이유"로 막히고 나머지는 동작한다. 빠진 파일을 채우거나 `.env`에 빠진 값을 넣으면 다음 요청에 회복된다. 이미 있던 `.env` 값을 바꾼 것은 서버를 다시 켜야 한다.
- 검토 되돌리기는 서버 메모리에만 있어서, 서버를 다시 켜면 직전 처리를 되돌릴 수 없다.
- 유사 기업 탭과 테마 검색은 공개된 유사도 계산 결과(`peers build`가 `done`으로 끝난 빌드)가 있어야 동작한다. 없으면 `유사 기업 기능을 지금 쓸 수 없습니다: 아직 공개된 유사도 계산 결과가 없습니다(…)`. 새 빌드가 `done`이 되면 웹앱을 다시 켜지 않아도 다음 요청부터 쓴다. 테마 검색에는 `OPENAI_API_KEY`도 있어야 한다.
- 모든 화면 맨 위 상태 줄이 주가·리포트 기준일을 보여 준다. 빨간색이면 문장을 보고 원인을 찾는다: 주가가 밀렸으면 작업 스케줄러의 마지막 실행 결과와 `price_update_runs`의 최근 행, 리포트가 밀렸으면 수집(`collect`)이 멈췄는지와 분류 대기(`tag inspect`의 `pending`). 회색 `자료 기준일 확인 불가`면 DB 설정을 본다.

## 매일 주가 갱신

준비(순서대로 — 앞 단계가 없으면 뒤 단계가 실패한다):
1. `.env`에 `KIS_APP_KEY`, `KIS_APP_SECRET`(한국투자증권 실전 계정)을 넣는다. 없으면 `prices update`가 4로 멈춘다.
2. 마이그레이션 008을 적용한다(아래 "DB 마이그레이션 적용"). 적용 전에는 `--codes` 확인 실행도 저장된 스냅샷을 읽다가 표가 없어 1로 끝난다.
3. KIS 확인(아무것도 저장하지 않는다 — 스냅샷·실행 기록·상태 줄 모두 그대로):
   ```powershell
   python -m research_desk prices update --codes 005930,080220
   ```
   두 종목의 계산 결과 JSON과 `확인용 실행이라 아무것도 저장하지 않았습니다: …` 한 줄이 나오고 0이면 KIS 접속이 된다. 저장된 스냅샷이 아직 없으면 초과수익률은 비어 나온다(정상). `market_cap`·`traded`·`flags`를 HTS 화면과 한 번 대조한다.
4. 전체 실행을 한 번 손으로 돌린다: `powershell -File scripts\run-prices.ps1`. 마지막 줄이 `주가 갱신이 끝났습니다.`이고 종료 코드 0이면 된다. 상태 줄의 주가 기준일이 바뀐다.
5. 윈도우 작업 스케줄러에 등록한다. 기본 작업 폴더(git worktree가 아닌 저장소 폴더)의 루트에서:
   ```powershell
   $repo = (Get-Location).Path
   schtasks /Create /TN "ResearchDesk-prices-update" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 18:30 /TR "powershell -NoProfile -ExecutionPolicy Bypass -File $repo\scripts\run-prices.ps1"
   ```
   경로에 공백이 있으면 `/TR` 안의 경로를 `\"…\"`로 감싼다. 시각은 사용자의 masterdb 프로젝트의 KIS 수집 시각과 겹치지 않게 고른다(아래 운영 원칙 7). 18:30이 아닌 시각으로 바꾸면 상태 줄의 기준 시각(20:00, `features/freshness/logic.py`의 `CUTOFF`)이 실행이 끝난 뒤인지 확인한다. 확인: `schtasks /Run /TN "ResearchDesk-prices-update"` 뒤 상태 줄과 `schtasks /Query /TN "ResearchDesk-prices-update" /V /FO LIST`의 마지막 결과(0이면 성공, 1 실패, 4 준비 문제).

결과 읽기:
- 0: `ok`(모두 받음) 또는 `partial`(받지 못한 종목 20% 이하 — 그 종목은 이전 값에 `no_data` 표시).
- 1: `failed`(20% 초과 — 스냅샷은 하나도 바뀌지 않음), 접근 토큰을 못 받음, 실행 중 오류. 요약 줄과 `price_update_runs`의 `message`(못 받은 종목과 첫 실패 이유)를 본다. 다시 돌리면 처음부터 다시 받는다.
- 4: 준비 문제. 찍힌 이유대로 고친다.
- PC가 꺼져 있거나 잠들어 스케줄러가 그 시각을 건너뛰면 실행 기록이 없고, 상태 줄이 그날 20:00부터 빨간색이 된다. 손으로 `scripts\run-prices.ps1`을 돌리면 된다.

## 유사 기업 계산 (연 1회)

준비(모두 갖춘 뒤 시작한다):
- 마이그레이션 008 적용, `python -m research_desk peers inspect`가 0으로 끝남(표가 보인다).
- 로컬 MongoDB가 켜져 있고 `FS.A001_v2`(`DART_MONGO_*`)에 그 회계연도 사업보고서가 `parser_version` 0.2.0 이상으로 들어 있음 — 별도 저장소의 DART 수집 프로그램(dart-fss-text, 로컬 갈래 `local/text-db-v2`의 `poetry run python ingest_local.py --schema v2`)이 채운다. 2026-10-10 지금은 2024년 결산 939곳(로컬 원본으로 만든 임시분)만 있어 `--fiscal-year 2024`로 빌드했다(`docs/tracking/findings.md`).
- `.env`: 프로필 모델과 재처리 모델의 키(기본 `claude-sonnet-5-5` → `ANTHROPIC_API_KEY`, `gpt-5.4` → `OPENAI_API_KEY`), 임베딩용 `OPENAI_API_KEY`.
- 웹 패키지 설치(`requirements-workspace.txt`). 없으면 분류 진행 확인에서 `ModuleNotFoundError`로 1.
- 종목표가 버전 정보와 맞음(아니면 4, 분류와 같은 안내).
- 텔레그램 수집·분류가 따라잡혀 있음 — 리포트 수가 그만큼 정확하다(빌드가 막지는 않지만 화면의 "리포트 없음"이 틀린다).
- 분류 백필이 돌고 있지 않음(아래 운영 원칙 6).

순서:
1. 시험 실행: `python -m research_desk peers build --pilot --codes 080220,032580,005930`처럼 몇 곳(또는 `--limit 50`). 화면에는 나오지 않고, 대상 회사마다 회사·부문 유사도 상위 10 표와 요약 JSON이 찍힌다. 이웃 목록이 그럴듯한지, `failed`·`kept_previous`가 없는지 본다. 시험 실행의 프로필은 저장되어 다음 실행이 재사용한다(AI를 다시 부르지 않음).
   - 동의어는 `research_desk/features/peers/synonyms.yaml`에 더한다(AI를 다시 부르지 않고, 다음 공개 빌드가 반영한다). 프롬프트·응답 모양을 고치는 것은 코드 변경이고, 프로필 버전 기본값(`PEERS_PROFILE_VERSION`)을 함께 올려야 한다 — 다음 빌드가 모든 회사를 다시 추출한다.
   - 프로필 모델(`LLM_MODEL_PEERS`)은 2026-10-10 시험 실행 뒤 사용자가 정한 `claude-sonnet-5-5`다(`docs/llm-models.md`). 다른 모델과 비교할 때는 모델마다 `.env`의 `PEERS_PROFILE_VERSION`을 다른 시험용 값(예: `peer-profile@1.2-try-gpt`)으로 두고 같은 회사로 시험 실행한다 — 같은 프로필 버전이면 모델을 바꿔도 저장된 프로필을 재사용해 새 모델이 불리지 않는다. 고른 뒤에는 시험용 값을 지우고 사용자와 정한 모델로 본 실행을 한다.
2. 비용·시간 보고: 요약 JSON의 `tokens`(입력·출력·임베딩)와 `peers inspect`의 상태별 토큰 합계를 회사 수로 나눠, 전체 대상 회사 수만큼의 토큰·비용·시간을 어림해 사용자에게 보고하고 승인받는다.
3. 전체 실행: `python -m research_desk peers build`. 회사 수와 모델에 따라 몇 시간이 걸릴 수 있다. 끝에 요약 JSON:
   - `"status": "done"`(0): 공개됨. 웹앱을 다시 켜지 않아도 다음 요청부터 쓴다.
   - `"status": "incomplete"`(1): 성공이 대상의 95% 미만. 실패 원인(`peers inspect`, 빌드 기록의 `message`)을 본 뒤 그대로 다시 실행하면 성공한 회사는 재사용하고 나머지만 한다.
   - Ctrl+C·오류(1): 빌드는 `failed`로 닫힌다. 다시 실행하면 이미 만든 프로필을 재사용한다.
4. 화면 확인: 제주반도체(080220) 기업 화면의 `유사 기업` 탭, `테마로 기업 찾기`에서 "레거시 DRAM", 상태 줄. FY2024 임시 빌드에는 제주반도체가 없어 피델릭스(032580)로 본다.

주의:
- 시험은 늘 `--pilot`으로 한다. `--codes`·`--limit`만 주고 `--pilot`을 빼면, 줄인 대상의 95%가 성공하는 순간 그 빌드가 공개(`done`)되어 화면이 바로 그것을 쓴다(기준 표시가 `FY… · n/n곳`으로 작게 보인다). 백분위표·용어표는 그 버전의 `ok` 프로필 전체로 계산되므로 결과가 틀리지는 않지만, 공개 빌드 보존 3개 중 하나를 차지한다.
- 빌드는 한 번에 하나다. 다른 빌드가 6시간 안에 진행했으면 새 실행은 1로 끝난다. 프로세스가 죽어 `running`으로 남은 빌드는 6시간이 지나면 다음 실행이 `failed`로 정리한다.
- 빌드는 Ctrl+C로 멈춘다. 작업 관리자 등으로 프로세스를 강제로 끄면 빌드 행이 `running`으로 남아 6시간 동안 새 실행이 1로 거절된다. 6시간을 기다릴 수 없으면 `peer_builds`의 그 행을 백업한 뒤 한 트랜잭션에서 `status = 'failed'`, `finished_at = now()`, 이유를 `message`에 적고 바뀐 행이 1인지 확인해 닫는다(2026-10-10에 한 번 했다, 백업 `backups/peers/`). 만든 프로필은 남아 다음 실행이 재사용한다.
- 동시 호출 수는 `PEERS_MAX_CONCURRENT_LLM`(기본 2, 절대 규칙 1)이다. 2026-10-10 FY2024 빌드는 사용자 요청으로 그 실행에만 8로 돌렸다(`$env:PEERS_MAX_CONCURRENT_LLM='8'`). 그 전에 공급자 응답 헤더로 분당 한도를 확인했다: Sonnet 5.5 입력 500만·출력 100만 토큰, `gpt-5.4` 100만 토큰, 둘 다 5,000회. 쓰인 양은 한도의 15% 안팎이었고 429는 0번, 857곳을 약 15분에 마쳤다. 그달 LangSmith 기록 한도가 차 있어 토큰 추이 검증은 하지 못했다. 기본값은 그대로 2다.
- 같은 회계연도·프로필 버전으로 빌드가 도는 동안, 다시 추출된 회사(보고서 정정, 파서 버전 변경)는 그 빌드가 끝날 때까지 지금 공개된 빌드의 화면에서 "이 종목은 유사 기업 자료가 없습니다."로 보일 수 있다.

## 운영 원칙 (위반 금지)

### 1. `MAX_CONCURRENT_LLM=2`가 분류의 운영 천장이다
- 실제 병목은 분당 요청 수가 아니라 **분당 토큰 한도(TPM)** 다. gpt-5.4-mini 실측: 한도 200K TPM, 동시성 2에서 피크 ≈134K(67%). 동시성 10이면 TPM 한도를 넘어 429가 잦아지고, 토큰 비용이 쌓이면서 처리량은 오히려 떨어진다.
- 위 수치는 **gpt-5.4-mini(OpenAI) 기준**이다. 태깅은 이제 `claude-haiku-5-5`(Anthropic)라 한도 체계(입력·출력 토큰 한도가 따로)와 행당 토큰(입력 ≈6K / 출력 ≈0.4K)이 다르다. 재검증 전까지 천장 2를 유지한다.
- 올리려면: Anthropic Console(Limits)에서 Haiku 5.5의 토큰 한도를 확인하고 → LangSmith 기록에서 토큰 추이를 검증한 뒤 → 한 단계씩(예: 3 → 4) 시도한다. 임의로 올리지 않는다.

### 2. 백필 배치 크기는 10이 정석이다
- `TAGGER_BATCH_SIZE_DEFAULT=10`. 예전 반복 실행 스크립트와 옛 명령으로 이 단위가 처음부터 끝까지 검증됐다. 새 명령 체계의 스크립트는 2026-10-09에 가짜 파이썬으로 흐름(0·1·3·4 종료)만 확인했다.
- 동시성 2가 묶는 조건이라 배치를 100으로 키워도 총 처리량은 같다. 10의 이점: 한 배치가 실패해도 되돌릴 범위가 10건으로 작고, 배치마다 JSON 보고가 자주 찍혀 지켜보기 쉽다.
- 임의 값(100, 200 등)을 쓰지 않는다.

### 3. 백필 실행
```powershell
powershell -File scripts\run-batches.ps1 -Iterations N -BatchSize 10
```
- 한 배치 ≈22~25초. 1,500회 ≈ 10시간이 약 15,000건 백필의 실측 추산이다(gpt-5.4-mini 기준). Haiku 5.5 첫 실운영 백필(2026-10-09)에서도 한 배치 ≈22~25초였다. 그림으로 읽는 행이 섞인 배치는 더 걸린다(2026-10-10 재분류에서 25~60초).
- Ctrl+C 해도 안전하다. 다시 실행하면 남은 `pending`부터 이어 간다(같은 행을 두 번 하지 않음).
- 스크립트는 저장소의 `.venv` 파이썬을 스스로 찾는다(순서: `-Python <경로>` → `$env:RESEARCH_DESK_PY` → 스크립트 위 폴더의 `.venv` → 기본 작업 폴더의 `.venv`). 종료 코드 0·1·4로 끝날 때 마지막 줄은 `Summary: success=<성공 배치 수> failed_attempts=<실패 시도 수>`다(2·3으로 끝날 때는 오류 문구로 끝난다).
- 스크립트 종료 코드: 0 모두 완료 / 1 한 배치가 3번 다 실패 / 2 파이썬 못 찾음 / 3 되돌리기(reset-worker) 자체 실패 / 4 준비 문제.

### 4. 자동 처리되는 사건 (수동 개입 금지)
| 사건 | 처리 |
|---|---|
| LLM 429·5xx·시간 초과·네트워크 오류 (Anthropic/OpenAI) | 분류기가 그 행을 `pending`으로 되돌림 → 다음 배치에서 재시도 |
| 행 하나가 90초(`PER_ROW_DEADLINE_S`) 초과 | 위와 같음 |
| 실행 자체가 비정상 종료(크래시) | 스크립트가 그 작업자 ID의 행만 되돌리고(`tag reset-worker`) 최대 2회 재시도 |
| 글자가 없는 PDF(쪽 전체가 그림) | 분류기가 1쪽 그림을 AI에게 보여 주고 `medium` + 메모 `page_image`로 마감. 그림도 못 만들면 검토 대기(`first_page_unreadable`) |
| AI가 사전에 없는 발행처 이름을 답함 | 분류기가 발행처를 비워서 저장(행은 실패하지 않음). 파일 이름 표기와 다르거나 모르면 `medium` + 메모 `publisher_suspect:…` |
| **3회 연속 실패** | 스크립트가 종료 코드 1로 멈춤. **사람이 원인을 본다** |
| **준비 문제** (설정 누락, codex CLI 없음, 종목표를 못 읽음, 종목표 버전 불일치) | `tag run`이 행을 하나도 가져가지 않고 4로 끝남 → 스크립트가 되돌리기·재시도 없이 바로 4로 멈추고 `준비 문제로 멈춥니다. 위 안내를 따른 뒤 다시 실행하세요.`를 출력. **위에 찍힌 안내대로 고친 뒤 다시 실행한다** |
| 되돌리기(`tag reset-worker`) 자체 실패 | 스크립트가 3으로 멈춤 → 사람이 DB 연결을 확인하고 `tag inspect`로 `processing` 행을 본다 |

사람이 나서는 경우는 스크립트가 0 아닌 코드로 멈췄을 때(1·2·3·4)와, 아래 "배치 보고에서 볼 것"처럼 스크립트는 계속 도는데 배치마다 모든 행이 오류로 되돌려질 때다. 그 밖의 사건은 자동으로 처리되니 손대지 않는다.

### 5. 알려진 무해한 경고 (디버깅하지 말 것)
- Pydantic serializer warning (`LLMExtraction` 직렬화 시) — 기능 영향 없음.
- Windows native crash `exit=-1073741569`(드물게 `-1073741784`) — 스크립트가 그 작업자의 행을 되돌리고 다시 시도해 자동 복구한다(데이터 손실 없음). 다만 한 배치가 3번 연속 나면 스크립트가 1로 멈춘다.
  - 2026-10-09 백필에서 `tag run` 937번 중 131번 났다. 처음 120여 배치에서는 없다가 그 뒤 몇 분에 한 번꼴이었고, 같은 행을 다시 돌리면 성공했다. 충돌 직후 배치에서 글자가 있는 PDF 1건이 `first_page_unreadable`로 저장된 일도 있었다.
  - 원인 추정: 분류기가 PDF 두 개를 두 스레드에서 동시에 읽었다(PyMuPDF는 여러 스레드 동시 사용을 지원하지 않는다). 2026-10-10에 PDF 접근을 프로세스 안에서 줄 세웠다(`core.pdf` 잠금).
  - 잠금 뒤 재측정: 2026-10-10 재분류 백필(첫 묶음의 앞부분)에서 44번 실행 중 0번 — 2026-10-09 백필의 937번 중 131번보다 크게 줄었다. 실행 수가 아직 적으니 남은 백필에서도 센다.
- `tag run`·`tag escalate` 시작 때 LangGraph의 `LangChainPendingDeprecationWarning` — 기능 영향 없음.

### 6. 유사도 계산과 분류 백필을 겹쳐 돌리지 않는다
- 둘 다 AI를 동시 2개로 부른다(`PEERS_MAX_CONCURRENT_LLM`, `MAX_CONCURRENT_LLM`). 겹치면 4개가 되어 분당 토큰 한도를 넘는다. `PEERS_MAX_CONCURRENT_LLM`도 원칙 1과 같은 절차 없이 올리지 않는다.
- `peers build`는 분류 작업이 진행 중이면 시작하지 않는다(종료 코드 1, `분류 작업이 진행 중이라…`). 그러나 이 확인은 정상 `tag run`이 지금 가져간 행만 본다. 반복 분류 스크립트의 배치 사이(다음 배치가 뜨는 몇 초)나 `tag escalate`·`tag run --row-ids`가 도는 중이면 통과한다. 백필 창이 열려 있으면 빌드를 시작하지 않는다.
- 반대 방향은 자동으로 막지 않는다. 유사도 계산이 도는 동안(`peers inspect`의 `builds`에 `running`이 있으면) 분류 백필을 시작하지 않는다.

### 7. KIS 수집은 masterdb와 같은 시간에 돌리지 않는다
- `KIS_APP_KEY`는 사용자의 다른 프로젝트(masterdb)와 같은 키다. 같은 키로는 초당 호출 한도와 접근 토큰 발급(1분에 한 번쯤)을 나눠 쓴다. 겹치면 토큰을 못 받아 `prices update` 실행 전체가 `failed`(1)로 끝나거나, 한도 초과 재시도가 늘어 실패 종목이 생긴다.
- 작업 스케줄러 시각을 정하거나 손으로 돌리기 전에 masterdb 쪽 KIS 수집 시각을 확인한다. 토큰 실패 직후 다시 돌릴 때는 1분 이상 기다린다.

## 모니터링

```powershell
# 큐 분포 (Supabase)
python -m research_desk tag inspect

# LLM 호출 오류 기록 (LangSmith — 로그인 필요, 프로젝트 이름은 .env의 LANGSMITH_PROJECT, 운영은 telegram_report)
langsmith trace list --project telegram_report --error --last-n-minutes 30

# 유사도 계산: 회계연도·프로필 버전·상태별 프로필 수와 토큰, 최근 빌드 10개(상태·대상·성공·실패·시각·메시지)
python -m research_desk peers inspect
```

- 예약 작업은 웹앱의 상태 줄로 본다(주가 기준일·리포트 기준일, 밀리면 빨간색과 이유). 주가 실행의 자세한 기록은 Supabase SQL 편집기에서 `select * from price_update_runs order by started_at desc limit 5;`(상태·성공·실패 수·메시지).

**정상 기준 (2026-05, 이전 분류 모델 기준):**
- 신뢰도: high ≈79% / medium ≈18% / low ≈3%
- `review_needed` ≈2.6%
- 분석 대상 외 ≈6% (`ir_self`가 가장 많음)
- 검토 사유 분포: `krx_unmatched_in_scope` > `type_indeterminate` > `first_page_unreadable`

이 비율이 짧은 시간에 크게 흔들리면 백필을 멈추고 원인을 찾는다. 태깅 모델이 2026-10-08부터 `claude-haiku-5-5`로 바뀌어 위 기준(이전 모델 기준)과 다소 다를 수 있다. 행에 모델 이름은 저장되지 않으므로, 모델별로 나눠 볼 때는 `tagged_at`으로 구분한다.

**그림·발행처 표시 (설계상 `medium` 비율이 오른다).** 배치 JSON의 `page_image`(1쪽 그림으로 읽은 행), `page_image_unsupported`(그림이 필요했지만 모델이 못 받은 행), `publisher_suspect`(발행처 의심 표시가 붙은 행)를 본다. 그림 행과 의심 행은 상태를 바꾸지 않고 신뢰도만 `medium`으로 낮추므로, 이 기능이 들어간 뒤(2026-10-10) 분류된 행은 위 기준보다 `medium` 비율이 높은 것이 정상이다. `review_reasons`에는 이 셋이 들어가지 않는다. `page_image_unsupported`가 0보다 크면 분류 모델이 그림을 받지 못하는 모델이다 — 아래 모델 표를 본다. 그림·의심 행을 골라 보려면 `tagging_notes`에 `page_image`·`publisher_suspect`가 든 행을 찾는다(`tag inspect`는 메모를 세지 않는다).

**다시 분류하는 동안.** `tag requeue --apply` 뒤 백필이 도는 동안에는 대기·검토 숫자가 크게 바뀌는 것이 정상이다. 이때 비율 감시는 다시 분류된 행끼리만 한다.

**배치 보고에서 볼 것.** 키가 틀리거나 만료돼 모든 LLM 호출이 거절돼도 `tag run`은 0으로 끝나고 행은 모두 `pending`으로 돌아간다. 반복 실행 스크립트는 멈추지 않고 다음 배치를 계속 돈다. 배치 JSON의 `auto`·`review_needed`가 0이고 `unhandled_errors`나 `transient_errors`가 배치 크기만큼이면 스크립트를 Ctrl+C로 멈추고 키·로그인·공급자 상태를 확인한다. 같은 행이 매번 오류를 내면 되돌려진 뒤 다음 배치에서 다시 먼저 잡혀 배치마다 한 자리를 차지한다(자동으로 검토로 넘기는 장치는 없다).

## AI 모델 구성

| 용도 | 설정 변수 | 코드 기본값 | 공급자 |
|---|---|---|---|
| 태깅 | `LLM_MODEL_DEFAULT` | `claude-haiku-5-5` | Anthropic (`ANTHROPIC_API_KEY`) |
| 태깅 재처리(`tag escalate`) | `LLM_MODEL_ESCALATION` | `gpt-5.4` | OpenAI (`OPENAI_API_KEY`) |
| 재무 분석·리포트 비교(웹앱) | `LLM_MODEL_PHASE2` | `gpt-6-luna` (운영 `.env`는 `codex:gpt-6-luna`) | `codex:` 접두사 → 로컬 `codex exec` |
| 유사 기업 프로필 추출(`peers build`) | `LLM_MODEL_PEERS` | `claude-sonnet-5-5` (2026-10-10 시험 실행 뒤 사용자가 정함) | 모델 이름이 정함 |
| 프로필 재추출(근거율 0.8 미만일 때 한 번) | `LLM_MODEL_PEERS_ESCALATION` | `gpt-5.4` | 모델 이름이 정함 |
| 임베딩(`peers build`, 웹 테마 검색) | `PEERS_EMBED_MODEL` | `text-embedding-3-large` (1536차원으로 받음) | OpenAI (`OPENAI_API_KEY`) |

- **모델 이름이 공급자를 정한다:** `claude-*` → Anthropic API, `codex:<모델>` → 로컬 Codex CLI(ChatGPT 로그인 한도, API 키 불필요), 그 밖 → OpenAI API. 바꾸거나 되돌릴 때는 `.env`의 모델 이름만 고치고 워커·웹앱을 다시 켠다. 옛 이름 `OPENAI_MODEL_*`도 `LLM_MODEL_*`이 없을 때 읽는다.
- 분류 명령은 시작할 때 **실제로 쓸 모델**의 키만 확인한다(`tag run`은 태깅 모델, `tag escalate`는 재처리 모델, `--model`을 주면 그 모델). `tag inspect`·`tag reset-worker`·`tag requeue`는 키가 필요 없다.
- **분류 모델은 그림을 받는 모델이어야 한다.** 글자가 없는 PDF는 1쪽 그림을 AI에게 보여 준다. Claude 모델과 `gpt-5.4` 같은 지금의 OpenAI 모델은 그림을 받는다(codex 경로는 그림 파일을 `-i`로 붙인다). 그림을 받지 못하는 모델(`gpt-3.5…`, `o1-mini` 같은 글자 전용 이름)로 바꾸면 그런 PDF는 AI를 부르지 않고 검토 대기(`first_page_unreadable`)로 가고 배치 보고의 `page_image_unsupported`가 늘며, `tag requeue --unreadable`은 4로 멈춘다.
- 웹앱은 분석·비교 해석문을 실제로 부르기 직전에만 키·codex를 확인한다. 없으면 `분석 기능을 지금 쓸 수 없습니다: …`로 막히고, 목록·재사용·검토는 그대로 동작한다.
- 재무 분석이 `codex:gpt-6-luna`인 이유: 사용자가 고른 리포트만 분석하므로 호출량이 적어 로그인 한도로 충분하다. Haiku는 재무 지표를 절반만 뽑고 형식 실패가 12.5%라서 뺐다(2026-10-08, 12건 비교).
- 재무 분석 응답(`ExtractionResult`)은 스키마가 커서, Claude 모델에서는 형식 강제(grammar) 대신 "스키마를 프롬프트에 넣고 받은 JSON 글자를 pydantic으로 검증"하는 경로(`constrained=False`)를 쓴다. 태깅과 비교는 형식 강제를 쓴다.
- 사고 깊이: Claude는 `ANTHROPIC_EFFORT`(기본 `medium`), Codex는 `CODEX_REASONING_EFFORT`(기본 `high`. `medium`은 빠르지만 재무 지표가 약 40% 적게 나온다). `codex` 실행 파일 위치는 `CODEX_BIN`(없으면 PATH).
- Codex 로그인이 만료되면 `codex login`으로 다시 로그인한다. 사용 한도를 넘으면 한도가 풀릴 때까지 기다리거나, `.env`의 `LLM_MODEL_PHASE2`를 API 모델(`gpt-6-luna`)로 바꾸고 웹앱을 다시 켠다.
- 모델을 바꿔도 저장된 분석은 원래 모델 정보를 유지한 채 재사용된다. 모델 변경이 일괄 재분석을 일으키지 않는다.
- 유사 기업: `peers build`는 시작할 때 프로필 모델과 재처리 모델 둘 다의 키(codex 모델이면 CLI)와 `OPENAI_API_KEY`를 확인한다. 프로필 모델을 바꿔도 이미 `ok`인 프로필은 재사용되므로(같은 보고서·파서 버전), 새 모델로 다시 만들려면 프로필 버전을 올린다. 임베딩 모델은 1536차원을 낼 수 있는 OpenAI 모델(`text-embedding-3-small`·`-large`)만 쓴다. 바꾸면 다음 빌드가 임베딩만 새로 만들고(AI 추출 없음), 웹의 테마 검색은 공개 빌드에 기록된 모델을 쓴다.
- 프로필 추출은 Claude 모델에서도 응답 모양을 형식 강제(grammar)로 받는다(`constrained=True`). 재무 분석처럼 스키마가 커서 거절될 수 있는지는 첫 시험 실행에서 확인한다.

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
| `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` | collect·prices update·peers build·peers inspect(필수, 없으면 `prices`·`peers`는 4), 웹의 DB 기능 | — | 없으면 웹의 리포트·커버리지·검토·비교·유사 기업·상태 줄이 사용 불가 |
| `SUPABASE_DB_URL` | 모든 `tag` 명령 | 필수 | Postgres 직접 연결 주소. 없으면 `SUPABASE_DB_URL is required`, 4 |
| `STORAGE_BASE_DIR` | collect, tag, web | `./reports` | PDF 저장 폴더. 세 단계가 같은 값을 써야 한다 |
| `KRX_CSV_PATH` | tag run/escalate, web, stocks set-version, prices update, peers build | `docs/stock_data/KRX_stocks_data.csv` | 종목표. 버전 정보 파일은 같은 폴더의 `<이름>.version.json`. 버전이 안 맞으면 `tag run/escalate`·`peers build`는 4, `prices update`와 웹은 그대로 동작 |
| `LLM_MODEL_DEFAULT` / `LLM_MODEL_ESCALATION` / `LLM_MODEL_PHASE2` | tag run / tag escalate / 웹 분석·비교 | 위 모델 표 | 옛 이름 `OPENAI_MODEL_*`도 읽음 |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | 모델 이름이 정한 공급자 | 쓰는 공급자 것만 | 유사 기업 기능(`peers build`의 임베딩, 웹 테마 검색)은 모델과 상관없이 `OPENAI_API_KEY`가 필요하다 |
| `ANTHROPIC_EFFORT` / `CODEX_REASONING_EFFORT` / `CODEX_BIN` | AI 호출 | medium / high / PATH의 codex | |
| `MAX_CONCURRENT_LLM` | tag | **2** | 위 운영 원칙 1 |
| `TAGGER_BATCH_SIZE_DEFAULT` | tag | **10** | 위 운영 원칙 2 |
| `LOCK_TTL_MINUTES` / `PER_ROW_DEADLINE_S` | tag | 30 / 90 | 오래된 잠금 기준(분) / 행 하나의 시간 한도(초). `LOCK_TTL_MINUTES × 60`이 배치 하나의 최장 시간(⌈배치 크기 ÷ 동시 처리 수⌉ × 행 한도, 기본 7.5분)보다 길어야 한다 |
| `PHASE2_PER_REPORT_TIMEOUT_S` | 웹 분석·비교 | 180 | 호출 한 번의 시간 한도. 재무 추출은 지표를 최대 48개 뽑아 gpt-6-luna 기준 최대 ≈105초 걸린다 |
| `PHASE2_MAX_INPUT_TOKENS` | 웹 분석 | 30000 | 넣을 PDF 글자의 토큰 어림 한도(글자 수 ÷ 3) |
| `PHASE2_SUMMARY_VERSION` | 웹 분석 | `llm-summary@1.0` | 화면에 보일 분석 결과 버전. 바꾸면 기존 분석이 화면에서 사라진다 |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, `LANGSMITH_ENDPOINT` | AI 호출 기록 | 선택 | `LANGSMITH_TRACING=true`이고 키가 있으면 LangGraph 실행과 LLM 호출을 LangSmith에 기록 |
| `KIS_APP_KEY`, `KIS_APP_SECRET` | prices update | 필수(없으면 4) | 한국투자증권 Open API 실전 계정의 앱키·시크릿. 사용자의 masterdb 프로젝트와 같은 키다(위 운영 원칙 7) |
| `KIS_BASE_URL` | prices update | `https://openapi.koreainvestment.com:9443` | KIS 실전 서비스 주소 |
| `PRICES_MAX_CALLS_PER_SEC` | prices update | 10 | KIS 호출을 1초에 몇 번까지 시작할지(토큰 요청 포함). 0보다 큰 수가 아니면 파이썬 오류로 1 |
| `LLM_MODEL_PEERS` / `LLM_MODEL_PEERS_ESCALATION` | peers build | `claude-sonnet-5-5` / `gpt-5.4` | 프로필 추출 모델 / 근거율 0.8 미만일 때 한 번 더 추출할 모델(위 모델 표). 옛 이름 `OPENAI_MODEL_PEERS*`도 읽음 |
| `PEERS_EMBED_MODEL` | peers build | `text-embedding-3-large` | 임베딩 모델. 1536차원을 낼 수 있는 OpenAI 모델만. 웹 테마 검색은 이 값이 아니라 공개 빌드에 기록된 모델을 쓴다 |
| `PEERS_PROFILE_VERSION` | peers build | `peer-profile@1.2` | 프로필의 키. 바꾸면 다음 빌드가 모든 회사를 새로 추출한다(AI 비용). 화면은 공개 빌드의 버전을 따른다 |
| `PEERS_FISCAL_YEAR` | peers build | 2025 | `--fiscal-year`가 없을 때의 회계연도 |
| `PEERS_MAX_CONCURRENT_LLM` | peers build | **2** | 빌드 중 AI 동시 호출. 위 운영 원칙 6 |
| `PEERS_PER_COMPANY_TIMEOUT_S` | peers build | 120 | 회사 하나의 추출(재추출 포함) 시간 한도(초). 넘으면 그 회사는 실패 |
| `DART_MONGO_URL` / `DART_MONGO_DB` / `DART_MONGO_COLLECTION` | peers build | `mongodb://localhost:27017/` / `FS` / `A001_v2` | 사업보고서 텍스트 MongoDB(읽기만). 별도 저장소의 DART 수집 프로그램이 채운다. 주소에 비밀번호가 들어갈 수 있다 |

숫자 설정(`PRICES_*`, `PEERS_*`)이 숫자가 아니면 그 명령은 준비 문제(4)가 아니라 파이썬 오류(1)로 끝난다.

`.env`에 남아 있는 `HEARTBEAT_ENABLED`·`HEARTBEAT_INTERVAL_S`·`PHASE2_MAX_CONCURRENT`는 아무도 읽지 않는다. 지워도 된다.

## 종목표 갱신

1. `docs/stock_data/KRX_stocks_data.csv`를 새 파일로 바꾼다.
2. `python -m research_desk stocks set-version --as-of <자료 기준일, YYYY-MM-DD>` — 새 버전 이름(`KRX@…`)이 찍히고 0이면 성공. 날짜·머리줄이 틀리면 이유를 찍고 4, 버전 정보 파일은 그대로다.
3. 이 명령을 실행하기 전까지 `tag run`·`tag escalate`는 4로 멈추고, 웹앱은 경고 로그만 남긴다.
4. CSV와 버전 정보 파일을 한 커밋에 올린다.
5. 옛 종목표에 없어 검토 대기(`krx_unmatched_in_scope`)로 쌓인 행은 저절로 풀리지 않는다. 아래 "다시 분류 대기" 절차로 `tag requeue --krx-unmatched`를 미리 보기 → `--apply` → 백필한다.

## 다시 분류 대기 (`tag requeue`)

이미 분류된 행을 지금 규칙으로 다시 분류하고 싶을 때(발행처 사전을 고친 뒤, 분류 규칙이 바뀐 뒤, 종목표를 바꾼 뒤) 쓴다. 행을 "분류 전"(`pending`) 줄로 되돌릴 뿐이고, 실제 다시 분류는 평소 백필이 한다. 사람이 승인한(`verified`) 행은 절대 되돌리지 않는다.

### 발행처 사전을 고친 뒤 (순서대로)
1. **백필이 도는 동안에는 사전(`research_desk/tagger/vocabulary/publishers.yaml`)을 고치지 않는다.** 사전 원문이 AI 요청에 그대로 들어가서, 고치는 순간 같은 백필 안에서 다른 요청이 섞인다. 사전을 고쳐 커밋한 뒤 다음 단계로 간다.
2. **백필·재처리·수집·웹앱을 끈다.** `--apply`가 이 PC의 프로세스 목록을 보고 하나라도 돌고 있으면 아무것도 바꾸지 않고 1로 멈추며 끌 것과 PID를 알려 준다(목록 자체를 못 읽으면 4). 사각지대: 관리자 권한으로 띄운 프로세스는 명령줄이 보이지 않아 찾지 못하고, 이미 열린 PowerShell 창에서 `& .\scripts\run-batches.ps1`로 띄운 백필은 배치 사이(자식 `tag run`이 없는 몇 초)에 보이지 않는다 — 백필은 `powershell -File scripts\run-batches.ps1 …`로, 웹앱은 일반 터미널에서 띄우고, 관리자 창이나 열린 창에서 띄운 것이 있으면 직접 끈다.
3. **미리 보기.** 고친 내용에 맞는 조건을 고른다: 이름을 바꾸거나 뺐으면 `--publisher-not-in-dictionary`, 구역(종류)을 옮겼으면 `--publisher-type-mismatch`, 별칭·파일 이름 표기를 더했으면 `--publisher-filename-mismatch`. 그림 경로가 생기기 전에 못 읽음으로 간 행은 `--unreadable`.
   ```powershell
   python -m research_desk tag requeue --publisher-not-in-dictionary --publisher-filename-mismatch --publisher-type-mismatch
   ```
   JSON의 `selected`(조건별 건수), `total`(되돌릴 행 수), `publisher_values`(그 행들의 지금 발행처 값), `unknown_filename_tags`(사전에 없는 파일 이름 표기 — 사전 보충 후보)를 본다. `--unreadable`을 골랐다면 `skipped.unreadable_no_page`가 지나치게 크지 않은지 본다(크면 `STORAGE_BASE_DIR`가 틀렸을 수 있다).
4. **적용.** 같은 조건에 `--apply`를 붙인다. 대상을 다시 고르고, 바뀔 행을 `backups\requeue\requeue-YYYYMMDD-HHMMSS.csv`에 먼저 쓴 뒤, 한 트랜잭션에서 되돌리고 건수를 확인한다. 보고의 `requeued`(되돌린 수)와 `backup_file`(백업 위치)을 기록해 둔다. `requeued`가 `total`보다 작으면 그사이 바뀐 행이 빠진 것이다.
5. **백필.** `powershell -File scripts\run-batches.ps1 -Iterations <남은 대기 건수 ÷ 10 올림> -BatchSize 10`. 되돌린 행은 먼저 수집된 순서로 잡혀 새 리포트보다 먼저 처리된다. 다시 분류될 때까지 그 리포트는 웹 목록에서 빠진다.
6. **(필요하면) 저장된 비교 비우기.** 발행처가 바뀐 리포트의 저장된 비교 해석문은 옛 발행처 판정(같은 증권사/다른 증권사)으로 쓰인 글이고, 저절로 지워지지 않는다(`tag requeue`는 분석 결과 표를 건드리지 않는다). 비우려면 백업 CSV와 지금 값을 비교해 발행처가 바뀐 리포트를 고르고, 그 리포트가 `report_id` 또는 `prev_report_id`인 `report_summaries` 행의 비교 칸 네 개(`prev_report_id`, `prev_match_type`, `diff_narrative`, `comparison_details`)만 운영 데이터 고치기 순서대로(백업 → 한 트랜잭션 → 바뀐 행 수 확인 → 커밋) NULL로 비운다. 분석 결과의 다른 칸은 건드리지 않는다. 비교 버튼을 다시 누르면 새로 만들어진다.

### 알아 둘 것
- 백업 폴더 `backups/requeue/`는 저장소 안에 있지만 git이 무시한다(`.gitignore`의 `/backups/`). 커밋하지 않는다. 백업은 기록용이고, 백업에서 되돌리는 명령은 없다. 대상이 0건이면 백업 파일을 만들지 않는다.
- 한 번 돌린 뒤 같은 조건으로 또 돌려도 같은 행이 계속 되돌려지지 않는다: 그림을 만들 수 없는 행은 `--unreadable`이 고르지 않고, 의심 표시가 이미 붙은 행과 못 읽음·거부 행은 `--publisher-filename-mismatch`가 고르지 않는다.
- 사람이 승인한 행은 되돌리지 않으므로 사전에서 이름이 바뀌거나 빠진 옛 발행처(예: `DB금융투자`, `KIRS`, `미래대우증권`)를 그대로 가진다.
- 실패할 때: 4(준비 문제)는 아무것도 바꾸지 않았으니 안내대로 고친 뒤 다시 실행한다. 1은 stderr 한 줄로 이유를 알린다 — 실행 중인 작업 때문에 거절했을 때, 백업 실패, 건수 불일치는 모두 아무것도 바꾸지 않은 상태다.

## DB 마이그레이션 적용

- `migrations/NNN_*.sql`을 Supabase SQL 편집기나 직접 연결로 **한 번** 실행한다. 자동 적용 장치는 없다.
- 데이터를 바꾸는 파일(예: `007_normalize_taxonomy_version.sql`)은 `BEGIN`/`COMMIT` 없이 쓰여 있다. 적용할 때 반복 분류·수집·웹앱을 멈추고, 바뀔 행을 백업한 뒤, 트랜잭션 안에서 실행하고 바뀐 행 수를 확인한 다음 커밋한다.
- 001~006은 표 구조를 만들고 바꾸는 파일이다(003은 v1 분류 결과도 모두 지운다). 파일 안에 트랜잭션이 들어 있는 것도 있으니 그대로 실행하고, 이미 적용한 DB에 다시 돌리지 않는다.
- 008(`008_peers_prices_freshness.sql`)은 주가 표 두 개, 유사 기업 표 다섯 개, 벡터 탐색 함수 두 개를 만든다. 데이터는 바꾸지 않고 다시 실행해도 깨지지 않는다(멈출 작업도, 백업할 행도 없다).
  1. 먼저 SQL 편집기에서 pgvector 확장의 위치를 본다: `select extnamespace::regnamespace from pg_extension where extname = 'vector';`. 결과가 없거나 `extensions`면 적용한다. `public`이면 적용하지 않고 사용자와 정한다 — 008의 표와 함수가 `extensions.vector` 타입을 써서 실패한다.
  2. 파일 전체를 한 번 실행한다(맨 앞의 확장 만들기 뒤는 한 트랜잭션이고, 끝에서 PostgREST에 표를 다시 읽게 한다).
  3. 확인: `python -m research_desk peers inspect`가 빈 목록(`"profiles": []`, `"builds": []`)을 찍고 0으로 끝나면 표가 REST로 보인다.

## 테스트

```powershell
.venv\Scripts\python.exe -m pytest
```
`research_desk/` 아래 테스트만 모은다(구조 검사 포함). DB·AI·텔레그램·MongoDB·KIS에 접속하지 않고 `.env`를 읽지 않는다. 커밋 검사가 같은 것을 돌린다. 파이프로 결과를 받아 읽을 때는 `$env:PYTHONIOENCODING='utf-8'`을 먼저 둔다(한국어 메시지가 `\uXXXX`로 깨지지 않게).
