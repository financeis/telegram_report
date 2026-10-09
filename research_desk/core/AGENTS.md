# research_desk/core/ — 공용 설비: 설정, DB 연결, AI 호출·임베딩, KIS Open API, MongoDB 읽기, PDF 파일

## 맡는 일

- `settings.py`: 설정을 읽는 유일한 방법. `.env` 읽기(`load_env`), 값 읽기 도우미(`required`·`optional`·`get_int`·`get_float`·`get_bool`·`model_name`), 여러 칸이 함께 쓰는 값(Supabase URL·서비스 키·DB URL, 두 공급자의 API 키, `STORAGE_BASE_DIR`, `KRX_CSV_PATH`), 설정 누락 오류 `MissingSetting`, 기능 준비 실패 오류 `NotReady`.
- `db.py`: Supabase REST 클라이언트(서비스 키), Postgres 직접 연결 풀(asyncpg), 그 위의 얇은 `SupabaseSQL`(fetch/execute, 행은 dict)과 연결 하나로 여러 문장을 묶는 트랜잭션 도우미 `SupabaseSQL.transaction()`.
- `llm.py`: 모델 이름으로 공급자(Anthropic API, OpenAI API, 로컬 Codex CLI)를 골라 구조화 출력을 받는 `LLMClient`(`parse` — 사용자 메시지에 그림 PNG를 붙이는 `images` 인자 포함)와 OpenAI 임베딩(`embed`), 그림을 받는 모델인지 이름으로 판정하는 `supports_images`와 그 오류 `ImageInputUnsupported`, 키 확인 `require_key`, 일시 오류 묶음 `TRANSIENT_ERRORS`, 한 번 재시도 `call_with_retry`.
- `kis.py`: 한국투자증권 Open API 클라이언트 `KisClient`(접근 토큰, 수정주가 일봉 `daily_prices`, 현재가 `quote`, 호출 간격·재시도·키 가리기), 오류 `KisError`.
- `mongo.py`: MongoDB 읽기 핸들 `mongo_collection`과 접속 확인 `ping`.
- `pdf.py`: 저장 폴더 안의 PDF인지 확인(`resolve_in_storage`), 쪽별 글자, 쪽 수, 쪽 그림(PNG). PyMuPDF를 쓰는 모든 호출은 프로세스 전체 잠금 하나(`_PYMUPDF_LOCK`)로 줄 세운다. import할 때 MuPDF가 오류·경고를 stdout에 직접 찍지 못하게 끈다(`pymupdf.TOOLS.mupdf_display_errors(False)`·`mupdf_display_warnings(False)`) — 명령의 stdout은 JSON 하나여야 하는데, 어떤 PDF는 `MuPDF error: format error: …`를 그 앞에 끼워 넣었다. 끄지 않는다. 오류는 지금처럼 예외나 빈 결과로 드러난다.
- 외부 도구 `supabase`·`asyncpg`·`openai`·`anthropic`·`dotenv`·`fitz`/`pymupdf`·`pymongo`/`bson`·`httpx`는 저장소 전체에서 이 칸만 import한다. DB 연결·AI 호출·외부 API·PDF 열기·`.env` 읽기를 한곳에 모아 두어, 나중에 서버를 옮기거나 공급자를 바꿀 때 고칠 범위가 이 칸으로 줄어든다.

## 맡지 않는 일

