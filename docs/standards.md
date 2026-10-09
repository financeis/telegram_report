# 규칙

## 검증 관문

- **커밋과 병합 커밋마다 전체 테스트가 돈다.** `.githooks/pre-commit`과 `.githooks/pre-merge-commit`이 `.githooks/run-checks.sh`를 부르고, 이 스크립트가 기본 작업 폴더(`git rev-parse --git-common-dir`의 상위 폴더)의 `.venv` 파이썬으로 `python -m pytest -q -p no:cacheprovider`를 돌린다. 하나라도 실패하거나 파이썬을 못 찾으면 커밋이 막힌다. 저장소마다 한 번 `git config core.hooksPath .githooks`로 켜야 한다.
- **`git commit --no-verify`(검사 건너뛰기)는 금지다.** 검사가 실패하면 원인을 고친다. 고칠 수 없으면 사용자에게 묻는다.
- **테스트를 지우거나 약하게 만들어 통과시키지 않는다.** 동작을 일부러 바꿀 때는 무엇을 바꾸는지 밝히고, 그 동작을 확인하는 테스트를 새 동작에 맞게 고친다.
- `python -m pytest`(인자 없이)는 `research_desk/` 아래 테스트만 모은다(`pytest.ini`의 `testpaths = research_desk`). 구조 검사(`research_desk/tests/test_architecture.py`)가 그 안에 있다.

## 테스트가 지킬 것

- 실제 DB·AI·텔레그램·Codex CLI에 접속하지 않는다. 연결 함수와 클라이언트를 가짜로 바꾼다.
- 실제 `.env`를 읽지 않는다. `research_desk/conftest.py`가 모든 테스트에서 `core.settings`의 읽기 스위치를 끈다. `.env` 읽기를 시험하려면 그 파일의 `env_file` 준비(임시 `.env`)를 쓴다. 테스트가 기대는 환경 변수는 테스트가 직접 넣거나 지운다. 모듈·세션 범위 준비에서 `load_env()`를 부르지 않는다.
- 실제 관심 기업 파일(`~/.review_viewer/favorites.json`)과 실제 `frontend/dist`에 기대지 않는다. 경로를 인자로 받는 함수에 임시 경로를 준다. 그래서 별도 작업 폴더(git worktree)에서 돌리든 기본 작업 폴더에서 돌리든 결과가 같아야 한다.
- 각 칸의 테스트는 그 칸의 `tests/` 폴더에 두고, 폴더마다 `__init__.py`를 둔다(같은 이름의 테스트 파일이 섞이지 않게).

## 칸 경계 (구조 검사가 강제)

`research_desk/` 아래 모든 파이썬 파일의 import를 읽어 검사한다. 각 칸의 `tests/` 폴더는 "옛 코드 금지"만 적용받는다. 위반은 `파일:줄 — R<번호> <이름>: 설명`으로 나온다.

| 실패 이름 | 규칙 |
|---|---|
| `R1 core` | `research_desk.core`는 `research_desk`의 다른 칸을 import하지 않는다 |
| `R2 domain` | `domain`은 `core`·`domain`만 import한다 |
| `R3 collector` | `collector`는 `core`·`domain`·자기 칸만 import한다 |
| `R4 tagger` | `tagger`는 `core`·`domain`·자기 칸만 import한다 |
| `R5 기능` | 기능은 `core`·`domain`·자기 폴더를 쓸 수 있다. 다른 기능은 `research_desk.features.<기능>` 자체나 거기서 이름을 import하는 방식으로만 쓴다. `research_desk.features.<기능>.<하위모듈>`을 직접 import하면 안 된다. `collector`·`tagger`·`web`·`cli`는 import하지 않는다 |
| `R6 순환 금지` | 기능 사이 의존은 서로 물고 물리지 않는다 |
| `R7 web` | `web`은 `core`·`domain`·자기 칸·기능의 공개 창구만 import한다. `import research_desk.features`(묶음 전체)도 안 된다 |
| `R8 입구` | `cli.py`·`__main__.py`는 각 칸의 등록 함수와 `core`·`domain` 함수를 쓴다. 기능은 공개 창구로만 쓴다. 다른 칸은 `cli`·`__main__`을 import하지 않는다 |
| `R9 외부 도구` | `supabase`, `asyncpg`, `openai`, `anthropic`, `dotenv`, `fitz`/`pymupdf`는 `core` 안에서만 import한다. `telethon`은 `collector`, `langgraph`는 `tagger` 안에서만 쓴다 |
| `R10 옛 코드` | `langgraph_tagger`와 옛 루트 모듈 이름(`collector`, `config`, `storage`, `telegram_client`, `main`)을 최상위 이름으로 import하지 않는다(테스트 포함) |
| `R11 표 주인` | `.table('<표>')` 호출(별칭 `.from_('<표>')` 포함)과, 문서 설명문이 아닌 문자열 안의 `FROM`/`UPDATE`/`INTO`/`JOIN <표>`는 그 표의 주인 칸에만 있다. `reports` → `collector`, `tagger`, `features/review`, `features/reports` / `failed_attempts` → `collector` / `report_summaries` → `features/analysis` |

