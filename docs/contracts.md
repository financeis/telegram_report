# 바깥과의 약속

이 시스템의 소비자는 둘이다: 화면(`frontend/`, 브라우저)이 쓰는 **웹 API**, 그리고 운영자와 `scripts/*.ps1`이 쓰는 **명령**. 아래 주소·요청 값·응답 키·상태 코드·문구는 화면과 스크립트가 그대로 의존한다. 바꾸려면 소비자 쪽을 같이 바꾼다.

## 웹 API — 공통 약속

- **접속.** `http://127.0.0.1:8520` (로그인 없음). `Host`는 `127.0.0.1`·`localhost`·`testserver`만 받는다 — 그 밖은 400.
- **쓰기 요청의 출처.** GET·HEAD·OPTIONS가 아닌 요청에 `Origin`이 있고, 그 호스트가 `127.0.0.1:8520`·`localhost:8520`·`127.0.0.1:5173`·`localhost:5173`이 아니면 403 `{"detail": "허용되지 않은 요청입니다."}`. `Origin`이 없으면 통과한다.
- **오류 모양.** 허용 호스트 거절(400)만 본문이 일반 글자 `Invalid host header`이고, 나머지 오류 응답 본문은 언제나 `{"detail": ...}`이다.
  - 업무 오류(404·409·422)는 `detail`이 아래 각 주소에 적은 고정 한국어 문장이다.
  - 요청 값 형식·범위 오류(정수가 아닌 id, 범위 밖 `days`, 정해진 값이 아닌 `unit`·`action` 등)는 422이고 `detail`은 FastAPI 기본 형식(오류 항목 목록)이다.
  - **기능 사용 불가:** 한 기능이 준비에 실패하면 그 기능의 주소만 503 `{"detail": "<기능 이름> 기능을 지금 쓸 수 없습니다: <이유>"}`. 기능 이름은 `기업 목록`, `리포트`, `분석`, `비교`, `커버리지`, `검토` 중 하나다. 다른 기능의 공개 창구를 거쳐 전해진 실패는 실패한 기능의 이름으로 보인다(예: 커버리지 화면에서 `리포트 기능을 지금 쓸 수 없습니다: …`). 이유에는 경로·키 값·오류 추적이 없다. 원인을 고치면 다음 요청에서 회복한다.
  - **예기치 못한 오류:** 503 `{"detail": "데이터를 불러오거나 분석하지 못했습니다. 연결 상태를 확인하고 다시 시도해 주세요."}`.
- **리포트 공개 모양**(여러 응답이 쓰는 리포트 객체): 키는 `id, title, file_name, published_at, publisher, report_type, stock_codes, company_names, sectors_major, sectors_minor, products, summary, pdf_url`. `pdf_url`은 `/api/reports/{id}/pdf`. `summary`는 그 리포트의 현재 버전(`llm-summary@1.0`) 분석 결과 객체이거나 `null`. `file_path`·저장 경로·키는 절대 들어가지 않는다.
- **종목 코드.** 주소의 `{code}`는 종목표 조회(기업 정보, 관심 기업 존재 확인)에서만 6자리가 되도록 앞을 0으로 채운다. DB 조회에는 받은 그대로 쓴다.
- **문서 화면.** `/docs`·`/redoc`은 꺼져 있다(404). `GET /openapi.json`은 자동 생성 API 설명을 준다.

## 웹 API — 주소별

### `GET /api/health`
→ 200 `{"status": "ok"}`. 어떤 기능이 고장 나도 항상 200.

### `GET /api/workspace` (기업 목록)
→ `{"stocks": [{"code", "name", "sector_major", "sector_minor"}…], "favorites": ["코드"…]}`. `stocks`는 종목표 전체를 파일 순서대로, 빈 업종은 `""`. 오류: 종목표를 못 읽으면 503 기업 목록 사용 불가.