- 업무 개념. 리포트·종목·분류 상태·분석 대상 규칙·종목표 버전은 `research_desk.domain`이다. core는 이런 이름을 모르게 둔다.
- research_desk의 다른 칸 import. core는 core만 import한다(어기면 `R1 core`).
- 표와 SQL. core는 어느 표의 주인도 아니다. `reports`·`failed_attempts`·`report_summaries`·주가 표·유사 기업 표를 `.table(…)`나 문자열 SQL로 다루면 `R11 표 주인`으로 잡힌다. SQL은 표의 주인 칸(`collector`, `tagger`, `features/review`, `features/reports`, `features/analysis`, `features/prices`, `features/peers`)에 둔다. docstring에 표 이름을 쓰는 것은 괜찮다.
- KIS 응답을 주가 스냅샷으로 바꾸는 계산(수익률·초과수익률·실행 상태)은 `features/prices`, 사업보고서 문서를 고르는 규칙(회계연도·대상 회사)은 `features/peers`의 몫이다. core는 받은 값을 그대로 넘긴다.
- 웹. FastAPI·starlette를 import하지 않고 HTTP 응답이나 상태 코드를 만들지 않는다. 오류는 평범한 예외로 던지고, 404·503으로 바꾸는 일은 기능(`features/*`)과 `web`이 한다.
- 호출하는 쪽마다 다른 정책.
  - 모델 기본값은 `tagger/settings.py`·`features/analysis/settings.py`·`features/peers/settings.py`, 프롬프트와 응답 스키마는 각 칸에 있다.
  - 재시도와 시간 한도는 호출자가 정한다. 분류기는 SDK 재시도 2·요청 60초에 실패한 행을 `pending`으로 되돌리고, 분석·비교는 SDK 기본 재시도에 `call_with_retry`와 호출마다 `PHASE2_PER_REPORT_TIMEOUT_S`를 쓴다. 유사도 계산은 `call_with_retry`에 회사마다 `PEERS_PER_COMPANY_TIMEOUT_S`, 임베딩 호출마다 60초, 테마 검색의 질의 임베딩은 15초다.
  - 동시 호출 수도 호출자 몫이다. 분류기는 `MAX_CONCURRENT_LLM`(운영 천장 2), 웹 서버의 분석·비교는 analysis의 `ai_slot`(2), 테마 검색의 질의 임베딩은 peers의 자체 자리(2), 유사도 계산은 `PEERS_MAX_CONCURRENT_LLM`(운영 천장 2)이 막는다.
  - KIS의 초당 호출 수는 호출자가 `max_calls_per_sec`로 넘긴다(`PRICES_MAX_CALLS_PER_SEC`).
  - core에 공통 제한이나 기본 정책을 새로 넣지 않는다. 넣으면 호출자마다 지켜 온 방식이 한꺼번에 바뀐다. 예외는 하나, 일부러 둔 PDF 잠금이다(아래 PDF). 호출자의 정책이 아니라 PyMuPDF가 여러 스레드 동시 사용을 지원하지 않는다는 도구의 제약이라 core에 둔다. AI 호출은 줄 세우지 않는다.
- 칸 전용 설정 값. 각 칸의 `settings.py`가 core 도우미로 읽는다.
- 준비 판정. 무엇이 없으면 어느 기능이 멈추는지는 각 기능과 명령이 정한다. core는 `NotReady`를 정의할 뿐 스스로 던지지 않는다.
- `telethon`(collector), `langgraph`(tagger).

## 늘 지켜야 할 것

설정.
- import 시점에는 아무 설정도 읽지 않는다. 모듈 최상위·클래스 본문·데코레이터·기본 인자에서 `os.environ`이나 설정 읽기 함수를 부르면 `test_core_modules_read_no_settings_at_import`가 실패한다. 테스트가 import 뒤에 환경을 바꾸는 것도, `.env`에 나중에 넣은 키가 다음 시도에 반영되는 것도 이 규칙에 기댄다.
- `load_env()`는 부를 때마다 `.env`를 다시 읽고, 이미 있는 환경 변수는 덮지 않는다(`load_dotenv(override=False)`). 그래서 `.env`에 새로 추가한 키는 재시작 없이 다음 시도에 반영되고, 이미 있던 값을 바꾼 것은 재시작해야 반영된다. 의도한 동작이다. 분석 기능의 안내 문구(`<변수>가 설정되지 않았습니다. .env에 추가 후 분석 다시 시도하세요.`)가 이것에 기대고, 테스트가 넣은 값이 이기는 것도 이 덕분이다. `override=True`로 바꾸지 않는다.
- 읽는 파일은 인자로 준 경로 → `ENV_FILE_PATH` → `research_desk/core/`에서 위로 올라가며 가장 가까운 `.env` 순서다. 현재 폴더 기준이 아니다. 그래서 저장소 폴더 안에 만든 별도 작업 폴더에서 명령을 돌리면, 그 폴더에 `.env`가 없는 한 바깥 저장소의 실제 `.env`가 읽힌다.
- `ENV_FILE_ENABLED`가 거짓이면 아무것도 읽지 않고 None을 돌려준다. 이 스위치 확인은 `load_env()` 안에 그대로 둔다. 테스트의 `.env` 차단이 이것에 기댄다.
- 도우미의 값 처리(테스트로 고정):
  - `required`: 없거나 빈 값이면 `MissingSetting`.
  - `optional`: 기본값은 변수가 없을 때만 쓴다. 빈 값은 빈 값 그대로 돌려준다.
  - `get_int`·`get_float`: 없으면 기본값, 그 밖에 숫자가 아니면(빈 값 포함) 변수 이름이 든 `ValueError`.
  - `get_bool`: 대소문자를 무시한 `true`만 참이다. `1`, `yes`, ` true`는 거짓.
  - `model_name(role, default)`: `LLM_MODEL_<ROLE>` → 옛 이름 `OPENAI_MODEL_<ROLE>` → 기본값. 빈 값은 없는 것으로 본다.
  - 비밀 값(Supabase URL·키·DB URL, API 키)은 빈 값이면 None.
  - `STORAGE_BASE_DIR` 기본값은 `./reports`, `KRX_CSV_PATH` 기본값은 저장소 안의 종목표 CSV(`DEFAULT_KRX_CSV_PATH`)다. 상대 경로는 현재 폴더 기준이다.
