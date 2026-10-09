# research_desk/tagger/ — `pending` 리포트 행을 LLM으로 분류해 `auto`·`review_needed`로 마감하는 분류기(LangGraph)

## 맡는 일

- `tag` 명령(`cli.register(subparsers)`): `run`, `inspect`, `escalate`, `reset-worker`, `requeue`.
- 행 처리 그래프(`graph.build_graph`): 노드 8개(`extract_pdf`, `llm_extract`, `mark_oos_reason`, `status_oos`, `status_unreadable`, `resolve_krx`, `decide_status`, `write`)와 분기 함수 `oos_gate`. 글자가 없는 PDF를 1쪽 그림으로 AI에게 보여 주는 길도 노드를 더하지 않고 `extract_pdf`·`llm_extract` 안에 있다.
- `reports` 표의 분류 몫. SQL은 모두 `sql.py`에 있다: 오래된 잠금 되돌리기, 대기 행 가져가기(`FOR UPDATE SKIP LOCKED`), 미리 보기·지정 행 읽기, 행 단위·작업자 단위 되돌리기, 분류 결과 쓰기, 현황 집계(`inspect`), 재처리 대상 고르기(`escalate`), 다시 분류 대기(`requeue`)의 후보 읽기·백업 읽기·잠금·되돌리기.
- 분류용 LLM 요청: `prompts.py`(시스템 프롬프트, 사용자 메시지, 그림 행의 고정 문장 `PAGE_IMAGE_NOTE`), `llm_schemas.py`(응답 모양 `LLMExtraction`), `vocabulary/publishers.yaml`(발행처 사전).
- 발행처 사전 조회 `vocabulary/__init__.py`: 정식 이름 목록(`canonical_names`), 정식 이름의 종류(`publisher_type`), 정식 이름·별칭·표기 → 정식 이름(`canonical_for`), 파일 이름 표기(`filename_tag`)와 그 표기가 가리키는 발행처(`filename_publisher`), 사전 오류 `PublisherDictionaryError`. DB·네트워크·설정 없이 파일만 읽고, 함께 들어 있는 사전은 처음 쓸 때 한 번 읽어 둔다(`load_dictionary(path)`는 다른 파일을 새로 읽는다).
- 다시 분류 대기 명령 `requeue.py`(`tag requeue`): 조건에 맞는 분류된 행 고르기, 미리 보기, 실행 중인 작업 확인, 백업 CSV, 한 트랜잭션 되돌리기. AI를 부르지 않는다.
- 저장 값 `tagger_version`(`sql.TAGGER_VERSION`)과, 분류한 행에 남기는 `taxonomy_version`.
- 설정: `LLM_MODEL_DEFAULT`·`LLM_MODEL_ESCALATION`(옛 이름 `OPENAI_MODEL_*`도 읽음), `MAX_CONCURRENT_LLM`(2), `TAGGER_BATCH_SIZE_DEFAULT`(10), `LOCK_TTL_MINUTES`(30), `PER_ROW_DEADLINE_S`(90). 공용 값 `SUPABASE_DB_URL`·`KRX_CSV_PATH`·`STORAGE_BASE_DIR`·API 키·`CODEX_BIN`은 `core.settings`·`core.llm`으로 읽는다. 그림 쪽 수·해상도(`extract_pdf.PAGE_IMAGE_PAGES` 1, `PAGE_IMAGE_DPI` 120)와 그림 크기 한도(`PAGE_IMAGE_MAX_SIDE_PX` 7000, `PAGE_IMAGE_MAX_BASE64_BYTES` 4,500,000)는 `.env` 설정이 아니라 코드 상수다(그릴 때 읽으므로 바꿔 끼울 수 있다).

## 맡지 않는 일

- **import 경계**(구조 검사가 `R4 tagger`·`R8 입구`로 보고): `research_desk.core`·`research_desk.domain`·`research_desk.tagger`만 import한다. `research_desk.collector`·`research_desk.features.*`·`research_desk.web`·`research_desk.cli`·`research_desk.__main__`은 import하지 않는다. 바깥에서 이 칸을 쓰는 곳은 입구의 `register` 호출 하나다.
- **외부 도구**(`R9 외부 도구`): `langgraph`는 이 칸만 쓴다. 나머지는 core를 거친다 — `asyncpg`·`supabase`는 `core.db`, `openai`·`anthropic`은 `core.llm`(분류기 코드가 `core.llm`에서 쓰는 것은 `LLMClient.parse`, `TRANSIENT_ERRORS`, `supports_images`, `ImageInputUnsupported`이고, 응답 검증 오류는 `pydantic.ValidationError`로 받는다), `fitz`/`pymupdf`는 `core.pdf`, `dotenv`는 `core.settings`. `telethon`은 쓰지 않는다. `requeue`는 실행 중인 작업을 Windows PowerShell(`powershell.exe`의 `Get-CimInstance Win32_Process`)로 확인한다(새 패키지 없음). `tests/`는 이 검사에서 빠진다(테스트는 `fitz`·`pymupdf`·`openai`·`anthropic`을 직접 쓴다).
- **다른 표**(`R11 표 주인`): `failed_attempts`(수집기)와 `report_summaries`(분석 기능)는 다루지 않는다. `tag requeue`도 저장된 비교(`report_summaries`의 비교 칸)를 읽지도 고치지도 않는다. docstring이 아닌 문자열 속 `FROM`/`UPDATE`/`INTO`/`JOIN <표>`도 검사에 걸린다. `reports`도 위의 분류 몫만이다 — 검토 처리(승인·제외·재분류·되돌리기)는 `features/review`, 분류가 끝난 리포트 조회는 `features/reports`의 일이다.
- **주인이 따로 있는 규칙**
  - 값 집합(리포트 종류·제외 사유·발행처 종류·상태·신뢰도)은 `domain/vocabulary.yaml`. "분석 대상" 규칙, "분석 대상 외 행 모양", "되돌리는 모양"은 `domain/reports.py` — `oos_row_shape`·`pending_reset_shape`를 부르고, 칸 목록을 이 칸에 베끼지 않는다(`sql.REQUEUE_RESET_COLUMNS`도 `pending_reset_shape()`에서 만든다).
  - 종목표 읽기·조회·내용 지문·버전 확인은 `domain/stocks.py`. 버전 정보를 새로 쓰는 `stocks set-version`은 입구 `research_desk/cli.py`.
  - 모델 이름 → 공급자, 그림을 받는 모델인지(`supports_images`), 그림 붙이기, 키 확인, codex CLI 실행, LangSmith 감싸기는 `core/llm.py`. DB 풀과 트랜잭션은 `core/db.py`, PDF 글자·그림과 PyMuPDF 잠금은 `core/pdf.py`, `.env`는 `core/settings.py`.
  - 백필 반복·실패한 배치 되돌리기·재시도는 `scripts/run-batches.ps1`. 다만 그 스크립트가 이 칸의 종료 코드·`--worker-id`·`reset-worker`에 기댄다(아래).
- **DB 구조.** 표·열·제약은 바꾸지 않는다. 값 집합을 바꾸려면 CHECK 제약을 고치는 새 `migrations/` 파일이 따로 필요하다.
- **옛 코드**(`R10 옛 코드`, 테스트 포함): `langgraph_tagger`, 옛 루트 모듈 `collector`·`config`·`storage`·`telegram_client`·`main`.

## 늘 지켜야 할 것

**저장 값**

- `tagger_version`은 `langgraph-tagger@2.0`. `sql.TAGGER_VERSION` 한 곳에서 정의해 `UPDATE_SQL` 글자 안에 박는다(바인드 인자가 아니다). 테스트가 이 칸 코드(테스트 제외)에서 `langgraph-tagger@` 글자가 그 정의 한 줄에만 있는지 본다 — 주석이나 docstring에 써도 실패한다.
- `taxonomy_version`은 종목표 버전 정보 파일의 `"version"` 값(형식 `KRX@<자료 기준일>`, 예: `KRX@2026-05-08`)이다. 시작 확인이 돌려준 값을 그대로 `$19`로 쓴다. 파일 수정 시각이나 `vocabulary.yaml`로 만들면 안 된다 — 같은 종목표 내용에 표시가 갈라진다.
- 값 집합(리포트 종류 6, 제외 사유 5, 발행처 종류 4)은 `domain/vocabulary.yaml`·DB CHECK 제약과 같아야 한다. `llm_schemas.REPORT_TYPES`·`PUBLISHER_TYPES`나 `state.RowState.oos_reason`의 Literal을 한쪽만 고치면 테스트가 실패하고, DB가 쓰기를 거부한다. 발행처 사전의 구역 이름도 발행처 종류 4개 중 하나여야 한다(사전을 읽을 때 확인한다).
- 지금 규칙으로 분류한 행의 `publisher`는 발행처 사전의 정식 이름이거나 NULL이고, `publisher_type`은 그 이름의 사전 구역이거나 NULL이다.