### `PUT /api/favorites/{code}` 본문 `{"enabled": true|false}` (기업 목록)
→ `{"favorites": [...]}`(바뀐 뒤 전체 목록). 켜기는 중복 없이 끝에 더하고, 끄기는 없으면 그대로. 오류: 종목표에 없는 코드 404 `종목을 찾을 수 없습니다.`(끌 때도 같다), `enabled`가 없거나 참/거짓이 아니면 422.

### `GET /api/stocks/{code}/reports` (리포트)
→ `{"stock": {"code", "name", "sector_major", "sector_minor"}, "reports": [리포트 공개 모양…]}`. 그 코드를 `stock_codes`에 담은 분석 대상 행 중 `published_at ≥ 2000-01-01`, `published_at` 내림차순 → `id` 내림차순. 오류: 종목표에 없는 종목 404 `종목을 찾을 수 없습니다.`

### `GET /api/reports/{rid}/pdf` (리포트)
→ `application/pdf`. 오류: 분석 대상 행이 아님 404 `기업 보고서를 찾을 수 없습니다.` / 저장 폴더 밖이거나 PDF가 아님 404 `PDF를 찾을 수 없습니다.` / 파일 없음 404 `로컬 PDF 파일이 없습니다. 수집 상태를 확인해 주세요.`

### `POST /api/reports/{rid}/analyze` (리포트 → 분석)
→ 리포트 공개 모양 + `"analysis_reused": true|false`(재무 상세가 있는 저장 결과를 그대로 돌려줬으면 true). 오류: 404 `기업 보고서를 찾을 수 없습니다.` / 409 `이 보고서는 분석 중입니다. 잠시 후 새로고침해 주세요.` / 422 `금융 정보 분석은 단일종목 보고서를 선택해 주세요.` / PDF 404 두 문구(위와 같음) / 422 `PDF에서 읽을 수 있는 텍스트가 없습니다.` / 모델 키·codex CLI가 없으면 503 `분석 기능을 지금 쓸 수 없습니다: <키 이름>가 설정되지 않았습니다. .env에 추가 후 분석 다시 시도하세요.` 또는 `…: codex CLI를 찾을 수 없습니다. 설치 후 \`codex login\`으로 로그인하세요.` 호출 한 번은 최대 `PHASE2_PER_REPORT_TIMEOUT_S`(180초)에 일시 오류 재시도 1회가 더해질 수 있다.

### `GET /api/compare?left=<id>&right=<id>` (비교)
→ `{"left": 리포트, "right": 리포트, "same_publisher": bool, "metrics": [...], "narrative": 문자열|null, "target_price_change": {...}|null}`. AI를 부르지 않는다.
- `left`는 앞 문서(`published_at`, 같으면 `id`가 작은 쪽), `right`는 뒷 문서다. 요청의 순서와 상관없다.
- `metrics` 항목: `metric, fiscal_period, unit, currency, accounting_basis, value_type, scenario, previous, current, delta, change_pct, change_label, previous_evidence, current_evidence`. `change_pct`는 %·0·음수가 끼면 `null`이고, 그때 `change_label`이 `+1.50%p`·`흑자 전환`·`적자 전환`·`음수→양수`·`양수→음수`·`… (증감률 미표시)` 중 하나다.
- `target_price_change`: 두 문서의 새 목표주가가 다 있을 때만 `{"delta", "change_pct", "change_label"}`, 아니면 `null`.
- `narrative`: 뒷 문서에 이 앞 문서와의 해석문이 저장돼 있을 때만 그 글, 아니면 `null`.
- 오류: 어느 쪽이든 분석 대상 리포트가 아니면 404 `기업 보고서를 찾을 수 없습니다.` / 422 `금융 비교는 단일종목 보고서 두 개를 선택해 주세요.` / `서로 다른 보고서 두 개를 선택해 주세요.` / `같은 기업의 보고서를 선택해 주세요.`

### `POST /api/compare/analyze` 본문 `{"left": id, "right": id}` (비교)
→ 위와 같은 모양에 `narrative`가 채워진다. 같은 짝의 해석문이 이미 저장돼 있으면 AI를 부르지 않고 그것을 돌려준다. 오류: 위 404·422 + 둘 중 분석 결과가 없으면 422 `선택한 두 보고서를 먼저 분석해 주세요.` + 모델 키·codex가 없으면 503 분석 사용 불가.