- **공개 창구는 `__init__.py`가 직접 묶은 이름뿐이다.** `from .router import router`처럼 명시적으로 묶은 이름만 다른 칸이 쓸 수 있다. `from .x import *`나 `__all__`에만 적은 이름은 공개로 치지 않는다.
- 상대 import(`from . import x`)는 실제 모듈 이름으로 풀어서 같은 규칙으로 검사한다.
- 지금의 기능 의존 방향: `reports → analysis`, `compare → reports, analysis`, `coverage → reports`, `review → coverage`. `companies`와 `analysis`는 다른 기능을 쓰지 않는다. 새 연결은 순환을 만들지 않아야 한다.
- 새 표가 생기면 주인 기능 하나를 정하고, 나머지 기능은 그 기능의 공개 창구로 읽는다(검사 규칙의 주인 목록에도 더한다).

## 칸 안의 모양

- 기능 하나 = `research_desk/features/<기능>/` 폴더 하나. 칸 이름은 역할이 같다: `router.py`(웹 주소), `service.py`(업무 처리), `store.py`(DB 읽기·쓰기), `logic.py`(DB·네트워크 없이 테스트할 수 있는 계산), `jobs.py`(백그라운드 명령, 필요할 때만), `settings.py`(기능 전용 설정), `tests/`. 필요 없는 칸은 만들지 않는다.
- **새 기능 등록은 두 곳뿐이다.** 웹 주소가 있으면 `research_desk/web/app.py`의 `FEATURES` 목록에 한 줄, 명령이 있으면 그 기능의 `jobs.py`에 `register(subparsers)`를 두고 `research_desk/cli.py`의 `build_parser`에 한 줄. 웹 조립부와 명령 입구에는 업무 처리를 넣지 않는다.
- 명령 등록 함수(`register`)와 그 모듈의 최상위 import는 가볍게 둔다. `python -m research_desk`는 명령마다 모든 등록 모듈(`collector.cli`, `tagger.cli`, `web.server`)을 import한다. LangGraph(분류 그래프)와 FastAPI·uvicorn(웹 서버), Telethon(텔레그램)은 그 명령이 실제로 돌 때만 import한다. `research_desk/tests/test_cli.py`가 인자 없는 실행에서 FastAPI·uvicorn·LangGraph가 올라오지 않는지 확인한다. 이 확인을 지우거나 느슨하게 하지 않는다. 반면 Supabase·asyncpg·Anthropic·OpenAI 라이브러리는 `tagger.cli`가 맨 위에서 `core.db`·`core.llm`을 불러오므로 지금 모든 명령에서 올라온다(테스트가 막지 않는다).
- 기능의 명령을 처음 붙일 때는 위 확인과 부딪힌다. `cli.py`는 기능을 공개 창구로만 쓸 수 있는데, 지금 기능 창구(`__init__.py`)는 모두 FastAPI를 함께 불러온다(`router`나, `HTTPException`을 쓰는 `service`). 그대로 `register_jobs`를 창구에 붙이면 모든 명령이 FastAPI를 불러와 확인이 실패한다. 두 조건을 함께 지킬 방식을 사용자와 정한 뒤 진행한다.

## 기능 준비와 오류 응답

- 기능은 처음 쓰일 때 준비한다(설정·파일·연결 확인). 준비에 실패하면 `core.settings.NotReady(<기능 이름>, <이유>)`를 낸다. 기능 이름은 한국어로 고정이다: `기업 목록`(companies), `리포트`(reports), `분석`(analysis), `비교`(compare), `커버리지`(coverage), `검토`(review). 새 기능도 고정 이름을 정한다.
- 이유 문구는 한국어 고정 문장이다. 키 이름은 넣어도 되지만 파일 경로·키 값·오류 추적은 넣지 않는다.
- 준비에 실패한 기능은 실패를 기억하지 않는다. 다음 요청 때 다시 준비한다. 준비가 끝난 뒤 생긴 예기치 못한 오류(DB 일시 장애 등)는 `NotReady`로 바꾸지 않는다 — 웹 조립부가 일반 503 문구로 낸다.
- 다른 기능의 공개 창구에서 온 `NotReady`는 바꾸지 않고 그대로 올린다(실패한 기능의 이름이 그대로 보여야 한다).
- HTTP 오류(404·409·422)는 FastAPI의 `HTTPException`으로 낸다. 문구는 웹 계약에 고정된 한국어 문장이다. `core`는 웹 예외를 쓰지 않는다(`core.pdf`의 `PDFNotFound` → 기능이 404로 바꾼다).

