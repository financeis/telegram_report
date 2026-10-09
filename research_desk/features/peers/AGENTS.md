# research_desk/features/peers/ — 사업보고서 기반 유사 기업: 연 1회 계산 명령과 유사 기업·테마 검색 주소

## 맡는 일

- 표 다섯 개와 DB 함수 두 개의 유일한 주인(마이그레이션 008): `company_profiles`(회사별 사업 요약 카드), `company_segments`(사업부문), `company_embeddings`·`segment_embeddings`(임베딩 모델별), `peer_builds`(빌드 기록과 백분위표·용어표), `match_company_profiles`·`match_company_segments`.
- 명령 `peers build [--fiscal-year N] [--codes …] [--limit n] [--pilot]`과 `peers inspect`(`jobs.py` → `build.py`).
- 웹 주소 `GET /api/stocks/{code}/peers`, `POST /api/peers/search`(`router.py` → `service.py`).
- 창구 이름은 둘뿐이다: `register_jobs`(= `jobs.register`)와 `web_router()`.
- 사업보고서 읽기(`dart.py`, MongoDB `FS.A001_v2`), 프로필 추출(`llm.py`·`prompts.py`·`schemas.py`), 계산(`logic.py`: 입력 조립·근거 검사·상한·임베딩 글·비교 열쇠·백분위·목록·반응·후보·RRF), 동의어표(`synonyms.yaml`), 설정(`settings.py`).

## 맡지 않는 일

- MongoDB·AI·DB 연결 만들기: `core.mongo`, `core.llm`(`parse`, `embed`), `core.db`로만 한다. pymongo·bson·openai·anthropic·supabase를 import하지 않는다(`R9 외부 도구`).
- 리포트 수는 `coverage.report_counts`, 주가는 `prices.snapshots`, 분류 진행 여부는 `reports.tagging_in_progress`로만 받는다. `reports` 표를 직접 읽지 않는다(`R11 표 주인`). 리포트 수 세기·주가 계산 규칙은 각 주인 기능의 것이고, 이 칸은 받은 값으로 반응·후보만 판정한다.
- `analysis.ai_slot()`·`analysis.phase2_llm()`. 질의 임베딩은 이 기능의 자리(`embed_slot`)로, 빌드는 자체 동시 수로 한다.
- MongoDB 쓰기와 DART 수집(별도 저장소). 이 칸은 사업보고서를 읽기만 한다.
- `freshness`·`compare`·`review`·`companies`·`analysis` import(이 칸이 기댈 이유가 없다), `collector`·`tagger`·`web`·`cli` import.

## 늘 지켜야 할 것

**창구.**
- `__init__.py`는 `register_jobs`와 `web_router()`만 묶는다. `web_router()`는 불릴 때 안에서 `from .router import router`를 한다. 창구 import나 `register_jobs` 호출로 FastAPI·starlette·uvicorn·라우터·서비스·빌드 모듈·리포트/커버리지/주가 창구·pymongo·numpy·AI SDK·`core.db`·`core.llm`이 올라오면 안 된다(`tests/test_window.py`와 입구 테스트가 확인). 그래서 `jobs.py` 최상위는 `core.settings`만 import하고, 나머지는 명령 함수 안에서 import한다.
- 리포트 창구는 `jobs.build_command` 안에서만 import한다. 그 창구가 FastAPI를 불러오므로 `peers build`는 웹 패키지가 있어야 돈다. `peers inspect`는 웹 패키지 없이 돈다.
- `web_router()`를 한 번 부르면 `research_desk.features.peers.router`는 하위 모듈이 된다. 라우터는 `web_router()`로만 받는다.