**쓰기 SQL**

- `UPDATE_SQL` 글자는 그대로 둔다(테스트가 한 글자씩 비교한다). 인자 19개 순서: `$1 id`, `$2 published_at`, `$3 report_type`, `$4 publisher`, `$5 publisher_type`, `$6 analysts`, `$7 title`, `$8 stock_codes`, `$9 company_names`, `$10 stock_codes_raw`, `$11 company_names_raw`, `$12 sectors_major`, `$13 sectors_minor`, `$14 products`, `$15 out_of_scope_reason`, `$16 tagging_status`, `$17 tagging_confidence`, `$18 tagging_notes`, `$19 taxonomy_version`. 같은 문장이 `tagging_locked_at`·`tagging_worker_id`를 비우고 `tagged_at=now()`를 찍는다. 조건은 `WHERE id=$1`뿐이다(상태를 보지 않는다).
- `nodes/write._build_payload`의 세 모양. 발행처(`$4`·`$5`)는 어느 모양이든 AI의 날 답이 아니라 `llm_extract`가 확인한 값(`publisher_final`·`publisher_type_final`)이다.
  - 분석 대상 외(OOS, `is_oos`): `oos_row_shape(분류 값, 사유)` 그대로 — `published_at` NULL, `stock_codes`·`company_names`·`sectors_*`·`products` 빈 배열, `report_type`·`analysts`·`title`·`*_raw`는 LLM 값, `publisher`·`publisher_type`은 확인된 값 유지(`기타`로 바꾸지 않는다; 분포를 볼 때 `단일종목`+`foreign` 같은 정보가 남는다), 그리고 `out_of_scope_reason`.
  - LLM 결과 없음(못 읽음·거부): 분류 칸은 모두 NULL이나 빈 배열, 사유 NULL.
  - 분석 대상: LLM의 종류·애널리스트·제목, 확인된 발행처와 그 종류, `resolve_krx`의 최종 종목·회사·업종·제품·발행일, 원문 배열(`*_raw`는 늘 보존), 사유 NULL.
- `--dry-run`이면 `write`는 아무것도 하지 않는다.

**시작 확인과 종료 코드**

- `run`·`escalate`(`run --dry-run`·`run --row-ids` 포함)는 DB 풀을 열기 전에 아래 순서로 확인한다. 하나라도 실패하면 stderr에 한 줄, stdout은 비운 채 4로 끝나고 DB 함수를 하나도 부르지 않는다 — 어떤 행도 가져가지 않았다는 보장이다.
  1. 실제로 쓸 모델의 접근. 모델은 `--model` > `LLM_MODEL_DEFAULT`(escalate는 `LLM_MODEL_ESCALATION`) > 옛 이름 `OPENAI_MODEL_*` > 기본값(`claude-haiku-5-5` / `gpt-5.4`)이고 빈 값은 없는 것으로 본다. `claude-*` → `ANTHROPIC_API_KEY`, `codex:*` → codex CLI(`CODEX_BIN` 값 또는 PATH의 `codex`; `CODEX_BIN`은 값이 있는지만 본다), 그 밖 → `OPENAI_API_KEY`. 문구는 `<변수> is required` / `codex CLI not found for model <모델>`. 쓰지 않는 공급자의 키는 요구하지 않는다.
  2. `SUPABASE_DB_URL is required`.
  3. 종목표(`KRX_CSV_PATH`, 기본은 저장소에 함께 들어 있는 종목표, 현재 폴더 기준)를 못 읽음(없음, UTF-8 아님, 머리줄 틀림, 폴더): `종목표 파일을 읽을 수 없습니다: <이유>`.
  4. 버전 정보 파일이 없거나 형식이 틀리거나 내용 지문이 다름: `종목표 파일 내용이 버전 정보와 다릅니다. 종목표를 바꿨다면 python -m research_desk stocks set-version --as-of <자료 기준일, YYYY-MM-DD>를 실행한 뒤 다시 시작하세요.`
- `inspect`·`reset-worker`는 `SUPABASE_DB_URL`만 본다(모델 키·codex·종목표를 읽지 않는다).
- `requeue`는 조건 옵션이 하나도 없으면 `.env`를 읽기도 전에 argparse 사용법 오류 2로 끝난다(`대상 조건을 하나 이상 고르세요: --unreadable, --publisher-not-in-dictionary, --publisher-filename-mismatch, --publisher-type-mismatch, --krx-unmatched`). 그다음 확인은 모두 DB에 닿기 전이고, 실패하면 stderr 한 줄·stdout 비움·4다.
  1. `SUPABASE_DB_URL is required`.
  2. 발행처 사전을 그때 새로 읽는다. 못 읽거나 사전 규칙을 어기면 `발행처 사전을 읽을 수 없습니다: <이유>`.
  3. `--unreadable`이면 `tag run`이 쓸 모델(`LLM_MODEL_DEFAULT` > 옛 이름 > `claude-haiku-5-5`, `--model`은 없다)이 그림을 받는지 이름으로 본다(`core.llm.supports_images`). 못 받으면 `지금 분류 모델 <모델>은(는) 그림을 받지 못해 --unreadable 행을 되돌려도 다시 못 읽음이 됩니다. LLM_MODEL_DEFAULT를 그림을 받는 모델로 바꾼 뒤 다시 실행하세요.` 모델 키는 보지 않는다(이 명령은 AI를 부르지 않는다).
  4. `--apply`이면 이 PC에서 백필·재처리·수집·웹앱이 도는지 본다(아래 "다시 분류 대기"). 돌고 있으면 `실행 중인 작업이 있어 아무것도 바꾸지 않았습니다. 다음을 끈 뒤 다시 실행하세요: <작업> PID <번호>[, <번호>][ / <작업> PID …]`, 프로세스 목록을 못 읽으면 `프로세스 목록을 읽을 수 없어 실행 중인 작업을 확인하지 못했습니다. 아무것도 바꾸지 않았습니다: <이유>`. 미리 보기는 프로세스를 보지 않는다.
- `requeue`가 DB에 닿은 뒤의 실패는 traceback 대신 stderr 한 줄(`requeue.failure_line`)과 1이고 stdout은 비어 있다. 백업을 못 씀(`백업 파일을 쓰지 못해 아무것도 바꾸지 않았습니다: <이유>`)과 건수 불일치(`되돌린 행 수(<n>)가 다시 확인한 행 수(<m>)와 달라 모두 취소했습니다. 아무것도 바뀌지 않았습니다.`)는 아무것도 바꾸지 않은 채 끝나고, 그 밖의 오류는 `tag requeue를 마치지 못했습니다: <예외 이름>: <내용>`이다(트랜잭션 안에서 났으면 모두 되돌려진다).
- 문구는 글자 그대로 둔다(사람이 터미널에서 보고 따라 하고, 테스트가 고정한다). 영어 문구를 한국어로 옮기지 않는다.
- 4는 "아무것도 바꾸지 않았고, 안내대로 고친 뒤 다시 실행하면 되는 문제"다. 분류 명령에서는 어떤 행도 가져가지 않았다는 뜻이고, `requeue`에서는 어떤 행도 되돌리지 않았다는 뜻이다. 반복 실행 스크립트는 4를 받으면 되돌리기·재시도 없이 바로 멈추고, 그 밖의 0 아닌 코드에는 자기가 넘긴 `--worker-id`로 `reset-worker`를 부른 뒤 다시 시도한다. 그래서 새 준비 확인은 `_prepare_tagging` 안(DB 풀을 열기 전)에 `NotReadyToRun`으로 넣고, 행을 가져간 뒤의 실패에 4를 쓰면 안 된다 — 스크립트가 되돌리기를 건너뛰어 그 행이 `processing`에 묶인다. `--worker-id`·`reset-worker`의 이름과 동작도 이 스크립트가 쓰므로 바꾸지 않는다. 웹 기능용 `core.settings.NotReady`(503)는 이 칸에서 쓰지 않는다.
- 그 밖의 예외는 1이다. 숫자 설정 형식 오류(`MAX_CONCURRENT_LLM`·`LOCK_TTL_MINUTES`·`PER_ROW_DEADLINE_S`, `run`은 `TAGGER_BATCH_SIZE_DEFAULT`도 — 옵션으로 덮어써도 읽는다)와 `--row-ids` 형식 오류는 DB 전에 `ValueError`로 1, `escalate --since` 형식 오류는 풀을 연 뒤 행을 건드리기 전에 1이다. 인자 오류는 argparse의 2, `--help`는 0.