## 명령과 종료 코드

- 모든 실행은 저장소 루트에서 `python -m research_desk <명령>`이다. `sessions/`와 `.env`의 상대 경로가 현재 폴더 기준이다.
- 종료 코드 4는 "준비 문제"다: 아무것도 바꾸지 않았고, 안내대로 고친 뒤 다시 실행하면 되는 문제. 설정 누락, codex CLI 없음, 종목표를 못 읽음, 종목표 버전 불일치, `stocks set-version --as-of`의 잘못된 날짜, 그리고 `tag requeue`의 경우(발행처 사전을 못 읽음, `--unreadable`인데 분류 모델이 그림을 받지 못함, `--apply`인데 백필·재처리·수집·웹앱이 돌고 있거나 프로세스 목록을 못 읽음)가 여기에 든다. 이유와 할 일을 stderr 한 줄로 찍는다. `tag escalate --since`의 형식 오류는 4가 아니라 1이다. `tag`·`stocks` 명령과 새로 붙는 명령이 쓴다. 분류 명령은 4로 끝날 때 어떤 행도 가져가지 않은 상태여야 하고, `tag requeue`는 어떤 행도 되돌리지 않은 상태여야 한다(모든 확인을 DB에 닿기 전에 한다). `collect`만 예외로 설정 누락이 1이다.
- `tag requeue`는 DB에 닿은 뒤의 실패를 traceback 대신 stderr 한 줄과 1로 끝내고 stdout을 비운다(백업을 못 씀, 되돌린 행 수가 다시 확인한 행 수와 다름, 그 밖의 오류).
- 인자가 없거나 틀리면 사용법을 보여 주고 2, `--help`는 0이다.
- 새로 만드는 사용자 문구는 한국어로 쓴다. 이미 있는 영어 명령 출력(`<NAME> is required`, `Config error: Missing required env var: <NAME>`, `codex CLI not found for model <모델>`, JSON 보고의 키)은 바꾸지 않는다 — 스크립트와 운영자가 그 문구를 본다.

## 설정

- `.env` 읽기는 `core.settings.load_env()` 한 가지다. 이미 있는 환경 변수를 덮어쓰지 않고, `.env`를 `core/settings.py` 위치에서 위로 찾아 올라간다. 명령이 시작할 때, 웹 기능이 준비할 때, 분석이 모델 키를 확인할 때마다 다시 부른다.
- 모듈을 import하는 시점에는 설정을 읽지 않는다(모듈 최상위에서 `os.environ`·`load_env()` 금지).
- 공용 값은 `core.settings`의 읽기 함수로, 칸 전용 값은 그 칸의 `settings.py`가 같은 도우미(`required`, `optional`, `get_int`, `get_float`, `get_bool`, `model_name`)로 읽는다. `dotenv`를 직접 쓰지 않는다.
- 환경 변수 이름은 바꾸지 않는다. 모델 이름 변수는 `LLM_MODEL_<역할>` → 옛 이름 `OPENAI_MODEL_<역할>` → 기본값 순서로 찾는다.

## 운영 값

- 분류 LLM 동시 호출 `MAX_CONCURRENT_LLM`의 기본값과 운영 값은 **2**다. 백필 배치 `TAGGER_BATCH_SIZE_DEFAULT`와 래퍼의 `-BatchSize`는 **10**이다. 토큰 한도 확인 절차(공급자 콘솔의 한도 확인 + LangSmith 기록의 토큰 추이 확인, 한 단계씩 올림) 없이 바꾸지 않는다. 임의 값(100, 200 등)을 쓰지 않는다.
- 웹 서버의 AI 동시 호출은 분석과 비교를 합쳐 2개 고정이다(설정 값 없음).
- `LOCK_TTL_MINUTES × 60 > ⌈배치 크기 ÷ MAX_CONCURRENT_LLM⌉ × PER_ROW_DEADLINE_S`가 성립하도록 둔다(기본: 30분 > 5 × 90초 = 7.5분). 배치 크기나 행 시간 한도를 키울 때 이 식을 다시 계산한다.

## 데이터와 저장 값

