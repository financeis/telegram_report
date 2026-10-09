# 알아 둘 것

## 디버깅하지 말아야 할 무해한 경고

| 보이는 것 | 원인 | 대응 |
|---|---|---|
| Pydantic serializer warning (`LLMExtraction`·`CompanyProfile` 직렬화 시) | 분류기와 유사 기업 프로필의 LLM 응답 모델을 직렬화할 때 Pydantic이 내는 경고 | 기능 영향 없음(분류 결과·프로필과 저장 값이 같다). 무시한다 |
| `Failed to send compressed multipart ingest: … LangSmithRateLimitError … Monthly unique traces usage limit exceeded` (429) | AI 호출 기록 서비스(LangSmith)의 월 기록 한도를 다 써서 기록 전송이 거절됨. AI 호출 자체와 결과는 그대로다(2026-10-10 유사 기업 시험 실행에서 처음 봄) | 무시해도 된다. 다만 그달에는 LangSmith에 토큰 추이가 남지 않아 동시 호출 수를 올릴지 판단할 자료가 없다. 큰 실행에서 경고가 거슬리면 그 창에서만 `$env:LANGSMITH_TRACING='false'`를 두고 돌린다(`.env`보다 앞선다) |
| 반복 실행 중 `exit=-1073741569`(드물게 `-1073741784`) (Windows native crash) | 윈도우에서 파이썬 프로세스가 비정상 종료된 경우. 2026-10-09 백필에서 자주 났고, 분류기가 PDF를 두 스레드에서 동시에 읽은 것(PyMuPDF는 여러 스레드 동시 사용을 지원하지 않는다)이 원인으로 추정돼 2026-10-10에 `core.pdf`의 PyMuPDF 호출을 프로세스 전체 잠금으로 줄 세웠고, 그 뒤 크게 줄었다 | `run-batches.ps1`이 그 작업자의 행을 되돌리고 재시도해서 자동 복구한다(데이터 손실 없음). 한 배치가 3번 연속 나면 스크립트가 1로 멈춘다 — 그때는 사람이 본다 |
| `LangChainPendingDeprecationWarning: The default value of allowed_objects will change…` | LangGraph가 import될 때 내는 예고 경고. `tag run`·`tag escalate`가 그래프를 불러올 때와, 테스트 실행 끝의 "1 warning"이 이것이다 | 무시한다. `collect`·`web`·`stocks`·`prices`·`peers`·`tag inspect`·`tag reset-worker`·`tag requeue`·`--help`에서 이 경고가 보이면 그쪽은 무해한 것이 아니다 — 누가 명령 입구에서 그래프를 일찍 import하게 만든 것이니 그 import를 함수 안으로 옮긴다 |
| `KIS … retry 1/3 in 1s` (`prices update` 중 경고) | KIS가 한도 초과(429·`EGW00201`)·5xx를 돌려주거나 시간 초과가 나서 1·2·4초 간격으로 다시 묻는 중 | 가끔이면 무시한다. 같은 실행에서 계속 나오면 같은 앱키를 쓰는 masterdb 수집이 돌고 있는지, `PRICES_MAX_CALLS_PER_SEC`가 너무 높지 않은지 본다 |

## 함정

