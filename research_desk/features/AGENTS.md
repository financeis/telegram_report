# research_desk/features/ — 웹앱 기능 칸. 기능 하나가 폴더 하나다

## 맡는 일

- 웹앱의 업무 기능. 폴더 하나가 기능 하나다.
  - `companies`: 기업 목록·관심 기업
  - `reports`: 기업별 리포트 목록·PDF·분석 실행 주소, 분류가 끝난 `reports` 행 읽기
  - `analysis`: 리포트 1건 재무 분석과 `report_summaries` 표. 웹 주소는 없다
  - `compare`: 같은 기업의 두 보고서 비교
  - `coverage`: 리서치 커버리지 집계, 종목별 증권사 리포트 수
  - `review`: `review_needed` 행의 수동 검토
  - `prices`: 매일 주가 스냅샷 명령(`prices update`)과 주가 창구. 웹 주소는 없다
  - `peers`: 연 1회 유사도 계산 명령(`peers build`·`peers inspect`)과 유사 기업·테마 검색 주소
  - `freshness`: 모든 화면 맨 위 상태 줄의 주소(`/api/freshness`). 자기 표·설정이 없다
- 표 주인 몫. `reports` 표에서 분류가 끝난 행 읽기는 `features/reports`가, 검토 대기열 조회·검토 결과 쓰기·되돌리기는 `features/review`가 맡는다. `report_summaries`는 `features/analysis`가 주인이고, 두 보고서 비교 결과 저장도 analysis 창구(`save_comparison`)를 거친다. 주가 표 두 개(`stock_price_snapshot`, `price_update_runs`)는 `features/prices`, 유사 기업 표 다섯 개(`company_profiles`, `company_segments`, `company_embeddings`, `segment_embeddings`, `peer_builds`)와 DB 함수 두 개는 `features/peers`가 주인이다.
- 앞으로 붙일 기능(수급 분석, 퀀트 연결 등)도 여기에 폴더 하나로 붙인다.

## 맡지 않는 일

- `research_desk.collector`, `research_desk.tagger`, `research_desk.web`, `research_desk.cli`는 import하지 않는다. 구조 검사가 `R5 기능`으로 막는다. 수집·분류는 각자의 칸이, 서버 조립은 `web`이, 명령어 목록은 `cli.py`가 맡는다.
- 외부 도구 `supabase`, `asyncpg`, `openai`, `anthropic`, `dotenv`, `fitz`/`pymupdf`, `pymongo`/`bson`, `httpx`, `telethon`, `langgraph`는 import하지 않는다(`R9 외부 도구`). DB 연결은 `core.db`, AI 호출·임베딩은 `core.llm`, PDF는 `core.pdf`, MongoDB는 `core.mongo`, KIS는 `core.kis`, `.env` 읽기는 `core.settings.load_env()`로 한다.
- 주인이 아닌 표는 직접 다루지 않는다(`R11 표 주인`). 검사 대상은 `.table('<표>')` 호출과, docstring이 아닌 문자열 안의 `FROM`/`UPDATE`/`INTO`/`JOIN <표>`다. 남의 표 데이터는 주인 기능의 공개 창구로 받는다. `failed_attempts`는 수집기 표라 어떤 기능도 만지지 않는다.
- "분석 대상"(`tagging_status ∈ {auto, verified}` 그리고 `out_of_scope_reason IS NULL`)과 "분석 대상 외 행 모양"의 정의는 `domain.reports`에만 있다. `('auto', 'verified')` 같은 값을 기능 안에 다시 적지 말고 `IN_SCOPE_STATUSES`, `is_in_scope`, `oos_row_shape`, `OOS_REASONS`를 쓴다. 종목표 읽기·조회·버전 확인은 `domain.stocks`가 한다.
- DB 구조(표·열·제약)는 기능 코드가 만들거나 바꾸지 않는다. `migrations/NNN_*.sql`을 번호 순서대로 더하고, 사람이 Supabase SQL 편집기나 직접 연결로 한 번 적용한다. 자동 적용 장치는 없다.
- `features/__init__.py`에는 docstring만 둔다. 여기서 기능들을 모아 import하면 `R8 입구` 위반이다(패키지 뿌리 파일은 research_desk 모듈을 import하지 않는다). `features/` 바로 아래에 공용 파일(`features/common.py` 같은)을 두는 것도 같다. 그 파일은 패키지 뿌리로 판정돼 research_desk import가 전부 `R8 입구`로 잡힌다. 새 코드는 `features/<기능>/` 안에 둔다.
- 화면 코드(`frontend/`)는 이 칸 밖이다.

