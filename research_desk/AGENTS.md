# research_desk/ — 명령어 입구, 공용 테스트 준비, 구조 규칙 검사

## 맡는 일

- 명령어 입구 `__main__.py`·`cli.py`. 모든 실행은 `python -m research_desk <명령>` 하나이고, 명령 목록은 `cli.py`의 `build_parser` 한 곳뿐이다. 여기서 각 칸의 등록 함수(`collector.cli.register`, `tagger.cli.register`, `web.server.register`)와 `register_stocks`를 차례로 부른다.
- 종료 코드 약속과 `stocks set-version --as-of YYYY-MM-DD` 명령. 이 명령은 `.env`를 다시 읽고 `KRX_CSV_PATH`를 받아 `domain.stocks.write_version`을 부르는 연결만 한다. 입구에 있는 이유는 `domain`이 설정을 읽지 않고(경로를 인자로 받는다) 명령도 두지 않기 때문이다.
- 공용 테스트 준비 `conftest.py`: 모든 테스트에서 `.env` 읽기를 끄고, 필요한 테스트만 `env_file` fixture로 켠다.
- 구조 규칙 검사 `tests/architecture_rules.py`(규칙 표와 판정 방법), `tests/test_architecture.py`(실제 검사와 검사기 자체 테스트), 입구 테스트 `tests/test_cli.py`.

## 맡지 않는 일

- 명령의 실제 동작. `collect`는 `research_desk.collector`, `tag`는 `research_desk.tagger`, `web`은 `research_desk.web`이 맡는다. `cli.py`에는 등록 한 줄만 더한다.
- 웹 기능 등록 목록은 `web/app.py`의 `FEATURES`다. 설정 읽는 방식은 `core.settings`, 종목표 지문과 버전 정보 파일 규칙은 `domain.stocks`가 정한다. 입구는 부르기만 한다.
- DB 표(`reports`, `failed_attempts`, `report_summaries`). 입구는 어느 표의 주인도 아니어서, `cli.py`에 `.table('…')`나 `FROM`/`UPDATE`/`INTO`/`JOIN <표>` 문자열을 두면 `R11 표 주인`으로 잡힌다.
- 외부 도구. 입구도 예외가 아니다. `supabase`·`asyncpg`·`openai`·`anthropic`·`dotenv`·`fitz`·`pymupdf`는 `core`, `telethon`은 `collector`, `langgraph`는 `tagger` 안에서만 import한다.
- 기능의 하위 모듈. 입구도 기능은 공개 창구(`research_desk.features.<기능>` 자체나 그 `__init__.py`가 직접 묶은 이름)로만 쓴다. `research_desk.features.<기능>.<하위 모듈>`을 직접 import하지 않는다.
- 패키지 뿌리 파일(`research_desk/__init__.py`, `features/__init__.py`)은 research_desk 모듈을 하나도 import하지 않는다. 여러 칸을 묶는 곳은 `cli.py`·`__main__.py`뿐이고, 테스트가 아닌 다른 파일은 `cli`·`__main__`을 import하지 않는다.

## 늘 지켜야 할 것

종료 코드. `main(argv)`은 명령 함수 `func(args)`의 반환값을 그대로 돌려주고, `__main__`이 `sys.exit(main())`으로 프로세스 종료 코드로 만든다.
- `0`: 정상. `--help`는 모든 명령과 하위 명령에서 0이다.
- `2`: 사용법 오류(명령 없음, 모르는 명령·옵션, 필수 인자 없음, `--view bad`나 숫자 자리의 글자처럼 argparse가 거르는 값). argparse가 stderr에 `usage: research_desk …`를 찍고, stdout은 비어 있고, 아무 명령도 돌지 않는다.
- `4`: 준비 문제. 다시 돌려도 저절로 풀리지 않는 상태다(설정 누락, codex CLI 없음, 종목표를 못 읽거나 버전 정보와 다름, 잘못된 `--as-of` 날짜). 이유를 stderr에 찍고 아무 일도 하지 않은 채 끝난다. `tag`는 행을 하나도 가져가지 않고, `set-version`은 버전 정보 파일을 건드리지 않는다. `tag`의 모든 하위 명령, `stocks set-version`, 앞으로 기능이 붙이는 명령이 쓴다.
  - `scripts/run-batches.ps1`은 4를 보면 되돌리기·재시도 없이 곧바로 4로 멈춘다. 준비 문제를 1로 내면 되돌리기와 재시도를 두 번 더 하고서야 멈춘다.