**보고(JSON)**

- stdout에는 JSON 하나만 나간다(들여쓰기 2, `ensure_ascii=False`). 다른 출력이나 로그를 stdout에 섞지 않는다.
  - `run`: `model, processed, auto, review_needed, confidence{high,medium,low}, oos{foreign,fund,digital,private,ir_self}, review_reasons, transient_errors, deadline_errors, unhandled_errors, page_image, page_image_unsupported, publisher_suspect, dry_run, batch_size`, `--dry-run`이면 그 뒤에 `rows`, 마지막에 `worker_id`. `processed`는 오류 난 행까지 센다.
    - `page_image`: 1쪽 그림으로 AI에게 물은 행 수. `page_image_unsupported`: 그림이 필요했지만 모델이 그림을 받지 못해 AI를 부르지 않은 행 수. `publisher_suspect`: 발행처 의심 표시가 붙은 행 수. 셋 다 검토 사유가 아니라 `review_reasons`에 들어가지 않는다.
    - `rows`(`--dry-run`만): 행마다 `{id, tagging_status, tagging_confidence, report_type, publisher, publisher_type, tagging_notes}` — 썼다면 들어갔을 값이고 발행처는 확인된 값이다. 오류 난 행은 `{id, error, detail}`(`error`는 `transient`·`deadline_exceeded`·`unhandled`, 시간 초과의 `detail`은 null). `--dry-run --row-ids`로 고른 행을 저장 없이 시험할 때 쓴다.
    - 가져간 행이 없으면 예전 그대로의 빈 보고(`model, processed, auto, review_needed, confidence, oos, review_reasons, transient_errors, deadline_errors, unhandled_errors`)에 `worker_id`만 붙는다. 새 세 키·`dry_run`·`batch_size`·`rows`가 없다.
  - `escalate`: 대상이 없으면 들여쓰기 없는 한 줄 `{"escalated": 0, "since": "<받은 글자 그대로>"}`, 있으면 `run`과 같은 보고에서 `worker_id`만 없다(지금 그대로 둔다). `--dry-run`이 없으니 `rows`도 없다.
  - `inspect`: `pending, processing, auto, review_needed, verified, oos_total, last_24h`. `reset-worker`: `worker_id, reset_count, ids`.
  - `requeue`: 미리 보기 `mode, selected, skipped, total, publisher_values, unknown_filename_tags`, 적용 `mode, selected, skipped, total, requeued, backup_file`(이 순서). 뜻은 아래 "다시 분류 대기". stdout이 콘솔 코드 페이지라 저장된 값의 글자를 못 보여 주면(`UnicodeEncodeError`) 같은 JSON을 `\uXXXX`로 이스케이프해 다시 낸다 — `--apply`면 이미 바뀐 뒤라 보고를 잃지 않으려는 것이다.

**동시 처리와 배치**

- `MAX_CONCURRENT_LLM` 기본 2와 `TAGGER_BATCH_SIZE_DEFAULT` 기본 10은 운영 기준값이다. 실제 병목은 공급자의 분당 토큰 한도라서 동시 처리를 올리면 429만 늘어 토큰을 쓰고 처리량은 떨어진다. 배치를 키워도 처리량은 같고, 실패 때 되돌릴 범위와 보고 사이 간격만 커진다. 기본값과 `.env.example` 값을 임의로 올리지 않는다(공급자 한도와 토큰 추이를 확인한 뒤 사용자가 정하고, 한 단계씩).
- `--batch-size 0`·`--max-concurrent-llm 0`은 설정값으로 돌아간다.
- 세마포어는 LLM 호출만이 아니라 행 하나의 그래프 전체(PDF 읽기·그리기 → LLM → 쓰기)와 되돌리기를 감싼다. 행 시간 한도 `PER_ROW_DEADLINE_S`는 칸을 얻은 뒤부터 잰다.
- PDF 접근(글자 읽기·그림 그리기)은 `core.pdf`의 프로세스 전체 잠금 하나로 줄 선다. 두 행이 동시에 돌아도 PyMuPDF 안에는 한 호출만 있고 다른 행은 잠금을 기다린다. AI 호출은 줄 세우지 않는다(동시 2 그대로). 잠금을 기다리는 시간도 그 행의 `PER_ROW_DEADLINE_S`에 들어간다. PyMuPDF 호출 하나가 멈추면 그 프로세스의 뒤 PDF 호출이 모두 기다리다가, 그 배치의 남은 행이 시간 초과로 `pending`에 돌아간다.
- 잠금 시각은 가져갈 때 배치 전체에 한 번 찍히고, 칸을 기다리는 행도 그동안 `processing`이다. 그래서 `LOCK_TTL_MINUTES`는 배치 하나의 최장 시간(⌈배치 크기 ÷ 동시 처리 수⌉ × `PER_ROW_DEADLINE_S`; 기본 ⌈10 ÷ 2⌉ × 90초 = 7.5분)보다 길어야 한다. 짧으면 다른 작업자가 시작하면서 아직 기다리는 행을 `pending`으로 돌려 다시 가져가고, 같은 행이 두 번 분류된다.
- DB 풀 최대 10, SDK 재시도 2회, 요청 시간 한도 60초는 설정이 아닌 고정값(`settings.py`)이다. 60초 × 3번은 행 한도 90초보다 길어서, 느린 호출은 행 한도가 먼저 끊는다.

**`pending`으로 돌아가는 경우**

- 정상 `run`(`--dry-run`도 `--row-ids`도 아닐 때)에서만, `REVERT_TO_PENDING_SQL`(조건 `tagging_status='processing'`)로 되돌린다:
  - `LLMTransientError` → `transient_errors`: `core.llm.TRANSIENT_ERRORS`(두 공급자의 429·5xx·시간 초과·연결 오류, Anthropic 과부하)와, 응답이 `LLMExtraction` 검증에 실패한 `pydantic.ValidationError`(예: 제목 120자·메모 200자 초과). 발행처 답은 검증 오류를 만들지 않는다 — 정식 이름이 아닌 답은 NULL이 된다(아래 "발행처").
  - 행 시간 한도 초과 → `deadline_errors`.
  - 그 밖의 모든 `Exception`(노드 버그, DB 일시 오류, codex 실행 실패, 키가 틀려 거절됨 등) → `unhandled_errors`. 행 하나의 실패가 `asyncio.gather`를 깨고 다른 행을 `processing`에 남기지 않게 하는 넓은 except다 — 좁히지 않는다.
- `ImageInputUnsupported`(그림을 못 받는 모델에 그림을 붙임)는 되돌리는 오류가 아니다. `llm_extract`가 잡아 그 행을 못 읽음(`page_image_unsupported`)으로 마감한다.
- 되돌리기 자체가 실패하면 행은 `processing`에 남는다. 다음 정상 `run`이 시작할 때 `STALE_LOCK_RECLAIM_SQL`이 잠금이 `LOCK_TTL_MINUTES`보다 오래된 `processing` 행을 되돌린다. 그 밖에 계속 지켜보는 장치는 없다.
- `reset-worker --worker-id W`는 그 작업자의 `processing` 행만 되돌리고 `ids`를 알린다. 시간이 아니라 작업자로 범위를 좁혀, 같은 PC에서 아직 도는 다른 작업자를 건드리지 않는다.
- 되돌리지 않는 것: LLM 거부와 못 읽는 PDF(정상적으로 `review_needed` 기록), `--dry-run`·`--row-ids`·`escalate`의 실패(가져가지 않은 행이라 원래 상태 그대로 — 지정한 행의 상태는 쓰기 말고는 바꾸지 않는다).
- Ctrl+C나 프로세스 종료(`KeyboardInterrupt`·`CancelledError`는 `Exception`이 아니다)는 되돌리지 않는다. 그 배치가 가져간 행은 `processing`으로 남아, 잠금이 `LOCK_TTL_MINUTES`를 넘긴 뒤 시작하는 `run`이 되돌린다. 바로 풀려면 반복 실행 스크립트가 찍은 `worker_id`로 `reset-worker`를 돌린다.
- 키가 틀리거나 만료돼 모든 호출이 거절돼도 명령은 0으로 끝나고 행은 모두 `pending`으로 돌아간다. 반복 실행 스크립트는 멈추지 않으니 보고의 `unhandled_errors`·`transient_errors`를 본다.
- 가져가는 순서가 `downloaded_at` 오름차순이라 되돌린 행은 다음 배치에서 다시 먼저 잡힌다. 늘 같은 오류를 내는 행은 배치마다 한 자리를 차지한다(자동으로 `review_needed`로 넘기는 장치는 없다 — 넣는다면 의도한 동작 변경이다).
- 분류가 끝난 `auto`·`review_needed` 행을 `pending`으로 되돌리는 길은 `tag requeue --apply`다(아래 "다시 분류 대기"). 검토 화면의 재분류도 같은 모양으로 `review_needed` 행을 되돌린다.