- 변수 이름은 바꾸지 않는다(사용자 `.env`는 고치지 않는다). 이름을 새로 정해야 하면 `model_name`처럼 읽는 쪽에서 옛 이름도 받아 준다.

오류와 문구. 다른 칸·화면·스크립트가 문자 그대로 쓰므로 바꾸지 않는다.
- `MissingSetting`: `RuntimeError`의 하위 클래스, `str()`은 `<NAME> is required`, `.name`은 변수 이름. `tag`는 이 문구를 stderr에 찍고 4로, `collect`는 `.name`으로 `Config error: Missing required env var: <NAME>`을 찍고 1로 끝난다.
- `NotReady(area, reason)`: `str()`은 `<area> 기능을 지금 쓸 수 없습니다: <reason>`이고, 웹이 이 문자열을 503 응답의 `detail`로 그대로 보낸다. `area`는 기능의 화면 이름(`기업 목록`·`리포트`·`분석`·`비교`·`커버리지`·`검토`·`주가`·`유사 기업`·`자료 기준일`), `reason`은 한국어 고정 문장이다. 변수 이름은 넣어도 되지만 파일 경로·키 값·traceback은 넣지 않는다(브라우저까지 간다).
- `NotReady`를 `RuntimeError`의 하위로 만들지 않는다. 호출 주변의 `except RuntimeError`(분석이 `require_key` 실패를 잡는 자리 같은 곳)가 준비 실패를 삼켜 일반 오류로 바꿔 버린다.
- 두 예외 모두 pickle을 거쳐도 같은 문구가 나와야 한다(테스트). 생성자를 바꾸면 `super().__init__`에 생성자 인자를 그대로 넘긴다.
- `require_key(model)`의 실패는 `RuntimeError`이고, 문구는 codex 모델이면 `codex CLI not found for model <model>`(`tag`가 stderr에 그대로 찍는다), 그 밖에는 `<ENV> is required for model <model>`이다. 호출하는 쪽이 `RuntimeError`로 잡으므로 예외 종류를 바꾸지 않는다.
- `PDFNotFound.message`: 저장 폴더 밖이거나 PDF가 아니면 `PDF를 찾을 수 없습니다.`, 파일이 없으면 `로컬 PDF 파일이 없습니다. 수집 상태를 확인해 주세요.` 기능이 이 문구를 404 응답의 `detail`로 그대로 보낸다.

DB.
- `create_pool`의 `statement_cache_size=0`을 빼지 않는다. `SUPABASE_DB_URL`이 Supabase 트랜잭션 풀러(6543 포트, pgbouncer 트랜잭션 모드)를 가리키면 이름 붙은 prepared statement가 연결 사이에 남지 않아 쿼리가 깨진다. 5432 직접 연결에서는 꺼도 해가 없다. `min_size=1`이고 `max_size`는 키워드 필수 인자다(부르는 쪽이 정한다).
- URL이 없으면 연결을 시도하기 전에 `MissingSetting("SUPABASE_DB_URL")`을 던진다.
- `SupabaseSQL.fetch`는 `asyncpg.Record`가 아닌 평범한 dict 목록을 돌려주고, 인자는 `$1…$n` 순서대로 넘긴다. `fetch`·`execute`는 부를 때마다 풀에서 연결을 따로 빌린다. 그래서 둘을 이어 불러도 한 트랜잭션이 아니다.
- 여러 문장을 한 트랜잭션에서 돌리려면 `async with sb.transaction() as tx:`를 쓴다. 블록 동안 연결 하나를 빌려 트랜잭션을 열고, `tx.fetch`/`tx.execute`(`SQLTransaction`, 모양은 `SupabaseSQL`과 같다)가 모두 그 연결에서 돈다. 블록이 정상으로 끝나면 커밋, 예외든 취소(Ctrl+C 포함)든 블록을 빠져나가면 먼저 되돌린 뒤 그 예외를 다시 올린다. 블록 안에서 한 일은 하나도 남지 않는다. "행 잠그기 → 다시 확인 → 고치기 → 바뀐 행 수 확인 → 안 맞으면 취소"(`tag requeue --apply`)가 이것에 기댄다.
- `supabase_client`는 서비스 키(행 수준 보안을 건너뛰는 마스터 키)로 연결한다. 키와 클라이언트가 브라우저 응답이나 로그로 나가지 않게 한다.