## 늘 지켜야 할 것

- 다른 기능은 공개 창구로만 쓴다: `from research_desk.features import reports` 또는 `from research_desk.features.reports import get_report`. 하위 모듈을 직접 import하면(`research_desk.features.reports.store`, 상대 import `..reports.store`도 같다) `R5 기능` 위반이다. 함수 안이나 `TYPE_CHECKING` 안의 import도 검사한다. `importlib` 같은 동적 import로 검사를 피하지 않는다.
- 공개 이름은 그 기능 `__init__.py`의 최상위(최상위 `if`·`try` 안 포함)가 직접 묶은 이름뿐이다: `import`, `from … import`, 값이 있는 대입, `def`, `class`. `import *`, `__all__` 목록, 하위 모듈 import의 부수효과로 생긴 이름(`from .store import f` 뒤의 `store`)은 공개가 아니다. 하위 모듈 자체를 내놓으려면 `from . import store`처럼 직접 묶는다.
- 기능 사이 의존은 순환하지 않는다(`R6 순환 금지`). 의존 방향은 `reports → analysis`, `compare → reports, analysis`, `coverage → reports`, `review → coverage`, `peers → coverage, prices, reports`, `freshness → prices, reports`이고, `companies`·`analysis`·`prices`는 다른 기능에 의존하지 않는다. 함수 안의 import도 의존으로 센다(`peers`는 `reports`를 명령 함수 안에서만 import하지만 그것도 간선이다). 새 의존을 더해도 이 그래프에 고리가 생기면 안 된다. 테스트 안의 역방향 import는 세지 않는다.
  - 고리가 생길 상황이면 아래쪽 기능이 위쪽을 부르게 하지 말고, 위쪽이 읽은 값을 인자로 넘기거나 함께 쓰는 것을 의존받는 쪽 기능(DB를 모르는 기준이면 `domain`)으로 내린다. `analysis`가 리포트 행을 직접 읽지 않고 `reports`가 읽은 행을 `analyze_report(row)`로 받는 것이 그 예다. 그래서 분석 실행 주소(`POST /api/reports/{rid}/analyze`)도 `reports`에 있다.
  - 기능 사이 연결을 `web`에 두지 않는다. 검토 뒤 커버리지 캐시 비우기는 `review`가 `coverage.invalidate()`를 직접 부른다.
- 다른 기능의 함수는 부르는 순간 창구 모듈의 속성으로 찾는다: `from research_desk.features import coverage`로 들고 있다가 `coverage.invalidate()`. `from research_desk.features.coverage import invalidate`로 미리 묶어 두면 테스트가 창구 속성을 바꿔 끼워도 닿지 않아, 가짜 대신 진짜 DB 연결을 만들려다 실패한다.
- 준비 실패는 `core.settings.NotReady(<기능 이름>, <이유>)` 하나로만 알린다. `web`이 이것을 그 기능 주소의 503 `{"detail": "<기능 이름> 기능을 지금 쓸 수 없습니다: <이유>"}`로 바꾼다.
  - 기능 이름: `기업 목록`(companies), `리포트`(reports), `분석`(analysis), `비교`(compare), `커버리지`(coverage), `검토`(review), `주가`(prices), `유사 기업`(peers), `자료 기준일`(freshness). 새 기능은 겹치지 않는 한국어 이름 하나를 정해 `AREA` 상수로 둔다.
  - 이유는 한국어 고정 문장이다. 변수 이름은 넣어도 되지만 파일 경로·키 값·트레이스백은 넣지 않는다. 이미 쓰는 문장: `DB 접속 설정(SUPABASE_URL, SUPABASE_SERVICE_KEY)이 없습니다`, `종목표 파일을 읽을 수 없습니다`. 같은 원인에는 같은 문장을 쓴다.
  - `StockListError` 문장에는 파일 경로가 들어 있어 이유로 쓰지 않는다. 원인은 `logger.warning`으로 로컬 로그에만 남긴다.
  - 다른 기능의 창구에서 올라온 `NotReady`는 잡아서 바꾸지 않고 그대로 올려 보낸다. 화면에는 실패한 기능의 이름이 보여야 한다(커버리지 화면의 `리포트 기능을 지금 쓸 수 없습니다: …`).
  - 준비를 마친 뒤 일하다 난 오류(DB 일시 장애, 만료된 키 때문에 외부 호출이 거절됨)는 `NotReady`로 바꾸지 않는다. 그 요청의 오류로 두면 `web`이 일반 503 문구로 답한다.