**`peers build`의 시작.**
- 준비 확인(4, 빌드를 시작하기 전, 이 순서): 설정 읽기(숫자 설정이 숫자가 아니면 `ValueError`로 1) → DB 설정 → 프로필 모델의 키 → 재처리 모델의 키(codex 모델이면 CLI) → `OPENAI_API_KEY` → MongoDB `ping` → 종목표 읽기와 버전 일치(분류 명령과 같은 문장) → 동의어표 → 그 회계연도의 `parser_version` 0.2.0 이상 문서. 그다음 리포트 창구의 `NotReady`는 그 문장 그대로 4.
- 분류 작업이 진행 중이면(`reports.tagging_in_progress()`) 고정 문장으로 1. 기다리면 풀리므로 4가 아니다.

**빌드 흐름(`build.py`).**
- 한 번에 하나: 마지막 진행이 6시간(`STALE_AFTER`, 정확히 6시간은 아직 진행 중) 이내인 `running` 빌드가 있으면 거절(1). 오래된 `running`은 `failed`로 바꾸고 시작한다.
- 대상(`dart.reports` → `--codes` → `--limit`, 종목코드 순) → 프로필(재사용 또는 추출, 동시 `PEERS_MAX_CONCURRENT_LLM`, 회사마다 `asyncio.timeout(PEERS_PER_COMPANY_TIMEOUT_S)`) → 임베딩(100개씩, `dimensions=1536`, 길이 1로 정규화, 호출 60초 한도, 일시 오류 1회 재시도) → 상태(`logic.build_status`) → `done`이면 용어 열 다시 쓰기·용어표·백분위표, `pilot`이면 이웃 표, `incomplete`이면 메시지 → 마감 → 보존 정리 → 요약 JSON.
- 회사 하나의 실패는 빌드를 멈추지 않는다. 그 밖의 오류와 Ctrl+C는 빌드를 `failed`로 닫고(`_record_failure`, 이미 닫혔으면 그대로) 1로 끝난다. 마감 뒤 보존 정리의 오류는 경고 한 줄뿐이고 종료 코드는 상태대로다.
- 마지막 진행 시각(`heartbeat_at`)은 회사 하나·임베딩 묶음 하나마다 갱신하되 `running`인 빌드에만 쓴다(`only_running`) — 이미 닫힌 빌드를 되살리지 않는다.
- 대상이 0곳이면 `failed`와 `대상 회사가 없습니다.`.

**프로필 저장.**
- 재사용: 같은 (회계연도, 프로필 버전)에 `ok` 행이 있고 `rcept_no`·`parser_version`이 같으면 추출하지 않는다(모델 이름은 보지 않는다).
- 교체는 추출이 성공했을 때만: `store.replace_profile`이 옛 행을 지우고(부문과 모든 임베딩 모델의 임베딩이 연쇄 삭제) 상태 없이 넣고 → 부문 → 마지막에 `ok`. 도중에 끊긴 상태 없는 행은 재사용도 화면 노출도 되지 않는다.
- 추출이 실패했는데 빌드 시작 때 `ok` 프로필이 있던 회사는 그 행을 건드리지 않는다(`kept_previous`에 적고 실패로 센다). 없던 회사만 `failed` 행을 쓴다.
- `profile`(JSON)은 근거 검사·상한 뒤의 원래 표기다. `terms`는 비교 열쇠 목록이다 — 추출 때 한 번 쓰고, 공개 빌드가 그 버전의 모든 `ok` 행을 다시 쓴다(그 열만 UPDATE, upsert로 바꾸지 않는다).
- 프롬프트(`prompts.py`)·응답 모양(`schemas.py`)·입력 조립·근거 검사·상한을 바꾸면 `settings.DEFAULT_PROFILE_VERSION`을 올린다. 안 올리면 재사용 조건 때문에 옛 프로필이 그대로 쓰여 한 빌드에 두 규칙이 섞인다.
- `CompanyProfile`·`Segment`에 개수·길이 상한을 넣지 않는다 — 검증 뒤 `logic.cap_profile`이 자른다. 스키마에 넣으면 긴 목록 하나로 그 회사가 실패한다. `Field(description=…)`은 모델이 받는 스키마이므로 프롬프트와 함께 고친다. null 대신 빈 목록과 -1을 쓴다.
- 프롬프트의 예시는 가상 회사 하나만 둔다. 실제 평가 대상 회사를 예시로 넣지 않는다.
- 프로필 추출은 `constrained=True`(Claude도 형식 강제)다. 거부는 실패, 형식 오류는 한 번 더 묻고, 일시 오류는 `call_with_retry`로 한 번 더. 근거율이 0.8(`GROUNDING_MIN`) 미만이면 재처리 모델로 한 번 더 추출해 그 결과를 쓰고(근거율과 상관없이), 재추출이 실패하면 첫 결과를 쓴다.