- `1`: 그 밖의 오류. `tag`·`stocks`는 예상하지 못한 예외를 잡지 않고 흘려보내고, 파이썬이 traceback과 함께 1로 끝낸다. `main()`에 예외를 통째로 잡는 처리를 넣지 않는다.
- `collect`만 다르다. 설정 누락도 1이고 stderr 문구는 `Config error: Missing required env var: <이름>`이다(예전 동작 유지). 4로 바꾸지 않는다.

`stocks set-version`.
- 날짜는 argparse가 아니라 명령이 확인한다. 값이 틀리면(`2026-13-01`, `20260630`, `2026-02-30`, `2026-6-30`, 빈 문자열 등) 4다. `--as-of`에 `type=` 검사기를 붙이면 2로 바뀌어 약속이 깨진다. `--as-of`가 아예 없거나 값이 없을 때만 사용법 오류 2다.
- 성공: stdout에 `KRX@<날짜>` 한 줄, stderr는 비움, 0. 같은 날짜로 다시 써도 성공이다. 버전 이름이 "자료 기준일"이라는 뜻이기 때문이다.
- 실패: stdout은 비우고 stderr에 `종목표 버전 정보를 쓰지 못했습니다. 버전 정보 파일은 그대로입니다. 이유: <이유>` 한 줄, 4. 버전 정보 파일은 한 바이트도 바뀌지 않고, 없었으면 계속 없다. 이유에는 파일 경로가 들어가도 된다. 명령 출력은 이 PC 터미널에서만 보이기 때문이다(웹 응답에 경로를 넣지 않는 것과 다르다).
- 시작할 때 `.env`를 다시 읽는다. `KRX_CSV_PATH`가 없으면 현재 폴더 기준 기본 경로를 쓴다.

명령 공통.
- 저장소 루트에서 실행한다. `sessions/`와 `.env`의 상대 경로(`STORAGE_BASE_DIR`, `KRX_CSV_PATH`)는 현재 폴더 기준이다. `.env` 파일 자체는 현재 폴더와 상관없이 찾는다.
- 도움말은 `usage: research_desk`로 시작하고 명령 목록은 `{collect,tag,web,stocks}` 순서다(테스트가 고정한다).
- 무거운 import는 명령이 실제로 돌 때만 한다. `cli.py`는 import 시점에 모든 칸의 등록 모듈(`collector.cli`, `tagger.cli`, `web.server`)을 불러오므로, 명령 없이 실행하거나 `--help`만 해도 이 모듈들이 로드된다. 등록 모듈 맨 위에서 FastAPI·uvicorn·`research_desk.web.app`·분류 그래프(`research_desk.tagger.orchestrator`)·LangGraph를 import하면 모든 명령이 느려지고 LangGraph 경고가 stderr에 찍힌다. 명령 함수 안에서 import한다(`web.server.serve`와 `tagger.cli.run_batch`가 그렇게 한다).
- 새 사용자 문구는 한국어로 쓴다. 기존 영어 출력 문구(`<이름> is required`, `Config error: …`)는 바꾸지 않는다.

## 이 칸의 방식

명령 추가.
- 칸마다 `register(subparsers)`가 자기 파서를 더하고 `set_defaults(func=…)`로 `func(args) -> int`를 건다. 새 명령은 그 칸의 등록 함수와 `build_parser`의 한 줄이다. 하위 명령 묶음은 `add_subparsers(..., required=True)`로 만들어, 하위 명령 없이 부르면 2가 나게 한다.
- 기능이 백그라운드 작업 명령을 붙일 때는 그 기능의 `jobs.py`에 등록 함수를 두고 `__init__.py`에서 이름으로 내보낸 뒤(`from .jobs import register as register_jobs`), `cli.py`에서 `from research_desk.features.<기능> import register_jobs`로 쓴다. 단 지금 기능 창구는 모두 FastAPI를 함께 불러오므로(`router`, 또는 `HTTPException`을 쓰는 `service`), 그대로 붙이면 모든 명령이 FastAPI를 불러와 `tests/test_cli.py`의 "웹 패키지를 불러오지 않는다" 확인이 실패한다. 첫 기능 명령을 붙일 때 두 조건을 함께 지킬 방식을 사용자와 정하고, 그 확인을 지우거나 느슨하게 하지 않는다.
- 명령 함수는 시작하자마자 `core.settings.load_env()`를 부른다. 준비 확인(설정·파일·연결 설정)은 일을 시작하기 전에 다 끝내고, 실패하면 이유를 stderr에 한 줄 찍고 4를 돌려준다.
- 정해진 시간에 도는 작업도 `python -m research_desk <명령>`으로 만든다. 예약 실행은 윈도우 작업 스케줄러가, 저장소 폴더를 현재 폴더로 맞추는 `scripts/`의 `.ps1`을 부르는 방식이다.