AI 호출.
- 공급자는 모델 이름이 정한다. `claude-*` → Anthropic(`ANTHROPIC_API_KEY`), `codex:<모델>` → 로컬 `codex exec`(ChatGPT 로그인, 키 없음), 그 밖 → OpenAI(`OPENAI_API_KEY`). 모델 교체나 되돌리기가 `.env`의 `LLM_MODEL_*` 수정만으로 끝나야 하므로, 공급자 분기를 다른 칸에 따로 만들지 않는다.
- 그림 입력(`parse(..., images=[PNG 바이트, …])`).
  - 그림은 사용자 메시지에만 붙는다. 시스템 프롬프트는 그림이 있든 없든 같아서 캐시가 유지된다. `images`가 None이거나 비면 예전 글자 요청과 한 글자도 다르지 않다.
  - Anthropic(형식 강제·`constrained=False` 두 경로 모두): 사용자 내용이 그림 블록(`image`, base64 `image/png`)들 다음 글자 블록 순서다. Claude는 그림이 글보다 앞에 올 때 더 잘 읽는다.
  - OpenAI: 글자 다음에 그림마다 `image_url`(`data:image/png;base64,…`).
  - Codex CLI: 그림마다 임시 작업 폴더에 `page-<n>.png`로 쓰고 `-i <파일>`을 하나씩 붙인다(codex-cli의 `-i/--image`). 그 옵션이 값을 여러 개 받으므로 뒤에 다른 옵션이 와야 마지막 `-`가 프롬프트(stdin)로 남는다 — 순서를 바꾸지 않는다.
  - `supports_images(model)`은 모델 이름만 보고 정한다(키·클라이언트·프로세스·네트워크 없음). Claude 모델은 모두 받는다. OpenAI·codex는 글자 전용 이름 목록(`gpt-4`, `gpt-3.5…`, `gpt-4-0…`, `gpt-4-32k…`, `o1-mini…`, `o1-preview…`, `o3-mini…` 등)만 못 받는다. 못 받는 모델에 그림을 주면 요청을 보내기 전에 `ImageInputUnsupported`다.
  - `ImageInputUnsupported`는 `RuntimeError`의 하위가 아니다. `require_key` 실패와 `CodexExecError`를 잡는 `except RuntimeError`가 삼키지 않게 하려는 것이다. 바꾸지 않는다. 호출자는 먼저 `supports_images`를 묻는다.
- Anthropic.
  - `temperature`를 보내지 않는다. Claude 모델은 기본값이 아닌 샘플링 값을 거절한다.
  - 시스템 프롬프트는 `cache_control: ephemeral`을 단 블록 하나다. 행마다 같은 문장이라 반복 호출이 캐시로 싸게 읽힌다.
  - `max_tokens`는 16000이다. 적응형 사고가 `max_tokens`에 포함되므로 JSON 뒤에 여유가 필요하다.
  - 사고 깊이는 `ANTHROPIC_EFFORT`(기본 `medium`).
  - 응답 앞에 사고 블록이 올 수 있으므로 `type == "text"`인 블록만 이어 읽는다.
  - 거절(`stop_reason == "refusal"`)은 예외가 아니라 `parsed=None`과 거절 사유로 돌려준다. 잘린 JSON은 pydantic `ValidationError`가 난다.
- `constrained=False`는 Claude에서만 쓰이고 OpenAI·Codex 경로는 무시한다. 스키마를 디코딩 문법으로 강제하지 않고, JSON Schema를 시스템 프롬프트 뒤 `<output_format>`에 붙여 JSON 텍스트로 받은 뒤 pydantic으로 검증한다.
  - 스키마가 커서 API가 "compiled grammar is too large"로 거절하는 재무 추출(`ExtractionResult`) 때문에 있다.
  - 스키마는 호출자 시스템 문장 뒤에 붙인다. 그래야 프롬프트가 호출마다 같아져 캐시가 유지된다.
  - 코드 펜스나 군말은 가장 바깥 `{…}`만 잘라서 견딘다.
  - 엄격하지 않은 tool 입력 방식은 시험했다가 버렸다(중첩 객체를 JSON 문자열로 돌려줬다). 되살리지 않는다.