**임베딩.**
- 임베딩 모델마다 따로 저장하고 다른 모델의 행은 건드리지 않는다. 그 빌드 모델의 임베딩이 없는 `ok` 프로필·부문만 만든다 — 이번 대상만이 아니라 그 (회계연도, 프로필 버전) 전체다.
- 1536차원, 길이 1. 회사 임베딩 글에 고객사·경쟁사·그룹 이름을 넣지 않는다(`logic.company_text`).
- 벡터는 REST에서 글자(`"[…]"`)로 돌아온다 → `store.parse_vector`. 벡터 읽기는 200행씩(`VECTOR_PAGE`), 그 밖의 목록 읽기는 1000행씩, 코드 목록은 100개씩 나눠 읽는다.

**웹.**
- 서비스를 만들 때 아무것도 읽지 않는다. 처음 요청 때 DB → 종목표 순으로 준비하고 그 둘만 프로세스 동안 들고 있다(종목표 버전 문제는 경고만). 실패는 기억하지 않는다.
- 최신 공개 빌드는 요청마다 한 행으로 조회한다. 없으면 그 요청만 `NotReady("유사 기업", "아직 공개된 유사도 계산 결과가 없습니다(…)")`이고 기억하지 않는다. 빌드의 표(백분위표·용어표)와 테마 검색의 동의어표는 빌드 번호마다 한 번 읽어 둔다(그 사이 빌드 행이 사라졌으면 빈 표를 두지 않는다).
- 확인 순서: 형식 422(라우터의 FastAPI 검증) → 준비 503 → 공개 빌드 503 → 종목표에 없음 404 → 시드 프로필·회사 임베딩 없음 404 → 시드에 없는 부문 번호 422. 404·422 문장은 고정이고 화면(`frontend/src/peers/Peers.jsx`)이 문장을 그대로 비교한다 — 바꾸면 화면도 바꾼다.
- DB 함수는 시드 자신도 돌려주고 최대 200행이다. 호출자가 시드·종목표 밖·그 빌드에 `ok` 프로필이 없는 회사를 뺀다.
- 질의 임베딩: 캐시(최근 256개, (모델, 정규화한 질의)) → `.env` 다시 읽기 → `OPENAI_API_KEY` 없으면 `NotReady("유사 기업", "OPENAI_API_KEY가 설정되지 않았습니다")`(검색 주소만) → `embed_slot()`(이벤트 루프마다 세마포어 2) 안에서 15초 한도, 클라이언트는 호출마다 만들고 닫는다. 시간 초과·거부는 그 요청의 일반 오류이지 `NotReady`가 아니다.
- 다른 기능의 `NotReady`(`리포트`·`커버리지`·`주가`)는 잡지 않고 올린다. 주가 표가 비어 있으면 `price: null`이다.
- 동기 DB 일은 `def` 핸들러(피어 목록)나 `asyncio.to_thread`(검색) 안에서 한다.
- 핸들러 이름(`stock_peers`, `peers_search`)과 본문 모델 이름 `PeerSearchBody`는 `/openapi.json`과 웹 조립 테스트가 고정한다.

## 이 칸의 방식