- **작업 폴더의 CSV는 CRLF, 저장소에는 LF.** 이 저장소는 `core.autocrlf=true`라 git이 체크아웃할 때 줄바꿈을 CRLF로 바꾼다. 그래서 종목표 지문은 파일 바이트가 아니라 CSV로 해석한 칸 값으로 계산한다. 지문 계산을 "파일 바이트의 해시"로 바꾸면 작업 폴더마다 지문이 달라져 분류 명령이 모든 PC·작업 폴더에서 종료 코드 4로 멈춘다.
- **PowerShell 5.1은 BOM 없는 스크립트를 코드 페이지 949로 읽는다.** 한국어 문구가 깨지고(`以鍮?臾몄젣…`), 문자열 해석까지 틀어질 수 있다. 한국어가 들어간 `.ps1`을 편집 도구로 저장할 때 BOM이 빠지지 않았는지 첫 3바이트(`EF BB BF`)로 확인한다.
- **파이프로 받은 pytest 출력에서 한국어가 `\uXXXX`로 보인다.** 파이썬이 파이프 출력을 코드 페이지 949로 쓰는데, 구조 검사 메시지의 `—`(em dash)는 949에 없어서 pytest가 그 줄 전체를 이스케이프한다. 커밋 검사 스크립트는 `PYTHONIOENCODING=utf-8`로 pytest를 돌려 이 문제를 피한다. 직접 pytest를 파이프로 돌려 결과를 읽을 때도 `PYTHONIOENCODING=utf-8`을 붙인다.
- **`.githooks/` 스크립트를 CRLF로 저장하면 커밋 검사가 sh 오류로 깨진다.** `.githooks/.gitattributes`(`* text eol=lf`)가 체크아웃 때 LF를 강제한다. 새 훅 파일도 이 폴더에 두면 같은 규칙을 받는다.
- **FastAPI 0.141은 `include_router`한 라우터를 `app.routes`에 한 덩어리로 넣는다.** `app.routes`를 그대로 훑으면 기능의 주소가 보이지 않는다. 주소 목록이 필요하면 `fastapi.routing.iter_route_contexts`로 펼친다(`research_desk/web/tests`가 이렇게 한다).
- **`load_env()`는 이미 있는 환경 변수를 덮어쓰지 않는다.** `.env`에 빠진 값을 새로 넣으면 다음 준비(웹 요청, 명령 시작)에 반영되지만, 이미 있던 값을 바꾼 것은 프로세스를 다시 켜야 반영된다. 웹앱에서 키를 바꿨는데 "분석 기능 사용 불가"가 그대로면 웹앱을 다시 켠다.
- **테스트에서 `.env` 읽기를 끄는 장치는 `core.settings` 안의 스위치다.** `from research_desk.core.settings import load_env`로 가져간 모듈은 함수 바꿔치기(monkeypatch)를 피해 가기 때문에, 함수를 바꿔치는 방식으로는 실제 `.env`가 읽힌다. 새 테스트에서 `.env`를 다뤄야 하면 `research_desk/conftest.py`의 `env_file` 준비를 쓴다.
- **Supabase 트랜잭션 풀러(6543 포트)는 이름 붙은 prepared statement를 연결 사이에 유지하지 않는다.** asyncpg의 statement cache를 켜면 "prepared statement does not exist" 류 오류가 난다. `core.db.create_pool`이 `statement_cache_size=0`으로 연다. 직접 연결 풀을 따로 만들지 말고 이 함수를 쓴다.
- **Supabase REST는 한 요청에 최대 1000행만 돌려준다.** 전부 읽어야 하는 조회(수집의 이미 받은 번호 목록, 리포트·커버리지의 기간 조회)는 1000행씩 끊어 읽는다. 끊어 읽지 않는 새 조회를 쓰면 표가 커진 뒤 조용히 일부만 읽혀, 예를 들어 되감기 수집이 이미 받은 메시지를 다시 받는다.
- **관심 기업 파일이 깨져 있으면 GET 요청 하나가 파일을 옮긴다.** `GET /api/workspace`가 깨진 JSON을 `favorites.json.bak`으로 이름을 바꾼다. 이미 `.bak`이 있으면 윈도우에서 이름 바꾸기가 실패해 그 요청이 일반 503으로 끝난다.
- **일반 수집은 중간의 빈 메시지 번호를 채우지 않는다.** 시작점이 "가장 큰 번호 + 1"이라서다. 수집이 중간에 끊기면(Ctrl+C, 전체 실패) 동시에 받던 메시지 중 큰 번호만 들어가고 작은 번호가 빠질 수 있다. 빠진 구간이 의심되면 `collect --dry-run --backfill-days N`으로 "새로 받을 것" 수를 먼저 보고, 그다음 `--dry-run` 없이 돌린다.
- **분류 프롬프트 재료를 고치면 LLM 요청이 바뀐다.** 분류 시스템 프롬프트는 `research_desk/domain/vocabulary.yaml`의 리포트 종류 순서와 `research_desk/tagger/vocabulary/publishers.yaml` 원문(주석까지), 그리고 LLM 응답 모양(`LLMExtraction`)의 설명문으로 만들어진다. 발행처 사전의 정식 이름은 응답 모양의 발행처 값 목록(enum)으로도 나가고, 그림 행의 사용자 메시지에는 고정 문장 `PAGE_IMAGE_NOTE`가 들어간다. 이 중 하나만 고쳐도(주석 한 줄이라도) 분류 결과가 달라질 수 있고 Anthropic 프롬프트 캐시가 새로 쌓인다. 그래서 백필이 도는 동안에는 고치지 않는다. 고친 뒤에는 parity 테스트와 첫 배치 결과를 확인한다.
- **발행처 사전을 고칠 때의 함정.** (1) 별칭·파일 이름 표기 하나는 정확히 한 항목에만 넣고, 어떤 항목의 정식 이름과도 같으면 안 된다 — 어기면 사전 읽기가 실패한다. 테스트가 커밋을 막고, 그래도 들어가면 `tag run`·`tag escalate`는 행을 가져가기 전 그래프를 불러오는 단계에서 1로(반복 실행 스크립트는 재시도 뒤 1로 멈춘다), `tag requeue`는 4로 멈춘다. (2) 파일 이름 표기는 글자 그대로(대소문자까지) 비교한다. `MERITZ`와 `Meritz`는 다른 표기라 둘 다 필요하면 둘 다 넣는다. (3) 정식 이름을 바꾸거나 빼도 이미 저장된 행은 옛 이름 그대로다. `tag requeue --publisher-not-in-dictionary`가 `auto`·`review_needed` 행만 고르므로, 사람이 승인한 `verified` 행에는 옛 이름이 남는다. (4) 항목을 다른 구역으로 옮기면 그 발행처의 종류가 바뀐다 — 저장된 행은 `--publisher-type-mismatch`로 다시 분류한다. (5) 채널에서 새로 본 표기를 사전에 넣으면 `research_desk/tagger/tests/test_vocabulary.py`의 `OBSERVED_FILENAME_TAGS`에도 더한다.
- **PyMuPDF 호출 하나가 멈추면 같은 프로세스의 PDF 일이 모두 기다린다.** `core.pdf`가 PyMuPDF를 프로세스 전체 잠금 하나로 줄 세우기 때문이다. 분류기에서는 그 배치의 남은 행이 행 시간 한도(90초)를 넘겨 `pending`으로 돌아가고, 웹앱에서는 검토 쪽 그림과 분석이 멈춘 것처럼 보인다 — 웹앱을 다시 켠다. 잠금을 풀어 해결하지 않는다(스레드 동시 사용이 비정상 종료의 원인으로 추정돼 넣은 잠금이다).
- **`tag requeue --apply`의 실행 확인은 관리자 권한으로 띄운 프로세스를 못 본다.** 일반 권한에서 보면 관리자 프로세스의 명령줄이 비어 있어(`CommandLine` null) 백필·웹앱인지 알 수 없다. 백필·수집·웹앱을 관리자 창에서 띄우지 않는다. 띄웠다면 `--apply` 전에 직접 끈다.
- **`tag requeue --unreadable`의 `skipped.unreadable_no_page`가 크면 PDF 폴더 설정부터 본다.** 1쪽 그림 확인은 `STORAGE_BASE_DIR` 안의 파일로 한다. 설정이 틀리면 모든 행이 "그림을 만들 수 없음"으로 빠져 아무것도 되돌리지 않는다.
- **`frontend/dist`는 있는데 `frontend/dist/assets`가 없으면 웹 서버가 켜지지 않는다.** 서버를 켤 때 dist 폴더가 있으면 `/assets`를 그 아래 `assets`에 연결하는데, 그 폴더가 없으면 시작 중 오류가 난다. 빌드를 중간에 멈췄다면 `npm --prefix frontend run build`를 다시 끝까지 돌린다.
- **오래 묵은 잠금은 정상 `tag run`이 시작할 때만 풀린다.** 분류를 돌리지 않는 동안 `processing`에 남은 행은 그대로다. 크래시 직후 바로 풀려면 `tag reset-worker --worker-id <그 작업자 ID>`를 쓴다(래퍼는 자동으로 한다).
- **종목표 표시가 다시 갈라지면 원인부터 본다.** 예전 분류기는 CSV 파일 수정 시각으로 버전 표시를 만들어, 같은 내용에 `KRX@2026-05-08`·`-09`·`-12` 세 표시가 생겼다(마이그레이션 007로 통일). 지금은 버전 정보 파일만 쓰므로 새 표시는 `stocks set-version`으로만 생겨야 한다.
- **검토 되돌리기는 서버 메모리에만 있다.** 웹앱을 다시 켜면 직전 처리를 되돌릴 수 없다(`되돌릴 작업이 없거나 서버가 재시작되었습니다.`). 검토 도중에 서버를 재시작하지 않는다.
- **숫자 설정 값이 숫자가 아니면 "사용 불가"가 아니라 일반 오류다.** `PHASE2_PER_REPORT_TIMEOUT_S`·`PHASE2_MAX_INPUT_TOKENS`에 숫자가 아닌 값이 있으면 분석·비교 버튼이 일반 503("데이터를 불러오거나 분석하지 못했습니다…")으로 끝나고, `MAX_CONCURRENT_LLM` 같은 분류 숫자 설정이면 `tag run`이 종료 코드 1로 끝난다(준비 문제 4가 아니다). 이유 문구가 없으므로 이런 실패를 보면 `.env`의 숫자 값부터 본다.
- **분석 입력의 쪽 표시 `--- Page N ---`를 바꾸면 숫자 근거 확인이 깨진다.** 근거 확인이 이 표시로 글자를 쪽별로 나눠서, 지표가 인용한 쪽에 그 숫자가 있는지 본다.
- **옛 경로의 git 이력.** `research_desk/`의 파일들은 옛 `langgraph_tagger/`·루트 모듈에서 옮겨 오면서 새 경로에서 이력이 새로 시작했다. 옛 이력은 옛 경로로 본다: `git log -- langgraph_tagger/<파일>` 또는 `git log -- collector.py`.
- **postgrest-py의 `range()`는 offset·limit을 바꾸지 않고 덧붙인다.** 같은 쿼리 객체에 쪽마다 `.range()`를 다시 부르면 두 번째 쪽부터 요청 주소에 `offset`·`limit`이 두 번 이상 실린다. 새 쪽 나누기 읽기는 쪽마다 쿼리를 새로 만든다(주가 저장 코드 `features/prices/store.py`의 `read_all_returns`, 유사 기업 저장 코드 `features/peers/store.py`의 `_paged`처럼). 주가 기능의 가짜 DB는 같은 쿼리의 두 번째 `range()`를 거절해 이것을 잡는다. 유사 기업 기능의 가짜 DB는 postgrest-py처럼 offset·limit을 덧붙이고 첫 쌍으로 답하며, 같은 쿼리를 10번 넘게 보내면 실패한다.
- **pgvector 값은 REST로 글자로 돌아온다.** `embedding` 열을 select하면 숫자 목록이 아니라 `"[0.1,0.2,…]"` 글자가 온다. 숫자로 쓰려면 `features/peers/store.py`의 `parse_vector`로 푼다. 쓸 때는 숫자 목록을 그대로 보낸다. 1536개 숫자의 글자라 행이 커서, 벡터 읽기를 1000행씩 하면 응답 하나가 수십 MB가 된다 — 200행씩 읽는다.
- **임베딩 차원은 마이그레이션이 고정한다.** 임베딩 열이 `vector(1536)`이고 `peer_builds.embed_dims`는 1536만 받는다. `PEERS_EMBED_MODEL`을 `dimensions=1536`을 받지 않는 모델로 바꾸면 빌드의 임베딩 단계가 오류로 끝난다(API가 `dimensions`를 거절하거나, 1536개가 아닌 벡터를 빌드 코드가 거절한다). 빌드는 `failed`로 닫힌다. OpenAI의 `text-embedding-3-small`·`-large`만 쓰고, 차원을 바꾸려면 새 마이그레이션과 전체 재임베딩이 먼저다.
- **DB 함수 호출(RPC)에도 1000행 한도가 걸린다.** Supabase REST의 한 응답 1000행 제한은 `.rpc(…)`에도 똑같이 걸린다. `match_company_profiles`·`match_company_segments`는 최대 200행이라 괜찮지만, 함수의 상한을 1000보다 크게 고치면 오류 없이 1000행에서 잘린다.
- **pgvector가 `public` 스키마에 이미 있으면 마이그레이션 008이 실패한다.** 008은 `create extension if not exists vector with schema extensions`로 시작하고 표·함수가 `extensions.vector` 타입을 쓴다. 확장이 이미 `public`에 있으면 첫 문장은 아무것도 하지 않고, 표 만들기가 `extensions.vector` 타입이 없다는 오류로 실패한다(뒤는 한 트랜잭션이라 표는 하나도 안 생긴다). 적용 전에 `select extnamespace::regnamespace from pg_extension where extname = 'vector';`로 확인한다.
- **KIS 접근 토큰은 1분에 한 번쯤만 발급되고, 실패는 그 실행 동안 기억된다.** `prices update`가 종목을 하나도 묻지 않고 곧바로 `KIS 접근 토큰을 받지 못했습니다(…)`로 1이면, 같은 앱키로 1분 안에 토큰을 받은 곳(같은 키를 쓰는 masterdb 수집, 방금 돌린 다른 실행)이 있거나 키·시크릿이 틀린 것이다. 한 클라이언트는 토큰 요청이 한 번 실패하면 KIS에 다시 묻지 않으므로(재시도는 그 한 요청 안의 1·2·4초뿐) 같은 실행 안에서는 풀리지 않는다. 1분 이상 기다렸다 다시 돌리고, 계속 실패하면 KIS 개발자 센터에서 키를 확인한다. 실행마다 클라이언트를 하나만 만든다 — 종목마다 새로 만들면 토큰을 매번 요청해 곧바로 막힌다.
- **`peers.web_router()`를 한 번 부르면 `peers.router`는 라우터가 아니다.** 파이썬은 하위 모듈을 import할 때 그 이름을 패키지 속성으로 붙인다. `web_router()`가 `.router`를 import한 뒤에는 `research_desk.features.peers.router`가 `router.py` 모듈이고, 그 전에는 속성이 아예 없다. 그래서 웹 조립부의 `feature_router`는 `web_router`가 있으면 그것을 먼저 쓴다. 명령이 있는 기능에 `include_router(feature.router)`를 쓰면 어떤 순서로 불렸는지에 따라 다른 오류로 앱 생성이 깨진다.
- **프로필을 교체하면 그 회사의 모든 임베딩이 지워진다.** 회사를 다시 추출하면 옛 프로필 행을 먼저 지우고 새로 넣는다. 지울 때 사업부문과 모든 임베딩 모델의 임베딩이 연쇄로 지워지고, 새 임베딩은 그 빌드의 임베딩 단계(모든 회사의 추출이 끝난 뒤)에 만들어진다. 그래서 같은 (회계연도, 프로필 버전)으로 빌드가 도는 동안 다시 추출된 회사는 지금 공개된 빌드의 화면에서 404(`이 종목은 유사 기업 자료가 없습니다.`)가 나고 다른 회사의 목록에서도 빠진다. 다른 임베딩 모델의 공개 빌드가 쓰던 그 회사 임베딩은 그 모델로 다시 빌드할 때까지 돌아오지 않는다. `ok` 프로필은 상태 없이 넣은 뒤 부문을 넣고 마지막에 `ok`로 바꾸므로, 도중에 끊긴 행은 상태가 비어 재사용도 화면 노출도 되지 않고 다음 빌드가 다시 추출한다. 추출이 실패하면 교체하지 않으므로 이전 `ok` 프로필은 남는다.
- **공개 빌드의 용어 열 다시 쓰기는 UPDATE로만 한다.** `terms`만 바꾸는 쓰기를 upsert로 바꾸면, 그사이 지워진 회사(다시 추출 중이거나 보존 정리로 지워진 행)가 키와 `terms`만 있는 상태 없는 행으로 다시 생긴다. UPDATE는 없는 행을 만들지 않는다.
- **웹은 공개 빌드의 표와 동의어표를 빌드 번호마다 한 번만 읽는다.** 백분위표·용어표(수 MB)와 테마 검색이 쓰는 `synonyms.yaml`은 공개 빌드 번호가 바뀔 때만 다시 읽는다. 그래서 공개된 빌드 행의 표를 SQL로 고치거나 `synonyms.yaml`만 고치면 화면이 바뀌지 않는다. 동의어표를 고친 뒤에는 새 공개 빌드를 만든다 — 그래야 프로필의 용어 열·용어표와 질의 정규화가 같은 표를 쓴다. 다른 표로 만든 빌드를 쓰는 동안에는 웹 로그에 동의어표 지문이 다르다는 경고가 남는다.
- **화면이 유사 기업 주소의 404·422를 문장으로 구분한다.** 유사 기업 탭(`frontend/src/peers/Peers.jsx`)은 `detail`이 `이 종목은 유사 기업 자료가 없습니다.`이면 빈 안내 화면을, `그 사업부문이 없습니다.`이면 "기본 부문으로 보기" 버튼을 보여 준다. 서버 쪽 문장(`features/peers/service.py`)만 고치면 오류 없이 일반 오류 화면으로 바뀐다. 두 곳을 같이 고친다.
- **분류 진행 확인은 지금 가져간 행만 본다.** `peers build`는 최근 30분 안에 잠긴 `processing` 행이 있으면 시작하지 않는데, 반복 분류 스크립트의 배치 사이(다음 `tag run` 프로세스가 뜨는 몇 초), `tag escalate`, `tag run --row-ids`, `--dry-run`은 행을 `processing`으로 바꾸지 않아 이 확인을 통과한다. 백필 창이 열려 있으면 확인만 믿지 말고 빌드를 시작하지 않는다. 분류기의 가져가기 방식(상태 이름, `tagging_locked_at` 찍는 방식)을 바꾸면 리포트 기능의 `tagging_in_progress`도 함께 고친다.
- **`peers build`가 `ModuleNotFoundError: No module named 'fastapi'`로 1이면** 웹 패키지가 없는 것이다. 분류 진행 확인이 리포트 기능 창구를 거치고, 그 창구가 FastAPI를 불러온다. `requirements-workspace.txt`를 설치한다. `prices update`·`peers inspect`·`collect`·`tag`는 웹 패키지 없이 돈다.