- OpenAI는 `chat.completions.parse(response_format=<스키마>)`로 부른다. `temperature`는 값을 줬을 때만 보낸다(luna 계열은 0을 거절해서 분류기가 None을 넘긴다).
- Codex CLI.
  - 실행 방식을 유지한다. 사용자 설정을 무시하고(`--ignore-user-config --ignore-rules`: 사용자의 MCP 서버·훅·알림을 끈다), 빈 임시 폴더가 작업 공간 전부이고, 읽기 전용(`-s read-only`)에 `--skip-git-repo-check --ephemeral`, 출력 스키마는 `--output-schema`(OpenAI 경로와 같은 엄격 스키마), 마지막 메시지는 `-o` 파일로 받는다.
  - CLI에 시스템 역할이 없어서 시스템 문장과 사용자 문장을 빈 줄로 이어 stdin으로 넣는다.
  - 생각 깊이 `CODEX_REASONING_EFFORT`의 기본값은 `high`다. 재무 추출에서 `high`가 OpenAI API와 같은 결과를 냈고 `medium`은 지표를 눈에 띄게 적게 뽑았다.
  - 종료 코드가 0이 아니거나 마지막 메시지가 비면 `CodexExecError`이고, 재시도하지 않는다. 토큰 수는 마지막 `turn.completed` 줄에서 읽는다.
  - 프로세스는 asyncio 서브프로세스가 아니라 작업 스레드의 `Popen`으로 돌린다. Windows의 selector 이벤트 루프가 asyncio 서브프로세스를 지원하지 않는다.
  - 시간 초과나 취소로 끊기면 프로세스 트리 전체를 죽인다(`taskkill /F /T /PID`). Windows의 `codex`는 npm 껍데기(cmd → node → codex.exe)라 껍데기만 죽이면 실제 프로세스가 고아로 남아 계속 돈다.
- LangSmith 감싸기는 `LANGSMITH_TRACING=true`와 `LANGSMITH_API_KEY`가 둘 다 있을 때만 하고, 패키지가 없으면 조용히 건너뛴다. 켜져 있으면 붙인 그림도 요청과 함께 기록된다.
- `TRANSIENT_ERRORS`(두 공급자의 429·5xx·시간 초과·연결 오류)는 서로 다른 두 정책이 같이 쓴다. 분류기는 이것(과 응답 형식 오류)을 "행을 `pending`으로 되돌릴 오류"로 보고, `call_with_retry`는 이것을 한 번 다시 시도한다. 여기에 넣거나 빼면 두 쪽이 동시에 바뀐다.
- `call_with_retry(call, backoff_s=5.0)`: 일시 오류(`TRANSIENT_ERRORS`, `TransientLLMError`, `asyncio.TimeoutError`)면 `backoff_s`를 기다린 뒤 정확히 한 번 더 부르고, 또 일시 오류면 그 오류를 원인으로 단 `TransientLLMError`를 던진다. 그 밖의 오류(`ValidationError`, `CodexExecError`, 거절로 만든 `RuntimeError`)는 기다리지 않고 바로 올린다. `call`은 시도마다 새 awaitable을 만들어야 한다(`lambda: asyncio.wait_for(client.parse(...), t)`). 코루틴 하나는 두 번 await할 수 없다.

임베딩(`LLMClient.embed(model=, texts=, dimensions=None)`).
- OpenAI 모델만 받는다. `claude-*`·`codex:` 모델이면 `ValueError`, 문자열 하나를 넘기면 `TypeError`, 빈 목록이면 호출 없이 빈 결과.
- 결과 벡터는 응답의 `index` 순서로 맞춰 입력 순서와 같게 준다. 개수나 번호가 입력과 맞지 않으면 `RuntimeError`.
- `dimensions`는 줄 때만 보낸다(유사 기업 기능은 늘 1536). 벡터를 길이 1로 맞추는 일과 한 요청의 개수 한도를 지키는 일은 호출자 몫이다. 오류는 OpenAI SDK의 것이라 `TRANSIENT_ERRORS`·`call_with_retry`가 `parse`와 똑같이 적용된다. `OPENAI_API_KEY`가 없으면 `RuntimeError`(`OPENAI_API_KEY is required for model <모델>`)이므로, 웹에서 쓰는 쪽은 호출 전에 키를 확인해 자기 `NotReady`로 바꾼다.

