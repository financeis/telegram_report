# 알아 둘 것

## 디버깅하지 말아야 할 무해한 경고

| 보이는 것 | 원인 | 대응 |
|---|---|---|
| Pydantic serializer warning (`LLMExtraction` 직렬화 시) | 분류기의 LLM 응답 모델(`LLMExtraction`)을 직렬화할 때 Pydantic이 내는 경고 | 기능 영향 없음(분류 결과와 저장 값이 같다). 무시한다 |
| 반복 실행 중 `exit=-1073741569` (Windows native crash) | 윈도우에서 파이썬 프로세스가 비정상 종료된 경우 | `run-batches.ps1`이 그 작업자의 행을 되돌리고 재시도해서 자동 복구한다. 3회 반복 테스트 중 1회 발생했고, 5건이 자동으로 `pending`으로 돌아간 뒤 재시도에서 성공해 데이터 손실이 없었다 |
| `LangChainPendingDeprecationWarning: The default value of allowed_objects will change…` | LangGraph가 import될 때 내는 예고 경고. `tag run`·`tag escalate`가 그래프를 불러올 때와, 테스트 실행 끝의 "1 warning"이 이것이다 | 무시한다. `collect`·`web`·`stocks`·`tag inspect`·`tag reset-worker`·`--help`에서 이 경고가 보이면 그쪽은 무해한 것이 아니다 — 누가 명령 입구에서 그래프를 일찍 import하게 만든 것이니 그 import를 함수 안으로 옮긴다 |

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
- **분류 프롬프트 재료를 고치면 LLM 요청이 바뀐다.** 분류 시스템 프롬프트는 `research_desk/domain/vocabulary.yaml`의 리포트 종류 순서와 `research_desk/tagger/vocabulary/publishers.yaml` 원문(주석까지), 그리고 LLM 응답 모양(`LLMExtraction`)의 설명문으로 만들어진다. 주석 한 줄만 고쳐도 분류 결과가 달라질 수 있고 Anthropic 프롬프트 캐시가 새로 쌓인다. 고친 뒤에는 parity 테스트와 첫 배치 결과를 확인한다.
- **`frontend/dist`는 있는데 `frontend/dist/assets`가 없으면 웹 서버가 켜지지 않는다.** 서버를 켤 때 dist 폴더가 있으면 `/assets`를 그 아래 `assets`에 연결하는데, 그 폴더가 없으면 시작 중 오류가 난다. 빌드를 중간에 멈췄다면 `npm --prefix frontend run build`를 다시 끝까지 돌린다.
- **오래 묵은 잠금은 정상 `tag run`이 시작할 때만 풀린다.** 분류를 돌리지 않는 동안 `processing`에 남은 행은 그대로다. 크래시 직후 바로 풀려면 `tag reset-worker --worker-id <그 작업자 ID>`를 쓴다(래퍼는 자동으로 한다).
- **종목표 표시가 다시 갈라지면 원인부터 본다.** 예전 분류기는 CSV 파일 수정 시각으로 버전 표시를 만들어, 같은 내용에 `KRX@2026-05-08`·`-09`·`-12` 세 표시가 생겼다(마이그레이션 007로 통일). 지금은 버전 정보 파일만 쓰므로 새 표시는 `stocks set-version`으로만 생겨야 한다.
- **검토 되돌리기는 서버 메모리에만 있다.** 웹앱을 다시 켜면 직전 처리를 되돌릴 수 없다(`되돌릴 작업이 없거나 서버가 재시작되었습니다.`). 검토 도중에 서버를 재시작하지 않는다.
- **숫자 설정 값이 숫자가 아니면 "사용 불가"가 아니라 일반 오류다.** `PHASE2_PER_REPORT_TIMEOUT_S`·`PHASE2_MAX_INPUT_TOKENS`에 숫자가 아닌 값이 있으면 분석·비교 버튼이 일반 503("데이터를 불러오거나 분석하지 못했습니다…")으로 끝나고, `MAX_CONCURRENT_LLM` 같은 분류 숫자 설정이면 `tag run`이 종료 코드 1로 끝난다(준비 문제 4가 아니다). 이유 문구가 없으므로 이런 실패를 보면 `.env`의 숫자 값부터 본다.
- **분석 입력의 쪽 표시 `--- Page N ---`를 바꾸면 숫자 근거 확인이 깨진다.** 근거 확인이 이 표시로 글자를 쪽별로 나눠서, 지표가 인용한 쪽에 그 숫자가 있는지 본다.
- **옛 경로의 git 이력.** `research_desk/`의 파일들은 옛 `langgraph_tagger/`·루트 모듈에서 옮겨 오면서 새 경로에서 이력이 새로 시작했다. 옛 이력은 옛 경로로 본다: `git log -- langgraph_tagger/<파일>` 또는 `git log -- collector.py`.