## 반복 작업 점검표

### 새 기능 붙이기
1. `research_desk/features/<기능>/` 폴더와 `__init__.py`(다른 칸이 쓸 이름만 명시적으로 묶음), 필요한 칸(`router.py`·`service.py`·`store.py`·`logic.py`·`settings.py`·`jobs.py`), `tests/__init__.py`.
2. 준비 실패를 `NotReady("<고정 한국어 기능 이름>", "<고정 이유>")`로 낸다. 실패를 기억하지 않는다.
3. 웹 주소가 있으면 `research_desk/web/app.py`의 `FEATURES`에 한 줄. 명령이 있으면 `jobs.py`의 `register(subparsers)`를 창구에서 `register_jobs`로 묶고, `research_desk/cli.py`에 창구 import 한 줄과 `build_parser` 등록 한 줄(종료 코드: 준비 문제 4). 명령이 있는 기능은 창구 최상위에 FastAPI를 부르지 않는 이름만 두고, 웹 주소도 있으면 라우터 대신 `web_router()`를 둔다.
4. 새 표가 있으면 마이그레이션 파일 + 구조 검사 규칙의 표 주인 목록. 새 외부 라이브러리는 `core`에 두고 외부 도구 목록에 더한다.
5. 확인: `PYTHONIOENCODING=utf-8 .venv\Scripts\python.exe -m pytest -q -p no:cacheprovider`가 통과하고, 구조 검사에 위반이 없고, 그 기능만 준비에 실패시켰을 때 다른 주소와 `/api/health`가 200인지 테스트로 본다. 명령을 붙였으면 `research_desk/tests/test_cli.py`의 "명령 없는 실행은 무거운 패키지를 불러오지 않는다" 확인이 통과하는지 보고, 그 명령이 새로 끌어오는 무거운 패키지가 있으면 그 목록(`NOT_LOADED_WITHOUT_A_COMMAND`)에 더한다.