KIS(`KisClient`).
- httpx는 메서드 안에서만 import한다. KIS를 실제로 부르는 일만 HTTP 라이브러리를 불러오게 하려는 것이다(모든 명령이 시작할 때 core를 불러온다).
- 접근 토큰은 클라이언트마다 한 번 받아(`ensure_token()` 또는 첫 호출) 클라이언트가 살아 있는 동안 쓴다. KIS는 토큰을 1분에 한 번쯤만 발급하고 같은 앱키를 다른 프로젝트도 쓴다. 그래서 실행마다 클라이언트를 하나만 만든다.
- 토큰 요청이 한 번 실패하면 그 클라이언트는 KIS에 다시 묻지 않고, 이후 모든 호출이 바로 `KisError`(첫 실패의 문장)다. 다른 스레드에서 기다리던 호출도 같다. 이 "실패 기억"을 없애면 실패한 실행이 종목마다 토큰을 요청해 KIS의 발급 제한을 계속 건드린다.
- 호출 시작 간격은 `1 / max_calls_per_sec` 이상이다(토큰 요청 포함, 여러 스레드가 함께 써도). HTTP 요청 하나의 시간 한도는 기본 10초(`timeout_s`)다.
- 재시도: HTTP 429나 KIS `EGW00201`(초당 거래건수 초과), 5xx, 시간 초과, 끊긴 연결만 1·2·4초 기다려 최대 3번 다시 묻는다. 그 밖의 응답은 바로 `KisError`(`code`·`status` 포함). `rt_cd`가 `0`이 아닌 데이터 응답도 `KisError`다.
- 비밀 값: 앱키·시크릿은 요청 머리글과 토큰 요청 본문에만, 토큰은 머리글에만 싣는다. `KisError` 문장과 로그에는 넣지 않고, 응답이 그 값을 되풀이하면 `***`로 가린다(`_mask`).
- 일봉(`daily_prices`): 수정주가(`FID_ORG_ADJ_PRC=0`), 한 번에 최대 100행·최신부터 오므로 기간이 길면 거꾸로 나눠 받아 합치고, 날짜 오름차순으로 돌려준다. 빈 숫자는 None.
- 현재가(`quote`): 시가총액은 `hts_avls`(억원)에 1억을 곱해 원으로, 거래정지는 `temp_stop_yn=Y` 또는 상태 코드 58, 관리종목은 `mang_issu_cls_code=Y` 또는 상태 코드 51.
- 기본 주소 `DEFAULT_BASE_URL`(실전 서비스)은 `features/prices/settings.py`에도 같은 값으로 적혀 있다(설정 읽기가 이 모듈을 불러오지 않게). 바꾸면 둘 다 바꾼다 — 테스트가 같은지 본다.

MongoDB.
- pymongo는 함수 안에서만 import한다(명령 없는 실행이 pymongo를 불러오지 않는다는 입구 테스트가 이것에 기댄다).
- `mongo_collection(url, db, name, timeout_ms=5000)`은 첫 조회 때 접속한다. 서버가 한도 안에 답하지 않으면 그 조회가 `ServerSelectionTimeoutError`. 핸들이 클라이언트를 가지므로 `handle.database.client.close()`로 닫는다.
- `ping(url, timeout_ms=3000)`은 접속 불가·로그인 거절·쓸 수 없는 주소를 오류가 아닌 False로 돌려준다. 주소에 비밀번호가 있을 수 있어 로그에는 오류 종류만 남긴다.