**그래프 모양, 분기·상태·신뢰도·메모**

```
START -> extract_pdf -> llm_extract --oos_gate--+-> status_unreadable               -> write -> END
                                                +-> mark_oos_reason -> status_oos   -> write
                                                +-> resolve_krx -> decide_status    -> write
```

- 노드 8개와 연결은 그대로 둔다(테스트가 노드 집합을 고정한다). 노드를 더하거나 빼는 것은 분류 흐름의 변경이다.
- `extract_pdf`: 1쪽부터 최대 3쪽(`MAX_PAGES`)까지 읽고, 지금까지 읽은 글자를 이어 붙인 것에 `_META_KEYWORDS` 중 하나가 나오면 멈춘다(목록은 테스트가 고정한다). 이어 붙인 글자가 비면 `pdf_unreadable`이고, **그때만** 1쪽을 그림(PNG)으로 그려 `page_images`에 담는다(`PAGE_IMAGE_PAGES` 1쪽, `PAGE_IMAGE_DPI` 120dpi). 1~3쪽 중 한 쪽에라도 글자가 있으면(표지 정보가 부족해 3쪽까지 읽은 경우도) 그림을 그리지 않는다.
  - 그림이 AI 공급자 한도를 넘으면(긴 변 7000px 초과 또는 base64 글자 4.5MB 초과 — Anthropic의 8000px·5MiB보다 여유를 둔 값) 해상도를 낮춰 최대 4번 다시 그리고, 그래도 넘으면 그림이 없는 것으로 친다. 넘는 그림을 보내면 매번 거절돼 그 행이 배치마다 `pending`으로 돌아가기 때문이다. 보통 쪽은 120dpi로 한 번만 그린다.
  - 파일이 없거나 깨졌거나 암호가 걸렸거나 0쪽이거나 그리기 오류면 예외가 아니라 글자 `""`·그림 `[]`이다 — `STORAGE_BASE_DIR`가 틀리면 대기 행이 줄줄이 `first_page_unreadable`로 검토 대기열에 빠진다. 상대 `file_path`는 행마다 그때의 `STORAGE_BASE_DIR`(기본 `./reports`, 현재 폴더 기준) 아래에서 찾고, 절대 경로는 그대로 쓴다.
- `llm_extract`: 글자가 있으면 글자로 한 번 묻는다. `pdf_unreadable`인데 그림이 있으면 같은 시스템 프롬프트·응답 모양으로, 사용자 메시지의 PDF 글자 자리에 고정 문장 `PAGE_IMAGE_NOTE`를 넣고 그림을 붙여 묻는다. 그러면 `page_image=True`, `pdf_unreadable=False`가 되어 그 답은 글자 행과 같은 갈래로 간다(거부·빈 답 포함). 다 쓴 그림은 상태에서 비운다(`page_images=[]`, 뒤 노드와 추적 기록에 그림이 따라다니지 않게). 그림이 없으면 AI를 부르지 않는다(`llm_raw=None`). 모델이 그림을 받지 못하면(`supports_images`가 거짓이거나 `parse`가 `ImageInputUnsupported`) AI를 부르지 않고 `page_image_unsupported=True`, 행은 못 읽음 그대로다.
- `llm_extract`는 AI 결과(`llm_raw`)가 있으면 행이 어느 갈래로 가든(분석 대상 외 포함) `publisher_checks`로 `publisher_final`·`publisher_type_final`·`publisher_suspect`를 남긴다(아래 "발행처"). 못 읽음·거부 행에는 없다.
- `oos_gate`(분기 함수 — 이름표만 돌려주고 상태를 바꾸지 않는다. LangGraph의 규약이고 테스트가 확인한다. OOS 표시는 `mark_oos_reason`이 한다). 위에서부터 처음 맞는 것:
  1. `pdf_unreadable`, `llm_refusal`, 또는 `llm_raw`가 없음 → `status_unreadable`
  2. `report_type`이 `IR자료` → `mark_oos_reason`
  3. `foreign_primary_coverage`·`etf_or_fund`·`digital_asset` 중 하나 → `mark_oos_reason`
  4. `private_company_likely`: `stock_codes_raw` 중 하나라도 종목표 코드면 `resolve_krx`, 아니면 `mark_oos_reason`(회사명은 보지 않는다)
  5. 나머지 → `resolve_krx`
- `mark_oos_reason`의 사유: `IR자료`면 `ir_self`, 그다음 `foreign` → `fund` → `digital`, 나머지 `private`.
- `resolve_krx`(리포트 종류별):
  - `산업`·`전략·시황`: 찾지 않는다(`krx_lookup_skipped`). 최종 종목·회사·업종·제품은 모두 빈 배열. 회사 하나로 대표할 수 없는 리포트라, 매칭을 강제하면 정상 리포트가 검토로 쏟아진다.
  - `단일종목`·`기타`: `stock_codes_raw`를 종목표에서 찾고(6자리 대문자·숫자, 0을 채우지 않고 그대로 일치), 하나도 없으면 `company_names_raw`에서 처음 맞는 회사 하나(공백·대소문자 무시). 결과는 하나만 남긴다.
  - `섹터`: 코드와 회사명으로 찾은 것을 모두 모은다(중복 제거, 순서 유지).
  - 찾으면 종목·회사명은 종목표 값(정식 이름으로 덮음), 업종(대·중)은 중복 없는 합집합, 제품은 `주요제품` 칸을 쉼표로 나누고 항목 끝에 붙은 `등`을 뗀 합집합이다. 업종·제품의 답은 LLM이 아니라 종목표다. 못 찾으면 종목·업종·제품은 빈 배열, 회사명은 원문 그대로.
  - 이름 불일치(`krx_name_code_mismatch`): `단일종목`이 코드로 찾아졌고 원문 회사명이 있는데, 공백을 빼고 소문자로 맞춘 정식 이름이 원문 회사명들 가운데 없을 때.
  - 발행일: LLM의 `published_at`이 ISO 날짜로 읽히면 그 값, 아니면 `sent_at`(시간대가 없으면 UTC로 봄)의 한국 날짜와 `used_sent_at_fallback`.
- 상태·신뢰도·기본 메모(세 상태 노드를 합친 표, 위에서부터 처음 맞는 것):

  | 조건 | 노드 | 상태 / 신뢰도 / 기본 메모 |
  |---|---|---|
  | 글자도 그림도 못 얻음, 또는 그림을 못 받는 모델 | `status_unreadable` | `review_needed` / `low` / `first_page_unreadable` |
  | AI 거부(빈 답 포함) | `status_unreadable` | `review_needed` / `low` / `llm_refusal:<이유>`(이유가 없으면 `llm_refusal:`) |
  | 분석 대상 외 | `status_oos` | `auto` / `private`이면 `medium`, 나머지 `high` / 없음 |
  | `단일종목`인데 종목표에서 못 찾음 | `decide_status` | `review_needed` / `low` / `krx_unmatched_in_scope:ipo_pending_or_unknown` |
  | `기타`이고 LLM `self_confidence`가 `low` | `decide_status` | `review_needed` / `low` / `type_indeterminate` |
  | 나머지 분석 대상 | `decide_status` | `auto` / 발행일을 보낸 시각으로 채움·2쪽 이상 읽음·이름 불일치 중 하나면 `medium`, 아니면 `high` / 이름 불일치면 `krx_name_code_mismatch`, 아니면 없음 |

- 마지막 규칙(`decide_status.apply_final_rules` — 세 상태 노드가 모두 마지막에 부른다):
  - 그림으로 읽은 행(`page_image`)이나 의심 표시가 붙은 행(`publisher_suspect`)은 `high`를 `medium`으로 낮춘다. `low`는 그대로다.
  - 메모는 기본 메모, `page_image`, `publisher_suspect:<filename_mismatch|unknown>` 순서로 `;`로 잇는다. 예: `krx_unmatched_in_scope:ipo_pending_or_unknown;page_image;publisher_suspect:unknown`, 그림 행의 거부는 `llm_refusal:<이유>;page_image`.
  - 상태는 바꾸지 않는다(둘 다 검토 사유가 아니다). `auto`는 `auto` 그대로 기업 화면에 바로 나오고, `medium`과 메모로 나중에 골라 볼 수 있다. 그림을 못 받은 행(`page_image_unsupported`)에는 메모가 붙지 않는다.
  - 그림도 의심도 없는 행의 상태·신뢰도·메모는 이 규칙 전과 글자 하나 다르지 않다(기존 패리티 사례가 지킨다).