### `GET /api/market` (커버리지)
인자: `days`(정수 1~36500, 기본 36500), `unit`(`D`|`W`|`M`, 기본 `W`), `level`(`sectors_major`|`sectors_minor`|`products`, 기본 `sectors_major`), `items`(여러 번 줄 수 있음, 기본 없음 = 전체), `include_oos`(참/거짓, 기본 false). 잘못된 값은 422.
→ `{"total", "inscope", "oos", "publishers", "latest", "earliest", "available_items", "coverage", "ranking", "types"}`
- `total`/`inscope`/`oos`: 기간 안 행 수(`include_oos`가 false면 `oos`는 0).
- `publishers`: 서로 다른 발행처 수. `latest`/`earliest`: 집계 날짜의 끝과 처음(`YYYY-MM-DD`, 없으면 `null`).
- `available_items`: 그 수준(`level`)에서 고를 수 있는 값 목록(가나다순).
- `coverage`: `[{"bucket": ISO 시각, "sector": 값, "count"}]`(분석 대상만). `ranking`: `[{"code", "count", "name"}]`(종목표에 없는 코드는 이름 `""`). `types`: `[{"bucket", "report_type", "count"}]`(`include_oos`면 분석 대상 외 포함).

### `GET /api/stocks/{code}/activity` (커버리지)
인자: `days`(1~36500, 기본 36500), `unit`(`D`|`W`|`M`, 기본 `W`). 잘못된 값은 422.
→ `{"timeline": [{"bucket", "count"}], "publishers": [{"publisher", "count"}], "total"}`. 그 코드를 `stock_codes`에 담은 분석 대상 행 전부를 리포트 종류와 상관없이 센다. `publishers`는 상위 5곳 + 나머지를 `기타` 한 줄로.

### `GET /api/review?skipped=<id>&skipped=<id>…` (검토)
→ `{"remaining": 남은 대기 수, "report": 다음 행 또는 null}`. `report`는 `review_needed` 행 중 `skipped`의 id를 뺀 것에서 `tagged_at`이 가장 오래된 행이다. `remaining`은 `skipped`와 상관없이 `review_needed` 행 전체의 정확한 개수다. `report`는 그 행의 모든 열에서 `file_path`만 빼고 `"pdf_url": "/api/review/{id}/pdf"`를 붙인 것이다(`file_hash_sha256` 등 나머지 열은 그대로 나간다).

### `GET /api/review/{rid}/pdf` (검토)
→ `application/pdf`. 오류: 행 없음 404 `검토할 보고서가 없습니다.` / PDF 경로 오류는 리포트 PDF와 같은 두 문구.

### `GET /api/review/{rid}/preview` (검토)
→ `{"page_count": 쪽 수, "preview_pages": min(3, page_count)}`. 오류는 위 PDF와 같다.

### `GET /api/review/{rid}/pages/{page}` (검토)
→ 그 쪽의 120dpi PNG, `Cache-Control: private, max-age=300`. 오류: `page`가 1~3 밖 404 `미리보기는 첫 3페이지까지 제공됩니다.` / 없는 쪽 404 `페이지가 없습니다.` / 행·PDF 오류는 위와 같다.

### `POST /api/review/{rid}/action` 본문 `{"action": "verify"|"oos"|"retag", "reason": 사유|null}` (검토)
→ `{"undo_token": 문자열, "report_id": id}`. 오류: 행 없음 404 `검토할 보고서가 없습니다.` / `oos`인데 사유 없음 422 `분석 대상 제외 사유를 선택해 주세요.` / `action`·`reason`이 정해진 값(`foreign`·`fund`·`digital`·`private`·`ir_self`)이 아님 422 / 대기 상태가 아님 409 `다른 작업에서 처리한 보고서입니다. 목록을 새로고침해 주세요.` / 읽은 뒤 상태가 바뀜 409 `보고서 상태가 바뀌었습니다. 새로고침해 주세요.`