PDF.
- `resolve_in_storage(base, relative)`는 경로를 끝까지 풀어서(`..`, 절대 경로 포함) 저장 폴더 안이고 확장자가 `.pdf`(대소문자 무관)일 때만 통과시킨다. 밖이거나 PDF가 아니면 `PDF를 찾을 수 없습니다.`, 안이지만 파일이 아니면 `로컬 PDF 파일이 없습니다. …`이다. 저장 폴더 안을 가리키는 절대 경로는 받아들인다.
- 웹으로 PDF를 내보낼 때 이 확인을 건너뛰고 DB의 `file_path`를 바로 열지 않는다. 저장 폴더 밖의 `.env` 같은 파일이 새어 나가는 길이 된다.
- `page_texts`는 예외를 던지지 않는다. 열 수 없는 파일(없음·깨짐·빈 파일)은 `[]`, 읽지 못한 쪽은 그 쪽만 `""`이다. 분류기의 "PDF를 읽을 수 없음" 판정과 분석의 "읽을 글자 없음" 422가 이것에 기댄다.
- `page_count`·`render_page_png`는 파일 여는 오류를 그대로 올린다. `render_page_png`의 쪽 번호는 1부터, 기본 120dpi, 범위 밖이면 `PageNotFound`(`LookupError`의 하위)다.
- **PyMuPDF 잠금.** PyMuPDF는 여러 스레드에서 동시에 쓰는 것을 지원하지 않는데, 이 모듈은 여러 스레드에서 불린다(분류기는 두 행의 PDF를 작업 스레드에서, 웹앱은 검토 쪽 그림과 분석 본문 읽기를 스레드 풀에서). 그래서 `page_texts`·`page_count`·`render_page_png`는 문서를 열기 전부터 닫을 때까지 프로세스 전체 잠금 `_PYMUPDF_LOCK`(재진입 가능한 `threading.RLock`, 안에서 이 모듈의 다른 함수를 불러도 막히지 않는다)을 쥔다. 한 번에 한 호출만 PyMuPDF 안에 있고 나머지는 차례를 기다린다. 결과와 오류 동작은 잠금 전과 같다.
  - 일부러 둔 프로세스 전체 제한이다. 잠금은 프로세스마다 하나라 분류기와 웹앱(서로 다른 프로세스)끼리는 기다리지 않는다.
  - 대가: PDF 일이 한 줄로 선다(PDF 읽기는 AI 호출보다 훨씬 짧아 처리량은 거의 그대로다). PyMuPDF 호출 하나가 멈추면 그 프로세스의 뒤 PDF 호출이 모두 기다린다 — 분류기에서는 기다리는 시간도 행 시간 한도(`PER_ROW_DEADLINE_S`)에 들어가고, 웹앱에서는 검토 쪽 그림·분석이 서버를 다시 켤 때까지 기다린다.
  - PyMuPDF를 쓰는 새 함수도 이 잠금 안에서 열고 닫는다. 다른 칸에서 PyMuPDF를 직접 쓰지 않는다(`R9 외부 도구`도 막는다).

## 이 칸의 방식

- 설정 값 추가: 두 칸 이상이 읽는 값만 `settings.py`에 함수로 둔다. 한 칸만 쓰는 값은 그 칸의 `settings.py`에서 core 도우미로 읽는다. 어느 쪽이든 import 때가 아니라 부를 때 읽는 함수로 만든다. 부르는 쪽은 먼저 `load_env()`를 부른다(명령은 시작할 때, 웹 기능은 준비할 때마다, 분석은 모델 키를 확인할 때마다).
- 준비 실패 흐름: core는 재료만 준다. 명령은 `MissingSetting`을 받아 종료 코드로 바꾸고(`tag` 4, `collect` 1), 웹 기능은 준비에 실패하면 `NotReady(<기능 이름>, <고정 문장>)`을 던지고 `web`이 그것을 503으로 바꾼다. 원인(경로, 예외 내용)은 로컬 로그에만 남긴다.
- DB 연결 함수는 모듈 속성으로 부른다. `from research_desk.core import db` 다음 `db.supabase_client(...)`, `db.SupabaseSQL.from_env(...)`처럼 쓴다. 테스트 안전망이 `core.db.supabase_client`·`core.db.create_pool`·`SupabaseSQL.from_env`를 거절하는 가짜로 바꿔 두는데, 모듈 맨 위에서 `from research_desk.core.db import supabase_client`처럼 함수를 복사해 두면 안전망을 빠져나가 진짜 연결을 시도한다. `load_env`는 스위치 방식이라 이름으로 가져가도 된다.
- AI 기능 추가: 공급자별 차이는 `LLMClient` 안에서 모델 이름으로 가른다. 호출자마다 다른 값(`timeout`, `max_retries`, `effort`, `temperature`, `constrained`)은 인자로 받아, 각 호출자가 지금 방식을 그대로 유지하게 한다. SDK 클라이언트는 공급자마다 처음 쓸 때 하나 만들어 재사용하고, 쓰는 쪽이 `async with`나 `close()`로 닫는다.
- 새 외부 설비(DB 드라이버, AI SDK, PDF 라이브러리)는 이 칸에 두고, 구조 검사의 외부 도구 표(`research_desk/tests/architecture_rules.py`의 `EXTERNAL_TOOL_AREAS`)에 넣어 다른 칸에서 못 쓰게 한다.
- core는 모든 명령이 시작할 때 import된다(입구 → `tag` 등록 모듈 → `core.db`·`core.llm`). 무겁거나 없어도 되는 패키지(`langsmith.wrappers`, `pymongo`, KIS용 `httpx`)는 쓰는 함수 안에서 import한다.
- `core/__init__.py`는 아무것도 다시 내보내지 않는다. 모듈을 직접 import한다(`from research_desk.core import settings`).

## 테스트