### 유사 기업 동의어표 고치기
1. `research_desk/features/peers/synonyms.yaml`에 `표준 표기: [다른 표기, …]`를 더한다. 한 표기는 표준 하나에만 속해야 하고, 다른 항목의 표준 표기를 다른 표기로 적으면 안 된다(어기면 `peers build`가 준비 문제 4로 멈춘다).
2. 낱말 안의 일부는 바뀌지 않는다(`시디램프`는 DRAM이 아니다). 여러 낱말로 된 표기는 용어 전체가 그 표기일 때만 바뀐다.
3. 전체 테스트 → `peers build --pilot`으로 이웃 목록을 확인 → 공개 빌드(`peers build`)를 다시 만든다. AI 추출은 다시 하지 않고(재사용), 공개 빌드가 용어 열·용어표를 새로 쓴다. 새 빌드가 공개되기 전에는 웹의 공통 키워드·테마 검색이 바뀌지 않는다.

### 유사 기업 임베딩 모델 바꾸기
1. `.env`의 `PEERS_EMBED_MODEL`만 바꾼다(1536차원을 낼 수 있는 OpenAI 모델만).
2. `peers build`(또는 `--pilot`)를 돌리면 그 모델의 임베딩이 없는 프로필·부문만 새로 임베딩한다. AI 추출은 다시 하지 않는다. 다른 모델의 임베딩은 남는다.
3. 확인: 요약 JSON의 `"embed_model"`과 `"embedded"` 수, `peers inspect`의 최근 빌드. 웹은 다음 요청부터 새 공개 빌드에 기록된 모델로 질의를 임베딩한다.