- 그래서 `review_needed`는 늘 `low`이고, 0건 매칭 `섹터`·못 찾은 `기타`·`산업`·`전략·시황`은 `auto`다. 사람이 보는 것은 단일종목 미매칭(상장 예정, 오래된 종목표)이고, 이름 불일치와 발행일 대체는 원문 칸으로 나중에 확인할 수 있어 검토로 보내지 않는다.
- 메모 형식은 `<사유>[:<자세히>]`, 여러 개면 `;`로 잇는다. `_aggregate`의 `review_reasons`는 `;`로 나눈 조각마다 `:` 앞 이름이 `first_page_unreadable`·`llm_refusal`·`type_indeterminate`·`krx_unmatched_in_scope`인 것만 센다(`page_image`·`publisher_suspect`·`krx_name_code_mismatch`는 세지 않는다). 검토 화면은 메모를 그대로 보여 주고 운영 감시(검토 사유 분포)도 이 이름으로 본다 — 이름을 바꾸거나 사유를 더하면 `_aggregate`와 패리티 사례를 함께 고친다. `tag requeue`의 조건도 메모 이름(`first_page_unreadable`, `llm_refusal`, `krx_unmatched_in_scope`, `publisher_suspect`)을 본다.
- LLM의 `notes`와 `self_confidence`는 저장하지 않는다(`self_confidence`는 `type_indeterminate` 판단에만 쓴다). `tagging_notes`에는 위 시스템 메모만 들어간다.

**LLM 요청**

- `llm_extract`는 `client.parse(model, system=SYSTEM_PROMPT, user=user_message(...), schema=LLMExtraction, temperature=…)` 한 번이고, 그림 행만 `images=[1쪽 PNG]`를 더한다. 모델 이름에 `luna`가 들어 있으면 `temperature=None`(0을 거부하는 모델), 아니면 0이다(Claude·codex 경로에서는 `core.llm`이 넣지 않는다). Claude는 응답 형식을 문법으로 강제하는 기본 경로(`constrained=True`)를 쓴다.
- 재시도는 SDK 재시도뿐이다. 분석·비교가 쓰는 `core.llm.call_with_retry`(5초 뒤 한 번 더)를 쓰지 않는다 — 분류기는 행을 되돌려 다음 배치가 다시 하게 한다.
- `SYSTEM_PROMPT`는 import할 때 `domain.reports.REPORT_TYPES`(`vocabulary.yaml`의 순서 그대로)와 `vocabulary/publishers.yaml` 원문(주석까지)으로 만들어지고, `LLMExtraction`의 클래스 docstring과 `Field(description=…)`, 그리고 `publisher_canon`의 정식 이름 enum(사전의 정식 이름, 파일 순서)도 요청 스키마로 나간다. 이 중 무엇을 고쳐도(사전 주석 한 줄이라도) LLM 요청이 바뀐다: 분류 결과가 달라질 수 있고 Anthropic의 시스템 프롬프트 캐시도 새로 쌓인다. 그래서 백필이 도는 동안 사전을 고치지 않는다.
- 행마다 달라지는 값은 `user_message`(파일명·캡션·보낸 시각(UTC)·PDF 글자 또는 `PAGE_IMAGE_NOTE`)와 붙인 그림에만 넣는다 — 시스템 프롬프트가 행마다 같아야 캐시가 맞는다. `PAGE_IMAGE_NOTE`도 모든 그림 행에 같은 고정 글이라, 고치면 그림 행의 요청이 바뀐다.

**발행처**

- 발행처 이름의 원본은 `vocabulary/publishers.yaml` 하나다. 구역(`broker`/`data_provider`/`ir_agency`/`other`)이 그 구역 발행처의 `publisher_type`이고, 항목은 `canonical`(정식 이름)과 `aliases`(옛 이름·영문 이름·약칭, 그리고 채널이 파일 이름 끝에 붙이는 표기)다.
- 사전 규칙. `load_dictionary`가 읽을 때 확인하고(어기면 `PublisherDictionaryError`), `tests/test_vocabulary.py`가 함께 들어 있는 사전을 지킨다: 구역은 발행처 종류 4개 중 하나, 항목 키는 `canonical`·`aliases`만, 정식 이름은 서로 겹치지 않는다, 별칭 하나는 정확히 한 항목에 한 번만 속하고 어떤 항목의 정식 이름과도 같지 않다, 앞뒤 공백 없는 글자다. 테스트는 더해서 관찰된 파일 이름 표기 목록(`OBSERVED_FILENAME_TAGS`)이 모두 사전에 있는지, `미래대우증권`이 없는지, 기술분석보고서 작성기관 이름이 `한국IR협의회`의 별칭인지, 머리 주석에 없어진 함수 이름(`lookup_publisher`)이 남아 있지 않은지 본다.
- 정식 이름은 표지에 적힌 지금 회사명이다(한국어 정식 상호가 있으면 그것; 예: `DB증권`, `한국IR협의회`, `iM증권`, `에프앤가이드`, `한국거래소`). 증권사가 아닌 독립 리서치는 `data_provider`다. 기술분석보고서는 표지의 작성기관(한국기업평가·NICE신용평가·NICE평가정보·한국기술신용평가·서울평가정보 등)과 상관없이 `한국IR협의회`로 둔다(사용자 결정, 2026-10-10). IR자료의 발행처는 `해당기업`(`other`)이다.
- AI의 답은 닫힌 목록이다. 요청 스키마의 `publisher_canon`이 정식 이름 enum이라, 스키마를 강제하는 경로(Claude 기본 경로, OpenAI·codex 엄격 스키마)는 다른 값을 낼 수 없다. 어느 경로든 `LLMExtraction`의 before 검증기가 정식 이름과 글자 그대로 같지 않은 답(별칭, 오타, 문장, 문자열이 아닌 값)을 None으로 바꾼다. 검증 오류를 내지 않는다 — 오류면 그 행이 배치마다 `pending`으로 되돌려지는 고리가 생긴다. 필드를 `Literal`로 바꾸지 않는다(스키마를 강제하지 않는 경로에서 오류가 난다).
- AI는 발행처 종류를 내지 않는다. 저장하는 종류는 저장할 정식 이름의 사전 구역이다(`publisher_type_final`, 이름이 None이면 None). 응답에 옛 `publisher_type` 키가 섞여 와도 버려진다.
- 파일 이름 표기: 파일 이름이 `_YYYYMMDD_<표기>_<숫자>.pdf`로 끝나면 그 `<표기>`다(글자가 하나 이상이고 글자·공백·`+ & . -`만). 표기가 사전의 정식 이름이나 별칭과 글자 그대로(대소문자까지) 같으면 그 항목의 정식 이름이 "파일 이름이 가리키는 발행처"다. 사전에 없는 표기는 아무것도 가리키지 않는다. 끝 모양이 다른 이름(예: 번호 뒤에 `_1`이 더 붙은 `…_<숫자>_1.pdf`)은 표기가 없는 것이다.
- 의심 표시(`publisher_suspect`, AI 결과가 있는 행만): 가리키는 발행처가 있는데 저장할 발행처가 그것과 다르면(None 포함) `filename_mismatch`, 가리키는 발행처가 없고 저장할 발행처가 None이면 `unknown`, 그 밖에는 없음. 비교는 정식 이름끼리다 — AI가 별칭(`Eugene`)을 답하면 None으로 저장되고 표기가 `Eugene`이면 `filename_mismatch`다. IR자료(`해당기업`)가 증권사 표기 파일이면 `filename_mismatch`가 붙는다(IR자료로 잘못 분류된 증권사 리포트를 찾는 신호).
- 좁혀 둔 예전 결정(결정 기록 0011): PDF 속 글자를 코드로 대조해 발행처를 정하지 않는다(판단은 AI). 코드는 AI 답이 사전 정식 이름인지 확인만 한다. 파일 이름 표기는 저장할 값을 정하지 않고 의심 표시와 다시 분류 대상 고르기에만 쓴다. 영문·별칭·부서명이 섞인 발행처를 코드 대조로 맞추다 실패한 적이 있고, 파일 이름 표기로 저장하는 안은 사용자가 뺐다. "표기가 대부분 맞으니 그걸로 저장하자"로 바꾸지 않는다.

**다시 분류 대기(`tag requeue`)**