### `POST /api/review/undo/{token}` (검토)
→ `{"report_id": id}`. 오류: 409 `되돌릴 작업이 없거나 서버가 재시작되었습니다.` / 409 `후속 작업이 처리한 보고서라 되돌릴 수 없습니다.` / 409 `후속 작업이 시작되어 되돌릴 수 없습니다.` / 행이 사라짐 404 `검토할 보고서가 없습니다.`

### `GET /`, `GET /assets/*` (화면 파일)
`frontend/dist`의 빌드 결과. `/`는 `index.html`을 `Cache-Control: no-cache`로 준다. 빌드가 없으면 `/`는 503 `프론트엔드를 먼저 빌드해 주세요: cd frontend && npm run build`. `/assets`는 서버를 켤 때 `frontend/dist`가 있었을 때만 연결된다.

## 명령 — `python -m research_desk <명령>`

공통: 저장소 루트에서 실행. 인자가 없거나 틀리면 사용법 + 종료 코드 2, `--help`는 0.

| 명령 | 인자 | 출력 | 종료 코드 |
|---|---|---|---|
| `collect` | `--cutoff-days N` 또는 `--backfill-days N`(같이 못 씀), `--dry-run`, `-v/--verbose` | 로그. 설정 누락 시 stderr `Config error: Missing required env var: <이름>` | 0 성공 / 1 전체 실패 / 2 일부 실패 |
| `tag run` | `--batch-size N`, `--model M`, `--dry-run`, `--row-ids 1,2,3`, `--max-concurrent-llm N`, `--worker-id W` | stdout에 배치 JSON 보고(아래 "분류 배치 보고", `worker_id` 포함) | 0 / 4 준비 문제 / 1 |
| `tag inspect` | 없음 | `{"pending", "processing", "auto", "review_needed", "verified", "oos_total", "last_24h"}` | 0 / 4 / 1 |
| `tag escalate` | `--since ISO시각`(필수), `--model M`, `--max-concurrent-llm N` | 대상 없음 `{"escalated": 0, "since": "<받은 값>"}`, 있으면 run과 같은 보고(`worker_id` 키 없음) | 0 / 4 / 1 |
| `tag reset-worker` | `--worker-id W`(필수) | `{"worker_id", "reset_count", "ids"}` | 0 / 4 / 1 |
| `tag requeue` | 대상 조건 `--unreadable`, `--publisher-not-in-dictionary`, `--publisher-filename-mismatch`, `--publisher-type-mismatch`, `--krx-unmatched` 중 하나 이상(없으면 2), `--apply`(없으면 미리 보기) | stdout에 JSON 하나(아래 "`tag requeue` 보고") | 0 정상(대상 0건 포함) / 4 준비 문제(아무것도 바꾸지 않음) / 1 그 밖의 오류(stdout 비움, stderr 한 줄) / 2 인자 오류 |
| `web` | `--view reports\|market\|review`(기본 reports) | 첫 줄 `Research Desk: http://127.0.0.1:8520/?view=<view>`, 그 뒤 서버 실행 | 0 (Ctrl+C까지 돈다) |
| `stocks set-version` | `--as-of YYYY-MM-DD`(필수) | 성공 시 stdout에 새 버전 `KRX@YYYY-MM-DD` | 0 / 4(날짜 형식·종목표 형식 오류, 버전 정보 파일은 그대로) |

### 분류 배치 보고 (`tag run`, `tag escalate`)