### 종목표 바꾸기
1. `docs/stock_data/KRX_stocks_data.csv`를 새 파일로 바꾼다(머리줄은 `종목코드, 종목명, 시장, 산업명(대), 산업명(중), 주요제품`).
2. `python -m research_desk stocks set-version --as-of <자료 기준일, YYYY-MM-DD>` → 새 버전 이름이 찍히고 0으로 끝나는지 본다.
3. 전체 테스트를 돌린다(함께 들어 있는 CSV = 버전 정보 테스트). 분류기 테스트 일부(parity 고정 사례, 종목 매칭)는 실제 종목표의 몇 행(`005930` 삼성전자, `000660` SK하이닉스, `016360` 삼성증권 등)의 이름·업종을 기대값으로 쓴다. 새 종목표에서 그 값이 바뀌어 실패하면 기대값을 새 종목표 값에 맞춘다 — 분류 규칙이 바뀐 것이 아니므로 규칙 코드를 고치지 않는다.
4. CSV와 `KRX_stocks_data.version.json`을 한 커밋에 올린다.
5. 그동안 종목표에 없어서 `review_needed`(`krx_unmatched_in_scope`)로 쌓인 행이 있으면 `tag requeue --krx-unmatched`(미리 보기 → `--apply` → 백필)로 다시 분류한다. `tag escalate --since <시각>`이나 검토의 재분류도 된다.