- DB 구조(표·열·제약) 변경은 `migrations/NNN_<설명>.sql`을 지금 가장 큰 번호 다음 번호(세 자리)로 더해서만 한다. 이미 적용한 마이그레이션 파일은 고치지 않는다. 자동 적용 장치는 없다 — Supabase SQL 편집기나 직접 연결로 한 번 적용한다.
- 데이터를 바꾸는 마이그레이션은 다시 실행해도 결과가 같게 쓰고, 운영 DB에 적용하기 전에 바뀔 행을 백업한다.
- 저장되는 버전 이름 `langgraph-tagger@2.0`(분류기, `tagger/sql.py`의 상수 하나), `llm-summary@1.0`(분석)을 바꾸지 않는다. 바꾸면 기존 행과 새 행이 다른 버전으로 갈리고, 분석 결과 조회가 현재 버전만 보여 주므로 기존 분석이 화면에서 사라진다.
- 종목표 CSV를 바꾸는 커밋에는 `stocks set-version`으로 갱신한 버전 정보 파일이 함께 들어가야 한다. 함께 들어 있는 CSV와 버전 정보 파일의 지문이 같은지 테스트가 확인하므로, CSV만 바꾸면 커밋 검사가 막는다.
- 분류 체계 값(리포트 종류·사유·발행처 종류·상태·신뢰도)의 원본은 `research_desk/domain/vocabulary.yaml` 하나다. 다른 곳의 사본 가운데 분류기 응답 모양 파일(`tagger/llm_schemas.py`)의 리포트 종류·발행처 종류(발행처 종류는 AI가 내지 않고 행 상태의 타입에 쓰인다), 분류기 상태 타입의 사유, 검토 주소의 사유 목록은 테스트가 원본과 같은지 확인한다. 발행처 사전(`tagger/vocabulary/publishers.yaml`)의 구역 이름은 사전을 읽을 때 원본 발행처 종류와 대조한다(어긋나면 사전 읽기 실패). 분류기 배치 보고(`tagger/orchestrator.py`)의 사유·신뢰도 키, 분류기 상태 타입과 LLM 응답의 신뢰도 값, `tag inspect` SQL의 상태 이름, 화면 쪽 사본은 확인하지 않는다 — 값을 바꿀 때 이 사본들을 함께 고친다(예: 사유를 하나 더하면 테스트는 모두 통과하지만 배치 보고의 `oos`에서 그 사유가 빠진다). 새 사본을 만들면 원본과 비교하는 테스트를 붙인다. "분석 대상" 정의, "분석 대상 외 행 모양", 행을 `pending`으로 되돌리는 "되돌리는 모양"도 `research_desk/domain/reports.py` 한 곳에만 둔다. 다른 칸에 같은 규칙을 다시 적지 않는다.
- 발행처 이름의 원본은 발행처 사전 `research_desk/tagger/vocabulary/publishers.yaml` 하나다. 분류기가 저장하는 발행처는 그 사전의 정식 이름이거나 비어 있고, 발행처 종류는 그 이름의 사전 구역이다. 파일 이름 표기로 저장할 발행처를 정하지 않는다.
- 운영 DB의 분류된 행을 조건으로 골라 `pending`으로 되돌릴 때는 `tag requeue`를 쓴다. 손으로 쓰는 UPDATE와 달리 실행 중인 작업 확인 → 백업 → 한 트랜잭션 → 바뀐 행 수 확인을 코드로 지킨다.
- 웹 API의 주소·요청 값·응답 키·상태 코드·문구는 화면이 그대로 쓴다. 바꾸려면 화면 코드를 함께 바꾼다.

## 커밋

- 고친 것 하나에 커밋 하나. 여러 문제를 한 커밋에 묶지 않는다.
- 커밋할 파일을 경로로 지정해서 올린다. `git add -A`·`git add .`를 쓰지 않는다 — 작업 폴더에는 커밋하면 안 되는 사용자 파일(개인 메모, 내보낸 자료 같은 미추적 파일)이 생길 수 있다.
- 커밋 메시지는 영어 conventional 형식(`feat(...)`, `fix(...)`, `docs(...)`, `refactor(...)`, `chore(...)`)이다.
- GitHub에 올리는 것(push)은 사용자가 요청할 때만 한다.

## 스크립트

- `.ps1` 스크립트는 Windows PowerShell 5.1(`powershell -File …`)에서 동작해야 한다. 이 PC에는 PowerShell 7(`pwsh`)이 없다. 7 전용 문법(`&&`, `??`, 삼항 연산자 등)을 쓰지 않는다.
- 한국어가 들어가는 `.ps1`은 UTF-8 **BOM 포함**으로 저장한다.
- `.githooks/` 안 스크립트는 LF 줄바꿈이어야 한다(`.githooks/.gitattributes`가 강제). CRLF면 sh가 실행하지 못한다.
