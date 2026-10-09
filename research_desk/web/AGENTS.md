# research_desk/web/ — 웹 서버 조립(공통 장치·오류 응답·화면 파일·기능 등록 목록)과 `web` 명령

## 맡는 일

- `create_app(dist=None)`(`app.py`): FastAPI 앱 하나에 공통 장치, 오류 응답, `/api/health`, 기능 라우터(`FEATURES` 순서), 화면 파일을 붙인다.
- `FEATURES`: 기능 등록 목록. 웹 주소가 있는 기능은 여기 이름 하나로 앱에 연결된다. 지금 순서는 `companies, reports, compare, coverage, review, peers, freshness`다.
- `feature_router(feature)`: 기능 창구에서 라우터를 받는 한 가지 방법. 창구에 `web_router`가 있으면 그것을 불러 받고, 없으면 `feature.router`를 쓴다.
- `web` 명령(`server.py`): `python -m research_desk web [--view reports|market|review]`.
- 직접 맡는 주소: `GET /api/health`, `GET /`, `/assets/*`, `GET /openapi.json`.
- 웹 입구 한 곳의 보안 장치(허용 호스트, 다른 출처의 쓰기 거절). 다른 기기 접속이 필요해져 로그인 같은 접근 제어를 붙이게 되면, 기능마다가 아니라 여기 한 곳에 붙인다. 방식은 그때 정한다.

## 맡지 않는 일

- 업무 처리를 하지 않는다. DB·표·AI·PDF를 다루지 않는다(`R11 표 주인`, `R9 외부 도구`). 기능 사이를 잇지도 않는다. 검토 뒤 커버리지 캐시 비우기 같은 연결은 그 기능이 상대 창구를 부른다.
- 기능은 공개 창구(`from research_desk.features import companies`)로만 import한다. 하위 모듈(`research_desk.features.companies.service` 등)이나 `collector`·`tagger`를 import하면 `R7 web` 위반이다. `cli`·`__main__`도 import하지 않는다(`R8 입구`).
- 기능 준비(설정 읽기·연결·종목표 읽기)는 각 기능이 처음 쓰일 때 한다. 앱을 만들 때 하지 않는다.
- 화면 코드는 `frontend/`에 있다. web은 빌드 결과 `frontend/dist`만 내준다. 빌드는 `scripts/start-workspace.ps1`이 하거나 `cd frontend && npm run build`로 한다.
- `.env`는 `core.settings.load_env()`로만 읽는다(`dotenv` import 금지).

## 늘 지켜야 할 것

- 공통 장치. 값과 등록 순서를 그대로 둔다.
  - `FastAPI(title='Research Desk', docs_url=None, redoc_url=None)`. `/openapi.json`은 켜 둔다. 화면은 쓰지 않지만 이 PC 안에서만 열리는 서버라 그대로 둔다. `/docs`·`/redoc`은 404다. `version`은 넘기지 않는다(`info`가 `{"title": "Research Desk", "version": "0.1.0"}`로 고정돼 있다).
  - 허용 호스트는 `127.0.0.1`, `localhost`, `testserver`이고 포트는 보지 않는다. 그 밖은 400 `Invalid host header`. `testserver`는 테스트 클라이언트의 호스트라 빼지 않는다.
  - GET·HEAD·OPTIONS가 아닌 요청에 `Origin`이 있고 그 호스트:포트(scheme은 보지 않는다)가 `127.0.0.1:8520`, `localhost:8520`, `127.0.0.1:5173`, `localhost:5173`이 아니면 403 `{"detail": "허용되지 않은 요청입니다."}`. `Origin`이 없으면 통과하고 `null`은 거절한다. 5173은 개발용 화면 서버(Vite)가 `/api`를 8520으로 넘기는 경우다.
  - 호스트 검사를 먼저 등록하고 출처 검사를 나중에 등록한다. 나중에 등록한 미들웨어가 바깥에서 먼저 돌므로 출처 검사가 먼저 판정한다(다른 호스트 + 다른 출처 = 403, 다른 호스트 + 자기 출처 = 400). 순서를 바꾸지 않는다.
  - CORS 장치를 더하지 않는다. API 주소의 HEAD·OPTIONS가 405인 것까지 테스트가 고정한다.
- 오류 응답
  - `NotReady` → 503 `{"detail": "<기능 이름> 기능을 지금 쓸 수 없습니다: <이유>"}`(`str(exc)`). 로컬 로그에는 `<메서드> <경로>: <문장>` 경고 한 줄만 남기고 트레이스백은 남기지 않는다. 그 기능 주소만 멈추고, 다른 기능과 `/api/health`는 200이다. 동기 핸들러, 비동기 핸들러, 의존 함수 어디서 나도 같다.
  - 그 밖의 예기치 못한 예외 → 트레이스백과 함께 로그(`logger.exception`)에 남기고 503 `{"detail": "데이터를 불러오거나 분석하지 못했습니다. 연결 상태를 확인하고 다시 시도해 주세요."}`. 예외 문장은 경로·키를 담을 수 있으므로 응답에 넣지 않는다.
  - 기능이 낸 `HTTPException`(404·409·422)은 FastAPI 기본 처리 그대로 `{"detail": …}`다.