- 브라우저 응답에 `file_path`, 저장 폴더 경로, 서비스 키·AI 키, 트레이스백을 넣지 않는다.
- 기능의 웹 주소는 모두 `/api/`로 시작한다. 화면의 `api()`가 `/api`를 붙여 부르고, 개발용 화면 서버(5173)도 `/api`만 8520으로 넘긴다.
- 화면에 보여야 하는 오류는 `HTTPException(<코드>, '<한국어 문장>')`처럼 `detail`을 문자열로 낸다. 화면은 `detail`이 문자열일 때만 그 문장을 띄우고, FastAPI 자체 검증 오류(`detail`이 목록)에는 `요청을 완료하지 못했습니다.`만 띄운다. 새 사용자 문구는 한국어로 쓴다.
- 기존 주소의 요청 값·응답 키·상태 코드·안내 문구는 화면이 그대로 쓰므로, 화면 코드를 함께 바꾸지 않고는 바꾸지 않는다. 기존 핸들러의 함수 이름·인자·요청 본문 모델 이름도 바꾸지 않고 docstring을 달지 않는다. 바꾸면 `/openapi.json`의 `summary`·`operationId`·`description`이 달라져, 그 항목을 고정한 테스트가 실패한다.

## 이 칸의 방식

**폴더 안의 칸.** 이름이 같으면 역할도 같다. 필요 없는 칸은 만들지 않는다.

| 파일 | 역할 |
|---|---|
| `__init__.py` | 공개 창구. 다른 칸이 쓸 이름만 묶고, docstring에 그 이름들의 계약을 적는다. 명령이 있는 기능은 FastAPI를 불러오지 않는 이름만 묶는다(아래 "새 기능 붙이기" 3) |
| `router.py` | `router = APIRouter()`와 웹 주소. 요청 값 검사까지만 하고 일은 서비스에 넘긴다 |
| `service.py` | 업무 처리와 준비. 프로세스 하나에 서비스 하나 |
| `store.py` | DB 읽기·쓰기. 그 표의 주인 기능에만 둔다 |
| `logic.py` | 계산. DB·네트워크·설정·파일 없이 테스트할 수 있어야 한다 |
| `jobs.py` | 백그라운드 작업 명령의 등록 함수. 필요할 때만 둔다 |
| `settings.py` | 기능 전용 설정 |
| `tests/` | 테스트. `__init__.py`를 함께 둔다 |

이 밖의 이름(`favorites.py`, `rules.py`, `prompts.py`, `schemas.py` …)은 기능 고유 부품에 쓴다.

**준비(처음 쓸 때).**
- 라우터는 `service=Depends(get_service)`로 서비스를 받는다. `get_service()`는 모듈 전역 `_service`를 잠금 안에서 한 번만 만들고, 만들 때 아무것도 읽지 않는다. FastAPI는 요청 값 검사(422)보다 의존 함수를 먼저 부른다. 여기서 설정을 읽거나 연결하면 잘못된 요청도 422 대신 503이 되고, 모든 요청이 DB 연결부터 만든다.
- 준비는 서비스 메서드가 처음 필요로 할 때 한다(`store()`, `stock_list()` 등): `settings.load_env()` → 설정 확인 → 연결 또는 파일 읽기. 성공하면 그 결과를 프로세스가 끝날 때까지 들고 있고, 실패하면 아무것도 남기지 않아 다음 요청에서 다시 준비한다. 잠금 안에서 한 번 더 확인해 동시에 두 번 준비하지 않는다.
  - 그래서 빠진 파일을 채우거나 `.env`에 빠진 값을 더하면 재시작 없이 다음 요청에 반영된다. `.env`에 이미 있던 값을 바꾼 것(`load_env()`는 덮어쓰지 않는다)과, 한 번 읽은 종목표 파일을 바꾼 것은 웹앱을 다시 켜야 반영된다.