## 반복 작업 점검표

### 새 기능 붙이기
1. `research_desk/features/<기능>/` 폴더와 `__init__.py`(다른 칸이 쓸 이름만 명시적으로 묶음), 필요한 칸(`router.py`·`service.py`·`store.py`·`logic.py`·`settings.py`·`jobs.py`), `tests/__init__.py`.
2. 준비 실패를 `NotReady("<고정 한국어 기능 이름>", "<고정 이유>")`로 낸다. 실패를 기억하지 않는다.
3. 웹 주소가 있으면 `research_desk/web/app.py`의 `FEATURES`에 한 줄. 명령이 있으면 `jobs.py`의 `register(subparsers)` + `research_desk/cli.py`의 `build_parser`에 한 줄(종료 코드: 준비 문제 4).
4. 새 표가 있으면 마이그레이션 파일 + 구조 검사 규칙의 표 주인 목록.
5. 확인: `PYTHONIOENCODING=utf-8 .venv\Scripts\python.exe -m pytest -q -p no:cacheprovider`가 통과하고, 구조 검사에 위반이 없고, 그 기능만 준비에 실패시켰을 때 다른 주소와 `/api/health`가 200인지 테스트로 본다.

### 종목표 바꾸기
1. `docs/stock_data/KRX_stocks_data.csv`를 새 파일로 바꾼다(머리줄은 `종목코드, 종목명, 시장, 산업명(대), 산업명(중), 주요제품`).
2. `python -m research_desk stocks set-version --as-of <자료 기준일, YYYY-MM-DD>` → 새 버전 이름이 찍히고 0으로 끝나는지 본다.
3. 전체 테스트를 돌린다(함께 들어 있는 CSV = 버전 정보 테스트). 분류기 테스트 일부(parity 고정 사례, 종목 매칭)는 실제 종목표의 몇 행(`005930` 삼성전자, `000660` SK하이닉스, `016360` 삼성증권 등)의 이름·업종을 기대값으로 쓴다. 새 종목표에서 그 값이 바뀌어 실패하면 기대값을 새 종목표 값에 맞춘다 — 분류 규칙이 바뀐 것이 아니므로 규칙 코드를 고치지 않는다.
4. CSV와 `KRX_stocks_data.version.json`을 한 커밋에 올린다.
5. 그동안 종목표에 없어서 `review_needed`(`krx_unmatched_in_scope`)로 쌓인 행이 있으면 `tag escalate --since <시각>`이나 검토의 재분류로 다시 분류한다.

### 분류 모델 바꾸기
1. `.env`의 `LLM_MODEL_DEFAULT`(또는 `LLM_MODEL_ESCALATION`, `LLM_MODEL_PHASE2`)만 바꾼다. 공급자는 모델 이름이 정한다.
2. 쓸 공급자의 키(`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`)나 codex 로그인이 있는지 본다 — 없으면 분류 명령이 4로 멈춘다.
3. 돌고 있는 반복 분류와 웹앱을 다시 켠다.
4. 첫 배치 몇 개의 JSON 보고와 `tag inspect` 비율을 평소 기준과 비교한다. 행에 모델 이름이 저장되지 않으므로 바뀐 시점 전후는 `tagged_at`으로 나눠 본다.

### 운영 DB 데이터 고치기
1. 반복 분류·수집·웹앱이 돌고 있지 않은지 확인한다.
2. 바뀔 행의 `(id, 바뀔 열)`을 파일로 백업한다.
3. 한 트랜잭션에서: 같은 문장을 `EXPLAIN`으로 먼저 확인 → 실행 → 바뀐 행 수 = 백업 행 수인지, 결과 분포가 기대와 같은지 확인 → 둘 다 맞을 때만 커밋, 아니면 되돌림.