- 키(이 순서): `model, processed, auto, review_needed, confidence{high, medium, low}, oos{foreign, fund, digital, private, ir_self}, review_reasons, transient_errors, deadline_errors, unhandled_errors, page_image, page_image_unsupported, publisher_suspect, dry_run, batch_size`, `--dry-run`이면 `rows`, `tag run`은 마지막에 `worker_id`.
  - `review_reasons`: 검토 사유 4가지(`first_page_unreadable`, `llm_refusal`, `type_indeterminate`, `krx_unmatched_in_scope`)별 건수. 덧붙는 메모는 세지 않는다.
  - `page_image`: 1쪽 그림으로 AI에게 물은 행 수. `page_image_unsupported`: 그림이 필요했지만 분류 모델이 그림을 받지 못한 행 수. `publisher_suspect`: 발행처 의심 표시가 붙은 행 수. 모두 정수다.
  - `rows`(`--dry-run`만): 처리한 행마다 `{"id", "tagging_status", "tagging_confidence", "report_type", "publisher", "publisher_type", "tagging_notes"}`(썼다면 들어갔을 값), 오류 난 행은 `{"id", "error", "detail"}`(`error`는 `transient`·`deadline_exceeded`·`unhandled`). `--dry-run`이 아니면 `rows`가 없다.
- 가져간 행이 없으면 빈 보고 `{"model", "processed": 0, "auto": 0, "review_needed": 0, "confidence", "oos", "review_reasons": {}, "transient_errors": 0, "deadline_errors": 0, "unhandled_errors": 0}`(`tag run`은 `worker_id`도). `page_image`·`page_image_unsupported`·`publisher_suspect`·`dry_run`·`batch_size`·`rows`가 없다.
- `tag escalate`는 대상이 없으면 한 줄 `{"escalated": 0, "since": "<받은 값>"}`이고, 있으면 위 보고에서 `worker_id`만 없다.

### `tag requeue` 보고

- 미리 보기(기본, 아무것도 쓰지 않음): `{"mode": "preview", "selected": {…}, "skipped": {…}, "total": n, "publisher_values": {…}, "unknown_filename_tags": {…}}`.
- 적용(`--apply`): `{"mode": "apply", "selected": {…}, "skipped": {…}, "total": n, "requeued": n, "backup_file": "<백업 파일 절대 경로>"|null}`.
- `selected`: 고른 조건만, 조건 이름(`unreadable`, `publisher_not_in_dictionary`, `publisher_filename_mismatch`, `publisher_type_mismatch`, `krx_unmatched`, 이 순서)별 행 수. 한 행이 여러 조건에 맞으면 각각 센다.
- `skipped`: `--unreadable`을 골랐을 때만 `{"unreadable_no_page": n}`(메모는 맞지만 지금 1쪽 그림을 만들 수 없어 고르지 않은 행 수), 아니면 `{}`.
- `total`: 중복 없는 대상 행 수. `requeued`: 실제로 `pending`으로 되돌린 행 수(그사이 바뀐 행은 빠지므로 `total`보다 작을 수 있다).
- `publisher_values`: 대상 행의 지금 발행처 값별 건수(비어 있으면 `(null)`), 많은 순. `unknown_filename_tags`: 고른 조건과 상관없이 분류된 행(`auto`·`review_needed`·`verified`) 전체에서 사전에 없는 파일 이름 표기별 건수, 많은 순.
- 대상이 0건인 적용은 백업도 쓰기도 없이 `"requeued": 0, "backup_file": null`, 종료 코드 0이다.
- 백업 파일: `<저장소>/backups/requeue/requeue-YYYYMMDD-HHMMSS.csv`(같은 초면 `-1`, `-2` …). UTF-8(BOM 없음), 칸은 `id`, `file_name`, `tagging_status`, `tagged_at`과 되돌리며 비우는 나머지 칸 전부(머리줄에 칸 이름), 값은 Postgres 글자 그대로(배열은 `{…}`), NULL은 따옴표 없는 빈 칸. 기록용이다 — 백업에서 되돌리는 명령은 없다.
- stdout이 저장된 값의 글자를 못 보여 주는 코드 페이지면 같은 JSON을 `\uXXXX`로 이스케이프해 낸다.
- 1로 끝날 때 stderr 한 줄: `백업 파일을 쓰지 못해 아무것도 바꾸지 않았습니다: <이유>` / `되돌린 행 수(<n>)가 다시 확인한 행 수(<m>)와 달라 모두 취소했습니다. 아무것도 바뀌지 않았습니다.` / `tag requeue를 마치지 못했습니다: <예외 이름>: <내용>`.
- 조건이 없으면 사용법과 함께 `대상 조건을 하나 이상 고르세요: --unreadable, --publisher-not-in-dictionary, --publisher-filename-mismatch, --publisher-type-mismatch, --krx-unmatched`, 종료 코드 2.