- 이미 분류된 행 가운데 조건에 맞는 것을 `pending`으로 되돌린다. 다시 분류는 평소 백필(`run-batches.ps1`, 동시 2·배치 10)이 지금 규칙으로 한다. 이 명령은 AI를 부르지 않는다. 사전을 고친 뒤나 분류 규칙이 바뀐 뒤 되풀이해 쓰는 운영 도구다.
- 조건(하나 이상; 옵션 이름의 `-`를 `_`로 바꾼 것이 보고 키이고 순서는 `requeue.CRITERIA`):

  | 옵션 | 고르는 행 |
  |---|---|
  | `--unreadable` | `review_needed`이면서 메모가 `first_page_unreadable`로 시작하고, 지금 PDF 1쪽을 그림으로 만들 수 있는 행 |
  | `--publisher-not-in-dictionary` | 발행처가 NULL이 아니고 사전의 정식 이름이 아닌 행 |
  | `--publisher-filename-mismatch` | 파일 이름이 가리키는 발행처가 있고 저장된 발행처가 그것과 다른 행(NULL 포함). 메모에 `publisher_suspect`가 있는 행(이미 지금 규칙으로 확인됨)과 메모가 `first_page_unreadable`·`llm_refusal`로 시작하는 행(AI 결과가 없음)은 뺀다 |
  | `--publisher-type-mismatch` | 발행처가 정식 이름인데 저장된 종류가 사전의 종류와 다른 행 |
  | `--krx-unmatched` | `review_needed`이면서 메모가 `krx_unmatched_in_scope`로 시작하는 행(종목표를 바꾼 뒤 쓰는 용도) |

- 상태가 `auto`·`review_needed`인 행만 고른다. `verified`(사람이 승인한 행)·`processing`·`pending`은 어떤 조건에서도 고르지 않는다. 한 행이 여러 조건에 맞으면 조건별 건수에 각각 세고 되돌리기는 한 번이다.
- 후보는 분류된 행(`auto`·`review_needed`·`verified`) 전체를 한 번에 읽어(`REQUEUE_CANDIDATES_SQL`) 파이썬에서 고른다. Supabase REST가 아니라 `core.db` 직접 연결이다(REST는 1000행에서 끊긴다). 1쪽 그림 확인은 `--unreadable`의 메모 조건에 맞는 행만 한다. 확인은 분류기 자신의 그림 규칙(`extract_pdf._render_page_images`, 한도를 넘으면 줄여 다시 그림)을 저장 폴더 안의 `.pdf`(`core.pdf.resolve_in_storage`)에 쓴다 — 여기서 고른 행은 분류기가 정말 그림으로 읽는다.
- 미리 보기(기본)는 아무것도 쓰지 않는다. `selected`는 고른 조건만, `skipped`는 `--unreadable`을 골랐을 때만 `{"unreadable_no_page": n}`(메모 조건은 맞지만 지금 1쪽 그림을 만들 수 없어 고르지 않은 행 — PDF 폴더 설정이 틀리면 커진다)이고 아니면 `{}`, `total`은 중복 없는 행 수, `publisher_values`는 고른 행의 지금 발행처 값별 건수(NULL은 `(null)`, 많은 순), `unknown_filename_tags`는 고른 조건과 상관없이 분류된 행 전체에서 사전에 없는 파일 이름 표기별 건수(많은 순, 사전 보충 참고용)다. 저장된 비교 수는 내지 않는다.
- 적용(`--apply`):
  1. 실행 확인(시작 확인 4번). `Get-CimInstance Win32_Process`의 명령줄에 `run-batches.ps1`이 있거나, `research_desk` 다음 낱말이 `tag run`(백필)·`tag escalate`(재처리)·`collect`(수집)·`web`(웹앱)이면 찾는다. 자기 자신과 `tag requeue`·`tag inspect`·`tag reset-worker`·`stocks`는 아니다. 자기 프로세스가 목록에 없으면 목록을 믿을 수 없다고 보고 4다.
  2. 대상을 다시 고른다(미리 보기를 믿지 않는다). 0건이면 백업도 쓰기도 없이 `requeued: 0`, `backup_file: null`, 0으로 끝난다.
  3. 백업. 고른 행의 `sql.REQUEUE_BACKUP_COLUMNS`(`id`, `file_name`, `tagging_status`, `tagged_at`, 그리고 되돌리며 비울 나머지 칸 전부)를 Postgres 글자(`::text`, 배열은 `{a,"b c"}` 꼴)로 읽어 `<저장소>/backups/requeue/requeue-YYYYMMDD-HHMMSS.csv`(로컬 시각, 같은 초면 `-1`, `-2` …, 있는 파일을 덮지 않음)에 쓴다. UTF-8(BOM 없음), 머리줄, 줄 끝 CRLF, NULL은 따옴표 없는 빈 칸, 값은 모두 따옴표로 감싼다(Postgres `COPY … CSV HEADER`로 NULL까지 다시 읽힌다). 저장소 위치는 명령을 띄운 폴더가 아니라 코드 위치로 정하고, `/backups/`는 `.gitignore`에 있다. 백업을 못 쓰면 아무것도 바꾸지 않고 1이다(반쯤 쓴 파일은 지운다).
  4. 한 트랜잭션(`SupabaseSQL.transaction()`): 행을 id 순서로 잠그고(`REQUEUE_LOCK_SQL`, `FOR UPDATE`), 잠근 값이 백업 값과 같고 여전히 상태·조건을 만족하는 행만 남긴다. 백업 뒤 바뀐 행은 빠진다 — 백업이 덮어쓴 값을 그대로 담게 하려는 것이다. 1쪽 그림 가능 여부는 트랜잭션 전의 확인 값을 쓴다. 남은 행을 `domain.reports.pending_reset_shape()`(검토의 재분류와 같은 22열 모양)로 되돌리고(`REQUEUE_RESET_SQL`, 조건에 `tagging_status IN ('auto', 'review_needed')`도 있다), 되돌린 id가 남긴 id와 다르면 모두 취소하고 1이다.
  5. `requeued`는 실제로 되돌린 행 수다. 그사이 바뀐 행이 빠지면 `total`보다 작다. `backup_file`은 백업 파일의 절대 경로다.
- 백업은 기록용이다. 백업에서 되돌리는 명령은 없다(사용자 결정).
- 실행 확인의 사각지대: 관리자 권한으로 띄운 프로세스는 일반 권한에서 명령줄이 비어 보여(`CommandLine` null) 찾지 못한다. 백필·수집·웹앱은 문서대로 일반 터미널에서 띄운다.
- 같은 행이 실행마다 되돌려지는 고리를 막는 규칙: `--unreadable`은 지금 그림을 만들 수 없는 행을 고르지 않고, 분류 모델이 그림을 못 받으면 4로 멈춘다. `--publisher-filename-mismatch`는 의심 표시가 이미 붙은 행과 못 읽음·거부 행을 고르지 않는다(이런 행에는 의심 표시가 붙지 않는다).
- 되돌린 행은 다시 분류될 때까지 웹 목록에서 빠진다. 가져가는 순서가 `downloaded_at` 오름차순이라 새로 수집한 리포트보다 먼저 다시 분류된다.

**알려진 문제(일부러 그대로 둠)**

- `escalate --since T`: `review_needed`이면서 `tagged_at >= T`인 행 id를 모두 골라 지정 행 방식(가져가기 표시 없음)으로 한 번에(`batch_size=len(ids)`) 다시 분류하고, 상태 확인 없이 `id`로 덮어쓴다. 그사이 검토 화면에서 승인·제외한 행을 덮을 수 있다. `T`에 시간대가 없으면 UTC로 읽는다(한국 시간이면 `+09:00`을 붙인다). 다시 분류된 행은 `tagged_at`이 새로 찍혀, 여전히 `review_needed`면 같은 `T`로 또 골라지고 검토 대기열 맨 뒤로 간다.
- `run --row-ids 1,2,3`: 상태와 상관없이(`auto`·`verified`도) 지정한 행을 다시 분류해 덮어쓴다. 가져가기 표시가 없어 동시에 도는 작업자와 겹칠 수 있고, 사람이 승인한 `verified` 행도 `auto`/`review_needed`로 돌아간다.
- 둘 다 상태 확인을 넣으면 "지정한 행은 무조건 다시 분류한다"는 지금 동작이 바뀐다. 말없이 고치지 않는다 — 바꾸려면 동작 변경으로 밝히고 테스트를 함께 고친다. 조건으로 고를 수 있는 행을 다시 분류할 때는 이 둘 대신 `tag requeue`를 쓴다(가져가기 표시는 평소 백필이 붙인다).

**가벼운 import와 금지 글자**