- 네트워크·실제 DB·실제 `.env` 없이 돈다.
  - AI: `LLMClient`의 `_anthropic`·`_openai`에 MagicMock·AsyncMock을 꽂고 보낸 요청 모양을 확인한다. Codex는 `llm._run_codex`·`llm._codex_bin`을 바꾼다. 진짜 서브프로세스는 `sys.executable -c <스크립트>`로만 띄워 stdin 전달, 출력 디코딩, 취소 때의 트리 종료를 본다.
  - DB: `db.asyncpg.create_pool`을 AsyncMock으로 바꾸고, 연결과 레코드는 가짜(`FakePool`, `FakeConn`, dict가 아닌 Mapping인 `FakeRecord`)를 쓴다. 트랜잭션은 한 연결에서 돌고 커밋되는지, 블록이 예외·문장 실패·취소로 끝나면 되돌리고 예외를 올리는지, `fetch`/`execute`가 여전히 따로 연결을 빌리는지 본다.
  - PDF: `tmp_path`에 pymupdf로 만든다. 한글은 내장 `korea` 글꼴로 넣어야 글자 추출이 된다. 잠금은 가짜 `pymupdf`(`fake_pymupdf`)로 여러 스레드에서 동시에 불러, PyMuPDF 안에서 두 호출이 겹치지 않는지(실패하는 호출도 포함), 실패한 호출이 다음 호출을 막지 않는지 본다. MuPDF의 stdout 출력이 꺼져 있는지도 본다.
  - 그림 입력: Anthropic 두 경로는 그림이 글자 앞, OpenAI는 글자 뒤 `data:` 주소, codex는 `-i` 파일, 그림이 없으면 요청이 예전과 같음, 시스템 프롬프트가 그림 유무와 상관없이 같음, 글자 전용 모델은 요청 전에 `ImageInputUnsupported`.
  - KIS: httpx `MockTransport`가 KIS 서버 노릇을 하고, 기본 주소는 `.invalid`(절대 풀리지 않는 이름)라 요청이 PC 밖으로 나가지 못한다. 기다림은 가짜 시계·가짜 `sleep`으로 재서 실제로 자지 않는다. 토큰 1회, 나눠 받기, 재시도 횟수와 간격, 토큰 실패 기억, 오류 문장에 키 없음을 본다.
  - MongoDB: `pymongo.MongoClient`를 가짜로 바꾼다(서버 없음). 새 프로세스로 `core.mongo` import가 pymongo·bson을 불러오지 않는지도 본다.
  - 임베딩: OpenAI 클라이언트의 `embeddings.create`를 가짜로 바꿔 요청 모양(모델·차원·순서)과 순서 맞추기, 비 OpenAI 모델 거절을 본다.
- 설정 테스트는 `RD_CORE_*`처럼 다른 테스트와 겹치지 않는 변수 이름과 `monkeypatch.setenv`·`delenv`를 쓴다. `.env` 동작은 `env_file` fixture로만 시험한다.
- `test_env_file_fixture_loads_variables`와 바로 뒤의 `test_env_file_fixture_cleans_up_after_the_test`는 파일 순서에 기대는 짝이다. 뒤 테스트가 앞 테스트가 넣은 변수가 지워졌는지 본다. 떼어 놓거나 순서를 바꾸지 않는다.
- `settings.py`에 환경을 읽는 함수를 새로 만들면 `test_settings.py`의 `_ENV_READS`에 이름을 더한다. 빠지면 import 시점 읽기 검사가 그 함수를 보지 못한다.
- `call_with_retry` 테스트는 `llm.asyncio.sleep`을 바꿔 실제로 기다리지 않는다. LangSmith를 시험하지 않는 테스트에서 SDK 클라이언트를 만들 때는 `LANGSMITH_TRACING`·`LANGSMITH_API_KEY`를 지운다. 개발 PC에서 켜져 있으면 클라이언트가 감싸져 비교가 틀어진다.
- 남겨야 할 경계 경우: 빈 값·옛 변수 이름·pickle, 캐시 토큰 필드가 None인 응답, 사고 블록이 앞선 응답, 코드 펜스 안의 JSON, 거절(사유 있음·없음), 잘린 JSON, codex 실패와 빈 출력, Windows와 그 밖 플랫폼의 트리 종료 분기(`llm.sys.platform`을 바꿔서), 저장 폴더 밖(`../`, 바깥 절대 경로, `.env`)·이름만 `.pdf`인 폴더·대문자 `.PDF`.
- 비동기 테스트는 표시 없이 `async def`로 쓴다(`asyncio_mode = auto`).