- `GET /api/health` → `{"status": "ok"}`. 아무것도 확인하지 않는다. DB나 종목표 확인을 넣으면 "어떤 기능이 고장 나도 늘 200"이 깨진다.
- `FEATURES`에는 웹 주소가 있는 기능의 창구만 넣고, 목록 순서대로 `include_router(feature_router(feature))`한다. 주소가 없는 `analysis`·`prices`는 넣지 않는다. 목록 순서가 곧 주소 등록 순서이고 테스트가 고정하므로, 새 기능은 끝에 더한다. 앱의 주소 표(메서드·경로)는 정해진 목록과 정확히 같아야 하고, 같은 주소가 두 번 붙으면 안 된다.
- `feature_router`는 `web_router`를 먼저 본다. 명령이 있는 기능(peers)은 창구가 FastAPI를 불러오지 않도록 라우터를 묶지 않고 `web_router()`로 넘긴다. 그런데 `web_router()`를 한 번 부르면 `peers.router`가 라우터가 아니라 하위 모듈이 되므로, 순서를 뒤집어 `feature.router`를 먼저 보면 모듈을 라우터로 넘기게 된다. 기능별로 `include_router(peers.router)` 같은 줄을 따로 쓰지 않는다.
- 화면 파일
  - 기본 위치 `DEFAULT_DIST`는 이 파일 위치에서 계산한 `<저장소>/frontend/dist` 절대 경로다. 현재 폴더 기준이 아니다.
  - 앱을 만들 때 `dist` 폴더가 있으면 `/assets`에 `dist/assets`를 연결한다. `dist`는 있는데 `dist/assets`가 없으면 Starlette `StaticFiles`가 RuntimeError를 내 앱 생성, 곧 서버 시작이 실패한다. 화면을 다시 빌드하면 풀린다.
  - `GET /`는 요청마다 `dist/index.html`을 찾아 `Cache-Control: no-cache`로 낸다. 그래서 다시 빌드하면 서버를 다시 켜지 않아도 새 화면이 나간다. 없으면 503 `{"detail": "프론트엔드를 먼저 빌드해 주세요: cd frontend && npm run build"}`.
  - 화면 라우트(`/assets`, `/`)는 기능 라우터 뒤에 붙는다.
- `health`, `index` 함수의 이름을 바꾸거나 docstring을 달지 않는다. `/openapi.json`의 `summary`·`operationId`(`health_api_health_get`, `index__get`)가 테스트로 고정돼 있다.
- 앱을 만들 때 설정을 읽지 않고(`load_env` 호출 없음) 어떤 기능 서비스도 만들지 않는다. 모듈을 import해도 앱이 생기지 않는다. 모듈 전역에 `app = create_app()`을 두지 않는다.
- `web` 명령
  - `Research Desk: http://127.0.0.1:8520/?view=<view>` 한 줄을 찍고 `uvicorn.run(create_app(), host='127.0.0.1', port=8520)`. 서버가 멈추면 0이다. `--view`는 `reports`(기본)·`market`·`review`이고 그 밖은 사용법 오류 2다. 보기 이름은 화면 주소의 `?view=`에만 쓰이고 서버 동작은 같다.
  - 시작할 때 `settings.load_env()`를 부른다(모든 명령의 공통 규칙). 기능은 준비할 때마다 또 읽는다.
  - `127.0.0.1`에서만 연다. 로그인이 없으므로 `0.0.0.0` 등으로 넓히지 않는다.
  - 프로세스 하나로 돈다. `workers`·`reload`를 넣지 않는다. 검토 되돌리기 기록, 커버리지 캐시, 웹 서버 AI 동시 호출 2개 제한, 분석 중 표시가 모두 프로세스 메모리에 있다.
  - 저장소 루트에서 실행한다. PDF 폴더(`./reports`)와 종목표 기본 경로가 현재 폴더 기준이다.
- FastAPI는 `web` 명령이 돌 때만 불러온다.
  - `cli.py`는 어느 명령이든 `research_desk.web.server`를 import한다. 그래서 `server.py` 최상위는 `argparse`와 `core.settings`만 import하고, `uvicorn`과 `.app`(FastAPI와 모든 기능)은 `serve()` 안에서 import한다. `web/__init__.py`는 아무것도 import하지 않는다.
  - 이유: FastAPI·uvicorn은 `requirements-workspace.txt`에만 있다. `collect`·`tag`·`stocks`·`prices`·`peers inspect`는 웹 패키지 없이 돌고 가볍게 시작해야 한다. `research_desk/tests/test_cli.py`가 새 프로세스로 `python -m research_desk`를 돌려 `fastapi`·`uvicorn`·`research_desk.web.app`(과 LangGraph·pymongo·유사 기업 웹 쪽)이 불러와지지 않았는지 확인한다.
  - 예외: `peers build`는 실행 중에 리포트 기능 창구를 거쳐 FastAPI를 불러오므로, `web`처럼 웹 패키지가 설치돼 있어야 돈다. 명령 없는 실행과 다른 명령은 그대로 웹 패키지에 기대지 않는다.