구조 규칙 검사.
- `research_desk/` 아래 모든 `.py`를 실행하지 않고 AST로 읽는다. `__pycache__`와 점으로 시작하는 폴더는 건너뛰고 `research_desk/` 밖은 보지 않는다. 함수 안·`TYPE_CHECKING` 안의 import도 import로 세고, 상대 import는 절대 이름으로 풀어서 본다.
- 칸은 경로로 정한다. `core/`·`domain/`·`collector/`·`tagger/`·`web/` 아래는 그 칸, `features/<기능>/` 아래는 그 기능, `cli.py`·`__main__.py`는 입구, 그 밖은 패키지 뿌리다. `tests/` 폴더 안 파일과 `conftest.py`는 테스트로 보아 옛 코드 금지만 적용한다.
- 실패 줄에 찍히는 이름과 그 뜻:
  - `R1 core`: core는 `research_desk.core` 밖의 research_desk 모듈을 import하지 않는다.
  - `R2 domain`: domain은 core·domain만 import한다.
  - `R3 collector`, `R4 tagger`: 각각 core·domain·자기 칸만 import한다.
  - `R5 기능`: 기능은 core·domain·자기 폴더, 그리고 다른 기능의 공개 창구만 import한다. collector·tagger·web·입구는 import하지 않는다.
  - `R6 순환 금지`: 기능 사이의 import(테스트 제외)가 돌고 돌면 안 된다.
  - `R7 web`: web은 core·domain·자기 칸·기능의 공개 창구만 import한다.
  - `R8 입구`: 여러 칸을 묶는 것은 입구뿐이다(하위 모듈까지 가능하지만 기능은 공개 창구로만). 다른 파일은 입구를 import하지 않고, 패키지 뿌리 파일은 research_desk 모듈을 import하지 않는다.
  - `R9 외부 도구`: `supabase`·`asyncpg`·`openai`·`anthropic`·`dotenv`·`fitz`·`pymupdf`는 core, `telethon`은 collector, `langgraph`는 tagger에서만 import한다.
  - `R10 옛 코드`: `langgraph_tagger`와 예전 루트 모듈(최상위 이름 `collector`·`config`·`storage`·`telegram_client`·`main`)을 import하지 않는다. 테스트에도 적용한다.
  - `R11 표 주인`: `.table('<표>')` 호출(supabase-py 별칭 `.from_('<표>')` 포함)과, docstring이 아닌 문자열 속 `FROM`/`UPDATE`/`INTO`/`JOIN <표>`(대소문자 무시, 앞에 `public.`이나 큰따옴표가 붙어도)는 주인 칸에만 둔다. `reports` → collector·tagger·`features/review`·`features/reports`, `failed_attempts` → collector, `report_summaries` → `features/analysis`.
  - `구문 오류`: 파이썬으로 읽히지 않는 파일은 검사할 수 없어 실패로 친다.
- 실패하면 `test_research_desk_follows_architecture_rules` 하나가 실패하고, 위반 건수를 적은 머리줄 다음에 위반마다 `<파일>:<줄> — <규칙 이름>: <설명>` 한 줄이 파일·줄 순서로 나온다. 예:
  ```
  research_desk/features/coverage/service.py:12 — R5 기능: 다른 기능은 공개 창구 `research_desk.features.reports`로만 쓴다. 하위 모듈 직접 import 금지: `research_desk.features.reports.store`
  ```
  기능 순환은 순환마다 한 줄이고, 고리를 이루는 import 자리(`a→b: 파일:줄`)를 모두 적는다.