### 분류 모델 바꾸기
1. `.env`의 `LLM_MODEL_DEFAULT`(또는 `LLM_MODEL_ESCALATION`, `LLM_MODEL_PHASE2`)만 바꾼다. 공급자는 모델 이름이 정한다.
2. 쓸 공급자의 키(`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`)나 codex 로그인이 있는지 본다 — 없으면 분류 명령이 4로 멈춘다. 분류 모델은 그림을 받는 모델이어야 한다(`core.llm.supports_images`가 참). 아니면 글자 없는 PDF가 모두 검토 대기로 가고 배치 보고의 `page_image_unsupported`가 늘어난다.
3. 돌고 있는 반복 분류와 웹앱을 다시 켠다.
4. 첫 배치 몇 개의 JSON 보고와 `tag inspect` 비율을 평소 기준과 비교한다. 행에 모델 이름이 저장되지 않으므로 바뀐 시점 전후는 `tagged_at`으로 나눠 본다.

### 발행처 사전 고치기
1. 백필·재처리가 돌고 있지 않을 때 `research_desk/tagger/vocabulary/publishers.yaml`을 고친다(백필 중에는 고치지 않는다). 정식 이름은 표지의 지금 회사명, 독립 리서치는 `data_provider`, 기술분석보고서는 작성기관과 상관없이 `한국IR협의회`.
2. 새 표기를 넣었으면 `OBSERVED_FILENAME_TAGS`에도 더하고 전체 테스트를 돌린다(사전 규칙 위반은 `test_vocabulary.py`가 잡는다). 사전을 커밋한다.
3. "다시 분류 대기" 절차대로: 백필·수집·웹앱 끄기 → `tag requeue <조건>` 미리 보기 → `--apply` → 백필 → (필요하면) 발행처가 바뀐 저장된 비교 비우기.

### 운영 DB 데이터 고치기
분류된 행을 조건으로 골라 `pending`으로 되돌리는 일이면 손으로 하지 말고 `tag requeue`를 쓴다(아래 순서를 코드로 지킨다). 그 밖의 데이터 수정은 이 순서로 한다.
1. 반복 분류·수집·웹앱이 돌고 있지 않은지 확인한다.
2. 바뀔 행의 `(id, 바뀔 열)`을 파일로 백업한다.
3. 한 트랜잭션에서: 같은 문장을 `EXPLAIN`으로 먼저 확인 → 실행 → 바뀐 행 수 = 백업 행 수인지, 결과 분포가 기대와 같은지 확인 → 둘 다 맞을 때만 커밋, 아니면 되돌림.