- DB가 필요 없는 확인(요청 값, 미리보기 쪽 범위, 되돌리기 토큰 등)은 준비보다 먼저 한다. 그래야 설정이 없어도 그 답(422·404·409)이 제대로 나간다.
- 준비 조건. DB 접속 설정(`SUPABASE_URL`, `SUPABASE_SERVICE_KEY`)은 reports·review·analysis·prices·peers가 확인한다. compare·coverage·freshness는 직접 확인하지 않고, 거쳐 가는 창구(coverage는 reports, compare는 reports·analysis, freshness는 prices·reports)가 낸 결과를 그대로 받는다. 종목표 파일은 companies·reports(기업 정보)·coverage(시장 집계)·peers가 확인한다. 분석 모델의 키 또는 codex CLI는 analysis가 실제 AI 호출 직전에만 확인한다. peers는 요청마다 공개된 유사도 계산 결과가 있는지 보고(없으면 그 요청만 `NotReady`, 기억하지 않음), 테마 검색만 질의 임베딩 직전에 `OPENAI_API_KEY`를 확인한다.
- 종목표가 필요한 기능은 `domain.stocks.StockList.load(settings.krx_csv_path())`로 직접 읽어 자기 이름으로 `NotReady`를 낸다. 지문이 버전 정보와 다르거나 버전 정보가 없으면 경고 로그만 남기고 그대로 동작한다. 웹은 종목표를 목록·이름 표시에만 쓰고 버전 값을 저장하지 않기 때문이다.
- 웹 서버 안의 AI 호출은 `analysis.ai_slot()` 안에서 한다(분석·비교를 합쳐 서버 전체 동시 2개). 클라이언트·모델·호출 시간 한도는 `analysis.phase2_llm()`에서 받는다. 이 함수가 `.env`를 다시 읽고, 키가 없으면 `NotReady("분석", …)`를 낸다. 기능이 따로 세마포어를 만들거나 분석 설정(`LLM_MODEL_PHASE2`, `PHASE2_*`)을 직접 읽지 않는다.
  - 예외는 하나뿐이다: peers의 테마 검색 질의 임베딩은 `ai_slot()` 밖에서 peers 자체 자리(동시 2개, 15초)로 하고, 모델은 공개 빌드의 임베딩 모델이다. 1초 안팎의 호출이 몇 분짜리 분석 뒤에 줄 서지 않게 하려는 것이고, 분석 모델로는 임베딩을 만들 수 없다. 다른 새 웹 AI 호출을 이 예외에 넣지 않는다.
- 동기 DB 호출을 하는 핸들러는 `def`로 둔다(FastAPI가 스레드에서 돌린다). `async def` 안에서 동기 호출이 필요하면 `asyncio.to_thread`로 감싼다(reports의 분석 실행, compare가 그렇게 한다). 이벤트 루프에서 그대로 부르면 그동안 서버 전체가 멈춘다.

**설정.** 공용 값은 `core.settings`의 함수(`supabase_url()`, `krx_csv_path()`, `storage_base_dir()` …)로, 기능 전용 값은 그 기능의 `settings.py`가 `core.settings`의 도우미(`optional`, `get_int`, `model_name` …)로 읽는다. import 시점에는 읽지 않는다. `.env` 변수 이름은 바꾸지 않는다(사용자 `.env`는 고치지 않는다). PDF 폴더(`./reports`)와 종목표의 기본 경로는 현재 폴더 기준이라, 웹과 명령은 모두 저장소 루트에서 실행한다.