- 우회하지 말고 이렇게 고친다.
  - 다른 기능의 하위 모듈을 썼다 → 그 기능 `__init__.py` 최상위에서 이름을 직접 묶고(`from .service import f`, `from . import store`) 그 이름으로 쓴다. `__all__`, `import *`, 하위 모듈 import의 부수효과로 생긴 이름은 공개 이름으로 치지 않는다.
  - 주인이 아닌 칸에서 표를 다뤘다 → 주인 칸에 함수를 두고 그 칸의 공개 창구로 부른다(`compare`·`coverage`는 `research_desk.features.reports`로 리포트를 읽는다).
  - 외부 도구를 다른 칸에서 썼다 → 그 일을 맡는 칸의 함수로 옮기고 그 함수를 부른다.
  - 기능끼리 순환한다 → 같이 쓰는 것을 의존받는 쪽 기능이나 domain·core로 내린다. 지금 방향은 `reports`→`analysis`, `compare`→`reports`·`analysis`, `coverage`→`reports`, `review`→`coverage`이고, `companies`·`analysis`는 다른 기능에 기대지 않는다.
  - 분류기와 웹 기능이 같이 써야 하는 규칙이다 → `domain`에 둔다.
  - 새 최상위 폴더(`research_desk/utils/` 같은)나 `features/` 바로 아래의 파일(`features/common.py` 같은)은 패키지 뿌리로 판정돼 research_desk import가 전부 `R8 입구`로 잡힌다. 새 코드는 기존 칸이나 `features/<새 기능>/` 폴더에 둔다.
- 하면 안 되는 우회:
  - `architecture_rules.py`의 표(`ALLOWED_AREAS`, `EXTERNAL_TOOL_AREAS`, `OLD_MODULES`, `TABLE_OWNERS`)에서 기존 경계를 풀어 통과시키기. 이 표가 사용자가 승인한 경계이고, 푸는 것은 설계 변경이라 사용자가 정한다.
  - `importlib`·`__import__` 같은 동적 import로 돌아가기. 검사가 보지 못할 뿐 허용된 것이 아니다.
  - 코드를 `tests/` 폴더나 `conftest.py`로 옮겨 검사를 피하기.
- 반대로 표에 새 항목을 더하는 것은 해야 하는 일이다. 새 DB 표를 만들면 그 주인 칸과 함께 `TABLE_OWNERS`에 넣는다(목록에 없는 표는 검사가 지키지 않는다). 새 DB·AI·PDF 라이브러리는 core에 두고 `EXTERNAL_TOOL_AREAS`에 넣는다.

커밋 검사.
- `.githooks/pre-commit`과 `pre-merge-commit`이 `run-checks.sh`로 구조 검사를 포함한 전체 테스트(`python -m pytest -q -p no:cacheprovider`)를 돌린다. 파이썬은 기본 작업 폴더(`git rev-parse --git-common-dir`의 상위)의 `.venv`에서 찾으므로 linked worktree도 같은 파이썬을 쓰고, 테스트는 지금 작업 폴더의 맨 위에서 돈다. 테스트가 하나라도 실패하거나 파이썬이 없으면 커밋이 막힌다(`구조 규칙 검사 또는 테스트가 실패해서 커밋을 막았습니다. …`).
- `--no-verify`로 건너뛰지 않는다. 원인을 고칠 수 없으면 사용자에게 묻는다. 새로 복제한 저장소는 `git config core.hooksPath .githooks`로 켠다.

## 테스트

- 전체 실행은 저장소 루트에서 `python -m pytest -q -p no:cacheprovider`(커밋 검사와 같다). `pytest.ini`가 `testpaths = research_desk`, `asyncio_mode = auto`라서 인자 없이 전부 모이고 async 테스트에 표시가 필요 없다. `research_desk/` 밖에 둔 테스트는 모이지 않는다.
- `.pytest_cache/`는 지우거나 정리하지 않는다. 사용자 작업 파일이 들어 있다. 커밋 검사처럼 캐시를 끄고 돌린다.
- Windows에서 출력을 파이프로 받을 때는 `PYTHONIOENCODING=utf-8`을 준다. 안 주면 949 코드 페이지에 없는 `—` 때문에 pytest가 구조 검사 메시지 줄 전체를 `\uXXXX`로 찍는다.
- 테스트는 칸마다 자기 `tests/` 폴더에 두고 빈 `__init__.py`를 함께 둔다. 같은 이름의 테스트 파일(`test_cli.py`, `test_settings.py` 등)이 여러 칸에 있어서, 패키지가 아니면 pytest가 모듈 이름 충돌로 수집 전체를 멈춘다.
- 동작을 바꾸는 변경은 무엇을 바꿨는지 밝히고 테스트를 그에 맞게 고친다. 테스트를 지워서 통과시키지 않는다.