### 준비 문제(4)의 stderr 문구

4는 아무것도 바꾸지 않았고, 안내대로 고친 뒤 다시 실행하면 되는 문제다. `tag run`·`tag escalate`는 행을 가져가기 전에 이 중 하나를 찍고 끝난다:
- 키 누락: `<변수> is required` (예: `ANTHROPIC_API_KEY is required`)
- `SUPABASE_DB_URL` 누락: `SUPABASE_DB_URL is required` (`tag inspect`·`tag reset-worker`도 같다)
- codex CLI 없음: `codex CLI not found for model <모델>`
- 종목표 파일 없음·못 읽음·머리줄 틀림: `종목표 파일을 읽을 수 없습니다: <이유>`
- 종목표 지문 불일치·버전 정보 없음: `종목표 파일 내용이 버전 정보와 다릅니다. 종목표를 바꿨다면 python -m research_desk stocks set-version --as-of <자료 기준일, YYYY-MM-DD>를 실행한 뒤 다시 시작하세요.`

`tag requeue`는 DB에 닿기 전에 이 순서로 확인하고, 하나라도 걸리면 그 한 줄을 찍고 4로 끝난다(어떤 행도 바꾸지 않았다):
- `SUPABASE_DB_URL is required`
- 발행처 사전을 못 읽음·사전 규칙 위반: `발행처 사전을 읽을 수 없습니다: <이유>`
- `--unreadable`인데 분류 모델(`LLM_MODEL_DEFAULT`)이 그림을 못 받음: `지금 분류 모델 <모델>은(는) 그림을 받지 못해 --unreadable 행을 되돌려도 다시 못 읽음이 됩니다. LLM_MODEL_DEFAULT를 그림을 받는 모델로 바꾼 뒤 다시 실행하세요.`
- `--apply`인데 이 PC에서 작업이 돌고 있음: `실행 중인 작업이 있어 아무것도 바꾸지 않았습니다. 다음을 끈 뒤 다시 실행하세요: <작업> PID <번호>` — 작업 이름은 `백필(run-batches.ps1 또는 research_desk tag run)`, `재처리(research_desk tag escalate)`, `수집(research_desk collect)`, `웹앱(research_desk web)`이고, 여럿이면 ` / `로 잇는다.
- `--apply`인데 프로세스 목록을 못 읽음: `프로세스 목록을 읽을 수 없어 실행 중인 작업을 확인하지 못했습니다. 아무것도 바꾸지 않았습니다: <이유>`

## 스크립트 — `scripts/*.ps1` (Windows PowerShell 5.1)

| 스크립트 | 인자 | 종료 코드 |
|---|---|---|
| `run-batches.ps1` | `-Iterations N`(기본 5), `-BatchSize N`(0이면 설정 기본값), `-MaxRetries N`(기본 2), `-RetrySleepS N`(기본 3), `-Python <경로>`; 환경 변수 `RESEARCH_DESK_PY` | 0 모두 완료 / 1 한 배치가 모든 시도에 실패 / 2 파이썬 못 찾음 / 3 `tag reset-worker` 실패 / 4 준비 문제(되돌리기·재시도 없이 즉시) |
| `start-workspace.ps1` | `-SkipBuild` | 빌드·실행 실패 시 예외로 끝남 |

`run-batches.ps1`은 `tag run`의 종료 코드만 본다: 0이면 다음 배치, 4면 즉시 멈춤, 그 밖이면 같은 작업자 ID로 `tag reset-worker` 후 새 작업자 ID로 재시도. 종료 코드 0·1·4로 끝날 때 출력의 마지막 줄은 `Summary: success=<n> failed_attempts=<n>`이다(2·3은 오류 문구로 끝난다).