| 파일 | 역할 |
|---|---|
| `jobs.py` | 명령 등록·인자·준비 확인·`peers inspect`. 연결 함수 `_supabase`·`_mongo_ping`·`_mongo_collection`·`_llm_client`를 테스트가 바꿔 끼운다 |
| `build.py` | 빌드 흐름: `Job`(빌드가 쓰는 모든 것), `run_build`, `execute`(종료 코드) |
| `dart.py` | MongoDB 읽기, 회계연도 선택·대상 회사 규칙, 섹션 글 모으기(`parser_version` 0.2.0 이상만) |
| `llm.py` | 회사 하나의 추출(형식 오류 1회 재질문, 근거율 미달 1회 재추출) |
| `prompts.py`, `schemas.py` | 추출 규칙 글과 응답 모양 |
| `logic.py` | 순수 계산(numpy). 조정 가능한 숫자는 모두 여기 상수다 |
| `store.py` | 다섯 표와 DB 함수. supabase-py 클라이언트 하나를 감싼다 |
| `service.py`, `router.py` | 웹 쪽(라우터는 `web_router()`로만 나간다) |
| `settings.py`, `synonyms.yaml` | 설정과 동의어표 |

- 조정 가능한 기본값(백분위 기준, ρ 0.25·0.6, 거래대금 5억, 상위 100·50, 공통 키워드 5, 95%, 6시간, 보존 3, 입력 상한들)은 `logic.py` 상수다. 시드 10%p 문턱, 후보 조건 여섯 가지, 1536차원, 증권사 종목 리포트만 센다는 기준은 사용자가 정한 값이라 바꾸지 않는다. 애매하면 후보로 띄우지 않는 쪽으로 판정한다.
- 동의어표 규칙: 한 표기는 표준 하나에만 속하고, 다른 항목의 표준 표기를 다른 표기로 적으면 안 된다(어기면 `parse_synonyms`의 `ValueError` → 명령은 4). 낱말 안의 일부는 바꾸지 않는다.
- 새 읽기는 `PeersStore` 메서드로 더하고, 목록 읽기는 정해진 정렬과 쪽 나누기를 둔다(1000행 한도).

## 테스트

- 실행: 저장소 루트에서 `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider research_desk/features/peers`.
- `tests/conftest.py`(autouse)가 이 기능이 읽을 수 있는 설정 변수를 모두 지우고, 실제 Supabase·MongoDB·AI 클라이언트를 만들려 하면 실패하게 한다.
- `tests/fakes.py`: `FakeSupabase`(다섯 표의 키·NOT NULL·CHECK·외래 키 연쇄 삭제·1536차원, 벡터를 글자로 돌려줌, `ov`는 PostgreSQL 배열 글자를 풀어 비교, 두 DB 함수 흉내, 모든 작업 기록; 같은 쿼리에 `range()`를 다시 부르면 마지막 창을 쓴다), `FakeCollection`(pymongo 컬렉션), `FakeLLM`(`parse`·`embed`, 동시 호출 최대 수 기록). `tests/web_world.py`: 코사인을 정해 둔 벡터와 백분위표로 된 작은 세계와 기대 목록.
- 꼭 덮을 것: 입력 조립의 순서·상한·표 거르기·짧은 개요 보충·금융업 양식, 근거 검사와 0.8 경계의 재추출 1회, 회계연도 선택(3월 결산)과 대상 규칙(코넥스·스팩·리츠·종목표 밖), 재사용 세 조건과 실패 회사만 다시, `kept_previous`, 상태 전이(`done`/`incomplete` 95% 경계/`pilot`은 용어 열 불변), 6시간 경계, 보존, 분류 진행 중 거절(문장·1), 준비 문제 4의 원인마다, 백분위 보간(95 미만 없음, 100 초과는 100)과 등급 경계, 비교 열쇠(동의어·대소문자·공백·가운뎃점·하이픈, 낱말 안은 그대로), 모델별 임베딩 공존, 오류·중단 때 `failed`와 1, 피어 주소의 확인 순서, 새 `done` 빌드가 재시작 없이 다음 요청에 반영, RRF와 질의 용어의 완전 일치, 캐시, 검색 주소만의 키 없음 503, 창구 import가 무거운 것을 부르지 않음.