**새 기능 붙이기.**
1. `research_desk/features/<이름>/`에 필요한 파일만 만들고 `__init__.py`에 공개 이름을 묶는다. 명령이 없고 주소가 있으면 `from .router import router`.
2. 주소가 있으면 `research_desk/web/app.py`의 기능 import 줄(`from research_desk.features import …`)과 `FEATURES` 목록 끝에 창구 이름을 더한다. 주소 없는 기능(analysis, prices)은 넣지 않는다. `create_app()`은 목록마다 `feature_router(feature)`로 라우터를 받는다: 창구에 `web_router`가 있으면 그것을 부르고, 없으면 `feature.router`를 쓴다.
3. 백그라운드 작업 명령이 있으면 `jobs.py`에 `register(subparsers)`를 두고, 창구에서 `from .jobs import register as register_jobs`로 묶는다. `research_desk/cli.py`에는 창구 import 한 줄과 `build_parser()` 안의 등록 한 줄을 더한다(`cli.py`도 기능은 창구로만 쓴다, `R8 입구`).
   - 창구 모양. `cli.py`는 어느 명령이든(명령 없이, `--help`만 해도) 이 창구를 import한다. 그래서 명령이 있는 기능의 창구는 FastAPI를 불러오지 않는 이름만 묶는다(`register_jobs`와 다른 기능이 쓸 함수 — 그 함수의 모듈도 최상위에서 FastAPI·`HTTPException`을 import하지 않는다). 웹 주소도 있으면 `router`를 묶지 말고 함수 `web_router()`를 둔다. 이 함수가 불릴 때 안에서 `from .router import router`를 하고 돌려준다. `jobs.py` 최상위에는 `core.settings` 정도만 두고, DB·MongoDB·AI 클라이언트와 다른 기능 창구는 명령 함수 안에서 import한다.
   - 확인: `research_desk/tests/test_cli.py`가 명령 없는 실행에서 `fastapi`·`uvicorn`·`research_desk.web.app`·`langgraph`·`pymongo`·`research_desk.features.peers.router`·`.service`가 올라오지 않는지 본다. 새 명령이 무거운 패키지를 끌어오면 그 목록에 더한다. 이 검사를 지우거나 느슨하게 하지 않는다. 기능 안에도 창구 import가 무거운 것을 부르지 않는지 보는 테스트를 둔다(peers의 `tests/test_window.py`처럼).
   - `web_router()`를 한 번 부르면 `research_desk.features.<기능>.router`는 하위 모듈이 된다. 그 기능의 라우터는 `web_router()`로만 받는다.
   - 명령이 실행 중에 FastAPI를 부르는 창구(예: reports)를 쓰면 그 명령은 웹 패키지(`requirements-workspace.txt`)가 있어야 돈다. `peers build`가 그렇다.
   - 명령은 시작할 때 `settings.load_env()`를 부른다. 준비 문제(키·설정·파일 없음)는 아무 일도 하기 전에 이유 한 줄을 stderr에 내고 종료 코드 4로 끝난다. 4는 다시 돌려도 저절로 풀리지 않는 상태라는 뜻이다(기다리면 풀리는 상태는 1). 정상은 0, 인자 오류는 argparse의 2, `--help`는 0이다.
   - 정해진 시간 실행은 윈도우 작업 스케줄러가 `scripts/`의 `.ps1`을 부르고, 그 스크립트가 저장소 폴더로 옮겨 `python -m research_desk <명령>`을 실행하는 방식이다(`scripts/run-prices.ps1`). 한국어가 든 `.ps1`은 UTF-8(BOM 포함)로 저장한다. Windows PowerShell 5.1은 BOM 없는 스크립트를 시스템 코드 페이지(949)로 읽어 한국어가 깨진다.
4. 새 표를 만들면 `migrations/`에 SQL을 더하고, 그 표와 주인 칸을 `research_desk/tests/architecture_rules.py`의 `TABLE_OWNERS`에 등록한다. 등록하지 않은 표는 구조 검사가 지키지 못한다. 여러 기능이 같이 쓰는 데이터는 기능 하나가 주인이 되고, 나머지는 그 창구로 읽는다(주가는 prices가 주인이다).
5. 테스트를 더한다(아래).

## 테스트

