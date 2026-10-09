# 0010 — 명령이 있는 기능의 창구는 FastAPI 없는 이름만 묶고, 웹 주소는 `web_router()`로 넘긴다

## 배경
`python -m research_desk`는 어느 명령이든(명령 없이, `--help`만 해도) `cli.py`가 모든 등록 함수를 불러 명령 목록을 만든다. 기능의 명령도 `cli.py`가 그 기능의 공개 창구(`__init__.py`)를 import해서 붙여야 한다(입구도 기능은 창구로만 쓴다). 그런데 기존 기능 창구는 모두 FastAPI를 함께 불러왔다(`router`, 또는 `HTTPException`을 쓰는 `service`). 웹 패키지는 `requirements-workspace.txt`에만 있고, 입구 테스트가 "명령 없이 실행하면 FastAPI·uvicorn·웹 앱을 불러오지 않는다"를 고정한다. 첫 기능 명령(`prices update`, `peers build`)을 붙이자 두 조건이 부딪혔다.

## 결정
사용자 선택: 기능 하나는 폴더 하나로 둔다.
- 명령이 있는 기능의 창구는 FastAPI를 불러오지 않는 이름만 묶는다: `register_jobs`와 다른 기능이 쓸 함수(주가의 `snapshots`·`latest_run` 같은).
- 그 기능에 웹 주소가 있으면 창구에 `web_router()`를 둔다. 이 함수는 불릴 때 안에서 `from .router import router`를 하고 그 라우터를 돌려준다.
- `research_desk/web/app.py`의 `feature_router(feature)`가 `web_router`가 있으면 그것을 부르고, 없으면 `feature.router`를 쓴다. `create_app()`이 모든 기능을 이것으로 붙인다.
- 명령 함수는 무거운 것(DB·MongoDB·AI 클라이언트, 다른 기능의 창구)을 실행될 때 함수 안에서 import한다.
- 인자 없는 `python -m research_desk`가 불러오지 않는 목록: `fastapi`, `uvicorn`, `research_desk.web.app`, `langgraph`, `pymongo`, `research_desk.features.peers.router`, `research_desk.features.peers.service`. `research_desk/tests/test_cli.py`가 새 프로세스로 확인한다.

## 버린 대안
- **기능을 작업용·화면용 두 폴더로 나누기**: "새 기능 = 폴더 하나" 원칙이 깨지고, 앞으로 일괄 작업이 있는 기능마다 폴더가 둘이 된다. 두 폴더가 같은 표를 다루면 표 주인도 둘로 갈린다.
- **`cli.py`에서 창구 import를 함수 안으로 늦추기**: 명령 목록과 도움말을 만들려면 `build_parser()`가 어느 명령에서든 모든 등록 함수를 불러야 한다. import 자리를 옮겨도 `--help`와 모든 명령에서 결국 창구가 실행되므로, 창구가 FastAPI를 부르는 한 소용이 없다.
- **웹 패키지를 모든 명령의 필수 설치로 바꾸기**: `collect`·`tag`가 웹 패키지 없이 가볍게 시작하던 약속이 깨지고, 모든 명령이 FastAPI를 불러오게 된다.

## 결과
- 명령이 있는 기능은 창구 최상위에서 `router`를 묶을 수 없다. 다른 기능에 내놓는 함수도 FastAPI를 부르지 않는 모듈에 있어야 한다(주가 기능의 `service.py`는 FastAPI를 쓰지 않는다).
- `web_router()`를 한 번 부르면 파이썬이 하위 모듈을 패키지 속성으로 붙이므로, 그 뒤 `peers.router`는 라우터가 아니라 `router.py` 모듈이다. 그래서 `feature_router`가 `web_router`를 먼저 본다. 이런 기능에 `feature.router`를 직접 쓰지 않는다.
- 명령 함수 안의 import는 막지 않으므로, 명령이 실행될 때는 웹 패키지가 필요할 수 있다. `peers build`는 분류 진행 확인을 리포트 기능 창구로 해야 해서(리포트 창구가 FastAPI를 부른다) 웹 패키지가 설치돼 있어야 한다. `collect`·`tag`·`stocks`·`prices`·`peers inspect`는 웹 패키지 없이 돈다.
- 무거운 의존을 창구가 부르는 모듈 최상위에 넣으면 입구 테스트가 실패해 커밋이 막힌다.