- 입구는 어떤 명령이든 `tagger/cli.py`를 import한다. 그래서 `tagger/__init__.py`와 `cli.py`(그리고 `cli.py`가 최상위에서 부르는 `sql.py`·`settings.py`)에서 `orchestrator`·`graph`·`langgraph`를 최상위 import하지 않는다. 행 처리 그래프는 `cli.run_batch`가 처음 불릴 때 `orchestrator`를 불러온다. 최상위로 올리면 `collect`·`web`·`stocks`·`--help`까지 LangGraph와 그 경고(`LangChainPendingDeprecationWarning`)를 끌고 와 입구 테스트가 실패한다.
- `requeue` 모듈(그리고 그것이 부르는 PyMuPDF·발행처 사전)도 `tag requeue`가 실행될 때 함수 안에서 불러온다. 입구 테스트가 인자 없는 실행에서 `research_desk.tagger.requeue`가 올라오지 않는지 본다.
- `HEARTBEAT` 글자를 이 칸 코드에 쓰지 않는다(없앤 설정이고, 테스트가 찾는다).

## 이 칸의 방식

- **흐름.** `_run`: `settings.load_env()` → `_prepare_tagging`(확인, 그리고 `_Tagging` 묶음) → 배치 크기·`--row-ids`·작업자 ID(`--worker-id`, 없으면 `<호스트>-<pid>-<16진 4자>`) → `asyncio.run(_cmd_run)`: `_open_db()`(`core.db.SupabaseSQL.from_env(max_size=10)`) + `_make_llm_client()` → `run_batch(...)` → 보고에 `worker_id`를 붙여 출력 → `finally`에서 풀과 클라이언트를 닫는다. 상대 경로 기본값(`KRX_CSV_PATH`, `STORAGE_BASE_DIR`)이 현재 폴더 기준이라 저장소 루트에서 실행한다.
- **`requeue` 흐름.** `_requeue`: 조건이 없으면 사용법 오류 2 → `settings.load_env()` → `_require("SUPABASE_DB_URL")` → `requeue.prepare`(사전·그림·실행 확인) → `asyncio.run(_cmd_requeue)`: `_open_db()` → `requeue.run`(미리 보기 또는 적용) → `finally`에서 풀을 닫는다 → 보고 출력. 실패는 `requeue.failure_line`의 한 줄로 1.
- **준비 확인.** `_require`(`core.settings.MissingSetting`의 `<변수> is required`를 그대로 옮김)·`_check_model_access`·`_load_stock_list`가 이 칸 전용 `NotReadyToRun`을, `requeue.prepare`가 `RequeueNotReady`를 내고, 하위 명령 함수가 잡아 `_not_ready`로 stderr에 쓰고 4를 돌려준다. `inspect`·`reset-worker`는 `_require("SUPABASE_DB_URL")`만 부른다.
- **배치.** `orchestrator.run_batch`: (정상 모드만) 오래된 잠금 되돌리기 → 행 읽기(정상: `ATOMIC_CLAIM_SQL` — `downloaded_at` 오름차순, `SKIP LOCKED`라 두 작업자가 같은 행을 가져가지 못함 / `--dry-run`: `DRY_RUN_SELECT_SQL` — 잠금 없이 읽기만, 그래서 두 미리 보기는 같은 행을 본다 / `--row-ids`: `ROW_IDS_FETCH_SQL`) → 배치마다 `build_graph(...)` → 행마다 세마포어 + `asyncio.wait_for` + 넓은 except → `_aggregate`. `--dry-run`도 LLM은 실제로 부른다(토큰 비용이 든다, 그림 행도 그림을 보낸다). 쓰지 않을 뿐이다.
- **의존성.** 노드가 쓰는 것(LLM 클라이언트, 종목표, DB, `dry_run`, `taxonomy_version`)은 `build_graph`가 `functools.partial`로 묶어 넘긴다. 모듈 전역 상태나 import 시점의 설정 읽기를 만들지 않는다. 설정은 명령이 시작할 때 `cli`가 읽어 `run_batch`에 인자로 넘긴다(`lock_ttl_minutes`, `per_row_deadline_s`). 예외는 `extract_pdf`로, `STORAGE_BASE_DIR`를 행마다 그때 읽는다. 발행처 사전은 `llm_schemas`가 import될 때 정식 이름을 한 번 읽어 스키마 enum과 검증에 쓴다(사전을 고치면 명령을 다시 띄워야 반영된다).
- **상태.** `state.RowState`(`total=False`). 노드는 자기 키만 담은 dict를 돌려준다. `RowState`에 없는 키는 LangGraph가 오류 없이 버린다(입력 행의 열이든 노드 반환값이든) — 다음 노드·`write`·보고가 그 값을 못 본다. 새 키나 가져오는 열(`RETURNING`·`SELECT` 목록)을 늘리면 `RowState`에 먼저 선언한다. 그림·발행처 키: `page_images`(`extract_pdf`, 글자가 없을 때만; `llm_extract`가 다 쓰고 비움), `page_image`·`page_image_unsupported`(`llm_extract`, 그림을 그린 행만), `publisher_final`·`publisher_type_final`·`publisher_suspect`(`llm_extract`, AI 결과가 있는 행만).
- **DB.** SQL은 모두 `sql.py` 상수이고 `sb.fetch(sql, args)`/`sb.execute(sql, args)`에 위치 인자(`$n`)로 넘긴다. 여러 문장을 한 트랜잭션에서 돌릴 때(`requeue`)는 `async with sb.transaction() as tx:` 안에서 `tx.fetch`/`tx.execute`를 쓴다. Supabase REST(supabase-py)는 쓰지 않는다 — PostgREST로는 `FOR UPDATE SKIP LOCKED`도 맨 SQL도 트랜잭션도 쓸 수 없다.
- **AI.** `core.llm.LLMClient`가 공급자 고르기·키·그림 붙이기·codex 실행·LangSmith 감싸기를 맡고, 분류기는 `parse()` 한 번(그림 행은 `images=`)과 `TRANSIENT_ERRORS`·`supports_images`·`ImageInputUnsupported`만 쓴다. 클라이언트는 `cli._make_llm_client` 한 곳에서 만든다.
- **PDF.** `extract_pdf`는 `_sync_read`를 `asyncio.to_thread`로 부른다(이벤트 루프를 막지 않게): `core.pdf.page_texts(path, max_pages=3)`, 글자가 없을 때만 `core.pdf.render_page_png`. 열 수 없는 파일은 `[]`, 못 읽는 쪽은 `""`, 못 그리는 쪽은 그림 없음이다. 두 함수 모두 `core.pdf`의 프로세스 전체 잠금 안에서 돈다.
- **설정.** `tagger/settings.py`의 함수가 부를 때마다 환경 변수를 읽는다(`core.settings.get_int`/`get_float`/`model_name`). 형식이 틀린 숫자는 변수 이름을 담은 `ValueError`다. 새 설정도 함수 하나로 두고 명령이 시작할 때 읽는다.
- **더할 때.**
  - `tag` 하위 명령: `register`에 parser를 더하고 `set_defaults(func=…)`로 종료 코드를 돌려주는 함수를 건다. 그 함수는 먼저 `settings.load_env()`를 부르고, DB를 열기 전에 필요한 설정만 확인해 4로 끝낸다(`NotReadyToRun`이나 그 명령 전용 예외). LangGraph·PyMuPDF가 필요한 모듈은 함수 안에서 import한다. 명령 목록 테스트(`research_desk/tests/test_cli.py`의 `COMMANDS`)에도 더한다.
  - 발행처: `vocabulary/publishers.yaml`만 고친다(새 발행처 더하기, 별칭·표기 더하기, 이름 바꾸기, 구역 옮기기). 사전 규칙을 지키고, 관찰된 표기를 새로 넣었으면 `tests/test_vocabulary.py`의 `OBSERVED_FILENAME_TAGS`에도 더한다. 백필이 도는 동안에는 고치지 않는다(LLM 요청이 바뀐다). 이미 분류된 행에는 저절로 반영되지 않으니, 고친 뒤 `tag requeue`(미리 보기 → `--apply`)로 대기 줄에 되돌리고 평소 백필로 다시 분류한다 — 이름을 바꾸거나 뺐으면 `--publisher-not-in-dictionary`, 구역을 옮겼으면 `--publisher-type-mismatch`, 표기를 더했으면 `--publisher-filename-mismatch`. `verified` 행은 되돌리지 않으므로 옛 이름이 남는다.
  - 리포트 종류·제외 사유·발행처 종류 값: `domain/vocabulary.yaml` + CHECK 제약 마이그레이션 + `llm_schemas.py` Literal + (제외 사유면) `state.py` Literal·`mark_oos_reason`·`status_oos`·`orchestrator`의 `oos` 키 + 프롬프트의 정의문. 발행처 종류면 `publishers.yaml`의 구역 이름도.
  - 검토 사유: `decide_status`(또는 상태 노드)에 메모 형식대로, `_aggregate`의 인식 목록, 패리티 사례. 검토 사유가 아닌 표시(그림·의심처럼)는 `apply_final_rules`에 두고 `review_reasons`에 넣지 않는다.
  - 종목표 교체는 이 칸이 아니라 `stocks set-version`이다. 옛 종목표에 없어 `krx_unmatched_in_scope`로 쌓인 행은 저절로 풀리지 않으니 `tag requeue --krx-unmatched`(또는 `escalate --since`, 검토의 재분류)로 다시 분류한다.