## 이 칸의 방식

- 새 기능 등록은 `app.py`의 `from research_desk.features import …` 줄과 `FEATURES` 목록에 창구 이름을 더하는 것으로 끝난다(라우터를 `router`로 묶은 창구든 `web_router()`를 둔 창구든 같다). 다른 연결 코드는 쓰지 않는다. 새 주소는 `/api/`로 시작하고 기존 주소와 겹치지 않아야 한다.
- 앱을 만들 때(`create_app`) `web_router()`가 불리므로 그 기능의 라우터·서비스 모듈과 FastAPI가 이때 올라온다. 서비스 객체를 만들거나 설정을 읽지는 않는다(그 기능의 `get_service`가 처음 요청 때 만든다).
- 기능 서비스는 각 기능의 `get_service`(라우터의 의존 함수)로 들어온다. web이 서비스를 만들거나 끼우지 않는다.
- 미들웨어·오류 처리기·`/api/health`·화면 라우트는 모두 `create_app()` 안에서 등록한다. 앱은 부를 때마다 새로 만들어진다.

## 테스트

- 실행: 저장소 루트에서 `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider research_desk/web`.
- `tests/conftest.py`의 `clean_env`(autouse)가 기능이 읽을 수 있는 변수를 모두 지우고, 현재 폴더와 홈 폴더(`USERPROFILE`·`HOME`)를 임시 폴더로 바꾸고, `core.db.supabase_client`를 거부하게 하고, 기능의 프로세스 전역 상태(`FEATURE_SERVICES`의 `_service`, analysis의 `_supabase_client`·`_analyzing`)를 새로 시작한다. 프로세스 전역 상태를 가진 기능을 더하면 `FEATURE_SERVICES`에 그 모듈을 더한다.
- 앱은 늘 `create_app(dist=<임시 폴더>)`로 만든다(`dist` fixture: `index.html`과 `assets/app.js`). 기본 폴더를 쓰는 경로는 `monkeypatch.setattr(app_module, 'DEFAULT_DIST', …)`로 돌린다. 실제 `frontend/dist`가 있든 없든 결과가 같아야 한다. `web` 명령 테스트는 `uvicorn.run`을 바꿔 끼워 서버를 띄우지 않는다.
- 기능의 답이 필요하면 `app.dependency_overrides[<기능의 service 모듈>.get_service]`에 `fakes.py`의 가짜를 끼운다. compare는 의존 함수 없이 `reports.get_report` 창구를 거치므로 `reports_service._service`를 바꾼다. 테스트는 구조 검사에서 빠지므로 기능 하위 모듈을 import해도 된다.
- 예기치 못한 예외의 503을 보려면 `TestClient(app, raise_server_exceptions=False)`로 만든다. 기본값이면 예외가 테스트 안으로 다시 올라온다.
- 주소 표는 `fakes.py`의 `SPEC_ROUTES`와 `test_app.py`의 순서 목록으로 고정돼 있다. HEAD는 Starlette가 `/openapi.json` 옆에 붙이는 것 하나뿐이다. 새 기능의 주소는 거기에 더하고 기존 항목은 바꾸지 않는다. 새 요청 본문 모델은 `/openapi.json` 스키마 이름 목록에 더한다(`PeerSearchBody`처럼). `feature_router`는 `web_router`만 있는 창구, `router`만 있는 창구, 둘 다 있는 창구(`web_router`가 이긴다), 실제 기능 창구들로 시험한다.
- `test_isolation.py`는 실제 기능 서비스로, DB 설정이 없을 때와 종목표가 없을 때 멈추는 기능과 멈추지 않는 기능, 원인을 고친 뒤 재시작 없는 회복, 응답에 경로가 없음을 본다. 새 기능은 자기 준비 조건을 여기에 더한다(유사 기업은 공개 빌드 없음과 검색의 `OPENAI_API_KEY` 없음까지, 상태 줄은 주가 창구의 `주가` 503).
- 꼭 지킬 검증: 허용 출처 넷과 쓰기 메서드(POST·PUT·PATCH·DELETE), 다른 호스트 400, 두 검사의 순서, `NotReady` 503(동기·비동기 핸들러·의존 함수)과 경고 한 줄, 일반 503과 트레이스백 로그와 비밀 미노출, health는 늘 200, 화면 파일·`no-cache`·빌드 안내 503·`index.html`을 요청마다 찾음, 기본 화면 폴더, `/openapi.json` 켜짐·docs 꺼짐, 앱을 만들 때 설정을 읽지 않음.