`.env`와 바깥 상태.
- `conftest.py`의 자동 fixture가 모든 테스트에서 `core.settings.ENV_FILE_ENABLED = False`, `ENV_FILE_PATH = None`으로 둔다. 함수를 바꿔치기하지 않고 `load_env()` 안의 스위치를 끄는 방식이라, `from research_desk.core.settings import load_env`로 이름을 가져간 모듈도 따르고, 경로를 직접 준 `load_env(path)`도 아무것도 읽지 않는다. 이 방식을 `load_env` 함수 monkeypatch로 바꾸지 않는다.
- `.env` 동작을 시험할 때만 `env_file` fixture를 쓴다. 비어 있는 임시 `.env`의 경로를 주고 `load_env()`가 그 파일을 읽게 한다. 줄을 써 넣은 뒤 대상 코드를 부른다. `load_env()`는 `os.environ`에 직접 쓰므로, 테스트 동안 새로 생긴 변수는 fixture가 끝날 때 지우고 바뀐 값은 되돌린다.
- `.env`는 이미 있는 환경 변수를 덮지 못한다. 그래서 임시 `.env`에서 값을 받아야 하는 테스트는 그 변수를 먼저 `monkeypatch.delenv`로 지운다(개발 PC의 환경 변수나 앞 테스트가 남긴 값이 있을 수 있다).
- 실제 `.env`, `frontend/dist`, `~/.review_viewer/favorites.json`, DB·AI·텔레그램에 닿는 테스트를 만들지 않는다. 별도 작업 폴더에서 돌리든 기본 작업 폴더에서 돌리든 결과가 같아야 한다.

입구 테스트(`tests/test_cli.py`).
- 자동 fixture `clean_env`가 명령이 읽을 수 있는 설정(`ENV` 목록)을 모두 지우고, 현재 폴더를 임시 폴더로 바꿔 상대 경로 기본값(`./reports`, 종목표 기본 경로, `sessions/`)이 실제 파일에 닿지 않게 하고, DB 풀·Supabase 클라이언트·텔레그램 클라이언트를 거절하는 가짜로 바꾼다. 명령이 새 설정을 읽게 되면 그 이름을 `ENV`에 더한다.
- `web`은 `uvicorn.run`을 바꿔 서버를 띄우지 않고, `web.app.DEFAULT_DIST`를 없는 임시 폴더로 돌려 실제 화면 파일을 읽지 않는다.
- 새 프로세스로 `python -m research_desk`를 띄우는 테스트(`run_python`)는 저장소 폴더에서 돈다. 그 프로세스는 pytest 밖이라 `.env` 차단이 걸리지 않으므로, 명령 함수가 돌기 전에 끝나는 경우(`--help`, 명령 없음)만 이렇게 시험한다. 실제 명령을 새 프로세스로 돌리는 테스트는 만들지 않는다(실제 `.env`를 읽고 실제 DB·AI·텔레그램에 닿는다).
- 명령을 더하거나 바꾸면 함께 고친다: `--help`가 0인지 보는 `COMMANDS`, 명령 목록을 고정한 `test_the_help_lists_every_command`, 사용법 오류 표 `USAGE_ERRORS`, `-X importtime`으로 무거운 패키지가 로드되지 않는지 보는 테스트.
- `set-version` 실패 테스트는 버전 정보 파일을 실패 전후 바이트로 비교하고, 폴더의 파일 이름 목록으로 임시 파일이 남지 않았는지 본다. 이 확인을 느슨하게 바꾸지 않는다.

구조 검사 자체 테스트.
- `make_tree`로 `tmp_path`에 가짜 `research_desk`를 만들고, 위반이 정확히 기대한 `(파일, 줄, 규칙)` 집합으로 잡히는지 본다. 판정을 바꾸면 잡혀야 하는 경우와 통과해야 하는 경우를 둘 다 더한다. `CLEAN_TREE`는 실제 구조에서 허용되는 모양을 모은 것이라 어떤 규칙 변경 뒤에도 위반이 없어야 한다.