- **무해한 경고(디버깅하지 않는다).** `LLMExtraction` 직렬화 때의 Pydantic serializer 경고, LangGraph를 불러올 때의 `LangChainPendingDeprecationWarning`. Windows 비정상 종료 코드 `-1073741569`(드물게 `-1073741784`)는 반복 실행 스크립트가 그 작업자의 행을 되돌리고 다시 시도한다(데이터 손실 없음). 2026-10-09 백필에서 자주 났고, PDF를 두 스레드에서 동시에 읽은 것이 원인으로 추정돼 PDF 접근을 프로세스 안에서 줄 세웠다(`core.pdf` 잠금, 2026-10-10). 잠금 뒤 재측정 결과: 아직 없음(운영 적용 백필의 횟수를 여기 적는다).

## 테스트

- **확인할 것.** 분기 하나하나(상태를 바꾸지 않는 것 포함), 상태·신뢰도·메모 글자와 마지막 규칙(그림·의심, 메모 순서, `low` 유지), 쓰기 세 모양과 인자 19개 순서, 그림 경로(글자가 조금이라도 있으면 그림 없음, 그림 실패·암호·0쪽이면 AI 호출 없음, 그림을 못 받는 모델, 너무 큰 그림 줄여 그리기), 발행처 확인(정식 이름이 아닌 답은 오류 없이 NULL, 종류는 사전에서, 의심 표시의 모든 경우), 명령마다 시작 확인(stderr 문구, DB 호출 0, 종료 코드 4), 모드별로 되돌리는 것과 안 되돌리는 것, 보고 키(새 세 키, dry-run `rows`, 빈 보고 그대로), `tag requeue`(조건별 선택과 제외, 미리 보기는 쓰지 않음, 백업이 쓰기보다 먼저, 0건 적용, 재확인에서 빠지는 행, 건수 불일치 취소, 되돌리는 모양 = `pending_reset_shape()`, 실행 확인), 지연 import.
- **픽스처(`tests/conftest.py`).**
  - `tagger_env`: `TAGGER_ENV_VARS`(명령과 AI 호출이 읽는 변수 전부, 그리고 없앤 `HEARTBEAT_*`)를 지운다. 새 변수를 읽게 하면 이 목록에 넣는다. `.env` 읽기는 `research_desk/conftest.py`가 모든 테스트에서 끄고, `.env` 동작은 `env_file`로만 시험한다.
  - `krx`: 저장소에 들어 있는 실제 종목표(`BUNDLED_CSV`)를 세션에 한 번 읽는다. `resolve_krx`·`oos_gate`·패리티 기대값(예: `005930` → 삼성전자, `반도체`/`메모리반도체`)이 이 데이터에 기댄다 — 종목표를 바꾸면 이 테스트들도 다시 본다.
  - `mock_llm_client`(`set_response`/`set_refusal`/`set_exception`), `make_llm_extraction(**바꿀 값)`(발행처 기본값 `키움증권`), `mock_supabase`(`execute`를 기록하고, `fetch`는 `queue_fetch`로 넣어 둔 결과를 순서대로 돌려준다 — 오래된 잠금 되돌리기는 `execute`라 결과를 넣지 않는다).
  - `test_cli.py`의 `backend`: `SupabaseSQL.from_env`를 가짜 DB로, `core.db.create_pool`·`asyncpg.create_pool`을 실패로, `cli.run_batch`를 기록용으로, `llm.LLMClient`를 `RecordingClient`로 바꾼다. 시작 확인 테스트는 stderr 문구와 `backend.calls == []`를 함께 본다. `env`는 DB 주소·두 키·종목표 절대 경로를, `no_codex`는 PATH의 codex를 없앤다.
  - `test_requeue.py`: `table`(트랜잭션까지 흉내 내는 메모리 속 `FakeReports`), `dictionary_file`(작은 임시 사전), `processes`(`requeue.list_processes` 대신), `pages`(`requeue.first_page_renders` 대신), `backups`(`requeue.BACKUP_DIR`를 임시 폴더로). DB·PowerShell·실제 백업 폴더에 닿지 않는다. 프로세스 목록과 1쪽 확인 함수는 자기 테스트에서 `subprocess.run`을 바꾸거나 임시 PDF로 따로 시험한다.
- **테스트 PDF.** PyMuPDF로 `tmp_path`에 만들고 `fontname="korea"`를 쓴다 — 기본 글꼴(Helvetica)은 한글을 넣지 못해 글자가 비고 그림 경로로 빠진다. 그림 경로를 시험할 때는 일부러 빈 쪽이나 그림만 있는 쪽을 만든다. 읽을 곳은 `monkeypatch.setenv("STORAGE_BASE_DIR", ...)`로 `tmp_path`에 맞춘다.
- **패리티(`tests/parity/fixtures.json` + `test_parity.py`).** 손으로 고른 사례 19개마다 PDF 글자·LLM 가짜 응답 → 그래프 전체 → 기록된 UPDATE 인자를, `expected`에 적힌 열만 정확히 비교한다. 리포트 종류 6가지, 제외 사유 5가지, 이름 불일치(처음 12개), 발행처 의심(표기 불일치·별칭 답·표기 없음·IR자료+증권사 표기), 그림만 있는 PDF(단일종목·분석 대상 외·미매칭+의심)를 덮는다. 사례 표는 `tests/parity/README.md`. 분류 규칙이 그대로인지 보는 그물이다: 규칙을 일부러 바꿀 때만 기대값을 고치고, 테스트를 통과시키려고 고치지 않는다. 처음 12개 사례의 기대값은 그림·의심 규칙을 더할 때도 바꾸지 않았다.
- **golden PDF(`tests/golden/`).** 커밋된 PDF는 `_synthesize.py`(`python -m research_desk.tagger.tests.golden._synthesize`, 덮어씀)로 만든 경계 사례 입력이고, 지금은 어느 테스트도 읽지 않는다. 그 README의 기대 결과 표는 옛 규칙(`unknown_publisher`·`unknown_product` 사유, `publisher_type=company`, `IR자료`를 분석 대상으로 봄)이라 지금 규칙과 다르다 — 그 표에 맞춰 코드를 고치지 않는다. 새 테스트에 쓸 때 기대값은 위 규칙에서 다시 정한다. 따로, `test_extract_pdf.py`의 자동 픽스처가 쪽 넘김 시험용 `single_page_with_meta.pdf`·`page1_blank_meta_on_p2.pdf`·`no_meta_anywhere.pdf`를 없을 때 이 폴더에 만든다. git이 무시하는 파일이니 커밋하지 않는다(소스 폴더에 쓰는 유일한 테스트다).
- **사전 무결성(`tests/test_vocabulary.py`).** 위 "발행처"의 사전 규칙과, 표기 읽기의 경계(날짜 8자리, 숫자 뒤에 붙은 `_1`, 대소문자, `.PDF`), 시스템 프롬프트에 같은 파일이 들어가는지를 본다. 채널에 새 표기가 생기는 것은 테스트 실패가 아니다 — 사전에 넣을 때 `OBSERVED_FILENAME_TAGS`에도 더한다.
- **함정.**
  - `LLMExtraction`(그리고 `make_llm_extraction`)은 모르는 키를 조용히 버린다. 옛 이름(`publisher_raw`, `publisher_type`, `topics`, `sectors_major`)을 넘기면 아무것도 바뀌지 않은 채 테스트가 통과한다 — 필드 이름은 `llm_schemas.py`에서 확인한다. `publisher_canon`에 정식 이름이 아닌 값을 넘기면 오류 없이 None이 된다.
  - 초기 상태에 `RowState`에 없는 키를 넣어도 버려진다.
  - 행 시간 한도 테스트는 `per_row_deadline_s`를 인자로 짧게 준다(환경 변수나 모듈 상태를 쓰지 않는다).
  - 입구 쪽 지연 import 검사는 `research_desk/tests/test_cli.py`에 있다(새 프로세스로 `python -m research_desk`를 띄워 불러온 모듈 목록을 본다).
  - DB·AI·codex·PowerShell에 실제로 닿는 테스트를 만들지 않는다. `run --dry-run`도 실제 AI를 부른다.