- 실행: 저장소 루트에서 `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider research_desk/features`. `.pytest_cache/`에 사용자 파일이 있어 캐시를 끈다. 커밋할 때마다 구조 검사를 포함한 전체 테스트가 돌고, 하나라도 실패하면 커밋이 막힌다. `--no-verify`로 건너뛰지 않는다.
- 테스트는 바깥 상태에 기대지 않는다.
  - `.env` 읽기는 모든 테스트에서 꺼져 있다(`research_desk/conftest.py`). `.env`를 다시 읽는 동작은 `env_file` fixture로만 시험한다. 임시 `.env`에 줄을 써 넣고 다음 요청을 본다.
  - 기능이 읽는 환경 변수는 테스트 시작 때 지우고 필요한 것만 넣는다. 상대 경로 기본값이 실제 `reports/`·종목표에 닿지 않게 현재 폴더를 임시 폴더로 바꾼다.
  - `core.db.supabase_client`는 거부하는 가짜로 바꾸고, DB가 필요한 테스트에만 메모리 가짜를 건넨다. MongoDB·AI 클라이언트를 만드는 core 함수도 필요하면 거부하게 바꾼다(peers의 `tests/conftest.py`). 실제 Supabase·AI·텔레그램·MongoDB·KIS에는 닿지 않는다.
  - 홈 폴더를 바꿀 때는 `USERPROFILE`과 `HOME`을 둘 다 바꾼다. Windows의 `Path.home()`은 `USERPROFILE`을 본다. 실제 관심 기업 파일 `~/.review_viewer/favorites.json`은 어떤 테스트도 건드리지 않는다.
  - 프로세스 전역 상태(`_service`, analysis의 `_supabase_client`·`_analyzing`)는 `monkeypatch.setattr`로 새 값을 넣어 시작하고, 끝나면 되돌린다.
- 기능 테스트는 자기 `router`와 `NotReady` → 503 처리기만 붙인 작은 FastAPI 앱으로 주소를 시험하고, 서비스는 `app.dependency_overrides[get_service]`로 끼운다. 창구 함수는 `get_service()`를 직접 부르므로 `dependency_overrides`가 닿지 않는다. 창구를 거치는 경로는 `_service`를 바꾸거나, 창구 속성을 `monkeypatch.setattr(reports, 'period_rows', …)`처럼 바꿔 시험한다.
- 새 기능에서 꼭 시험할 것: 준비 실패 때 그 주소만 503 + 고정 문장(경로·키 없음), 원인을 고치면 다음 요청에서 회복, `.env`에 더한 값 반영, 서비스를 만들 때 아무것도 읽지 않음, 422 같은 사전 확인이 준비보다 먼저.
- 웹 조립 테스트도 함께 늘린다. 새 주소는 `research_desk/web/tests/fakes.py`의 `SPEC_ROUTES`와 `test_app.py`의 주소 순서 목록에 더하고(기존 항목은 그대로), 새 요청 본문 모델은 `/openapi.json` 스키마 이름 목록에 더한다. 프로세스 전역 서비스가 있으면 `web/tests/conftest.py`의 `FEATURE_SERVICES`와 `test_making_the_app_reads_no_setting_and_prepares_no_feature`의 목록에 그 모듈을 더한다. 준비 조건은 `test_isolation.py`에 더한다. 새 명령은 `research_desk/tests/test_cli.py`의 `COMMANDS`·`USAGE_ERRORS`·`ENV`에, 새 명령이 끌어오는 무거운 패키지는 `NOT_LOADED_WITHOUT_A_COMMAND`에 더한다. `web_router`로 붙는 기능은 `web/tests/test_app.py`의 `feature_router` 테스트가 그대로 덮는다.
- `tests/__init__.py`가 있어야 테스트가 `research_desk.features.<이름>.tests` 패키지로 잡힌다. 그래야 `from .fakes import …` 같은 상대 import가 되고, 다른 기능의 같은 이름 테스트 파일과 모듈 이름이 겹치지 않는다.
- 구조 검사 실패는 `파일:줄 — 규칙 이름: 설명`으로 나온다. `tests/` 폴더와 `conftest.py`에는 `R10 옛 코드`(`langgraph_tagger`와 옛 루트 모듈 `collector`·`config`·`storage`·`telegram_client`·`main` import 금지)만 적용되므로, 테스트에서는 다른 기능의 하위 모듈이나 가짜를 import해도 된다.
