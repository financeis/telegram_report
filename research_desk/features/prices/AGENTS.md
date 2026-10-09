# research_desk/features/prices/ — 매일 주가 스냅샷: KIS에서 받아 저장하는 명령과, 다른 기능이 읽는 주가 창구

## 맡는 일

- 주가 표 두 개의 유일한 주인: `stock_price_snapshot`(종목당 최신 스냅샷 한 행)과 `price_update_runs`(실행마다 한 행). 다른 기능은 이 기능의 창구로만 읽는다.
- 명령 `prices update [--codes 005930,080220]`(`jobs.py`)과 매일 실행 스크립트 `scripts/run-prices.ps1`.
- 창구 이름은 셋이다: `register_jobs`, `snapshots(codes)`, `latest_run()`. 웹 주소는 없다(유사 기업 기능과 상태 줄이 창구로 읽는다).
- 계산(`logic.py`): 기간 수익률, 같은 실행·같은 시장 중앙값 대비 초과수익률, 20일 평균 거래대금, 표시(`flags`), 실행 상태.
- 설정(`settings.py`): `KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_BASE_URL`, `PRICES_MAX_CALLS_PER_SEC`.

## 맡지 않는 일

- KIS HTTP 호출·접근 토큰·재시도·호출 간격·키 가리기. 모두 `core.kis.KisClient`가 한다. 이 칸은 httpx를 import하지 않는다(`R9 외부 도구`).
- DB 연결 만들기는 `core.db.supabase_client`로만. 주가 표 밖의 표(`reports` 등)는 다루지 않는다(`R11 표 주인`).
- 종목표 파일은 `domain.stocks.StockList`로만 읽는다. 버전 지문은 확인하지 않는다(주가는 종목표 버전을 저장하지 않는다).
- 반응·후보 판정(`features/peers`), 상태 줄의 밀림 판정(`features/freshness`). 이 칸은 값만 준다.
- 다른 기능 import. `peers`·`freshness`가 이 칸에 기대므로 그쪽을 import하면 `R6 순환 금지`다. `collector`·`tagger`·`web`·`cli`도 import하지 않는다.
- FastAPI. 창구는 모든 명령에서 import되므로 창구가 부르는 모듈(`jobs.py`, `service.py`, `logic.py`, `store.py`, `settings.py`) 최상위에 FastAPI·httpx·supabase·pymongo를 두지 않는다.

## 늘 지켜야 할 것

**창구 import는 가볍다.** `cli.py`가 모든 명령에서 `research_desk.features.prices`를 import한다. supabase-py(`core.db`)는 `service.connect()` 안에서, KIS 클라이언트(`core.kis`, httpx)는 `jobs.kis_client()` 안에서 import한다. 테스트가 창구 import로 웹·HTTP·KIS·MongoDB 패키지가 올라오지 않는지 확인한다.

**단위.** 수익률·초과수익률은 % 실수(12.3 = +12.3%)이고 DB와 창구가 같다. 금액(종가·시가총액·평균 거래대금)은 원 단위 정수. 날짜는 `YYYY-MM-DD`, 시각은 오프셋이 붙은 ISO 글자로 저장한다.

**창구 모양.**
- `snapshots(codes)` → `{code: price}`: 저장된 코드만, 물은 순서대로, 코드마다 한 번. 영문·숫자가 아닌 코드는 아무것도 맞지 않는다. 문자열 하나를 넘기면 `TypeError`(글자마다 묻게 되므로). `price` = `{"market", "as_of", "close", "market_cap", "avg_value_20d", "traded", "returns": {"1w", "1m", "3m"}, "excess": {"1w", "1m", "3m"}, "flags"}`. 6개월 수익률은 저장만 하고 창구로 내지 않는다.
- `latest_run()` → `{"last_run_at": 가장 최근 실행의 시작(UTC aware), "last_run_status", "as_of": 가장 최근 ok/partial 실행의 기준일 | None}`, 실행이 하나도 없으면 None.
- DB 설정이 없으면 둘 다 `NotReady("주가", "DB 접속 설정(SUPABASE_URL, SUPABASE_SERVICE_KEY)이 없습니다")`. 실패는 기억하지 않는다. 빈 표는 준비 실패가 아니다(`{}`, None).

**명령의 준비 확인(종료 코드 4).** 아무것도 하기 전에 DB 설정 → KIS 키 → 종목표 읽기 순서로 본다. 문구는 `주가 갱신을 시작하지 않았습니다. 이유: …` 한 줄, KIS·DB는 부르지 않는다. `PRICES_MAX_CALLS_PER_SEC`가 0보다 큰 수가 아니면 `ValueError`(1), 이것도 호출 전이다.

**전체 실행 순서.** 실행 기록 `running` → 접근 토큰(못 받으면 아무 종목도 묻지 않고 `failed`, 모든 종목을 실패로 셈, 스냅샷 불변, 1) → 종목마다 일봉(오늘 한국 날짜까지 `LOOKBACK_DAYS` = 240일) 다음 현재가 → 상태(`logic.run_status`) → `ok`·`partial`만 저장 → 실행 기록 마감 → 요약 한 줄.
- 받지 못한 종목이 20%(`MAX_FAILED_PERCENT`)를 넘는 순간 남은 종목은 묻지 않는다(어차피 `failed`).
- `failed`면 스냅샷을 하나도 바꾸지 않는다. `partial`이면 받은 종목을 이번 실행의 시장 중앙값으로 계산한 초과수익률과 함께 upsert하고, 받지 못한 종목은 저장된 행의 `flags`에 `no_data`만 더한다(값은 그대로, 저장된 행이 없으면 아무것도 쓰지 않음).
- 예외·Ctrl+C면 실행 기록을 `failed`로 닫고 예외를 그대로 올린다(오류는 1, Ctrl+C는 인터프리터의 중단 코드). 저장은 원자적이지 않다 — upsert 도중 끊기면 이미 쓴 종목은 새 값이고, 다음 실행이 모두 다시 쓴다.

**`--codes`는 확인용이다.** 토큰을 먼저 받고(못 받으면 stdout 없이 stderr 한 줄, 1), 그 종목만 묻고, 결과 JSON과 요약 한 줄을 낸 뒤 스냅샷·실행 기록 어디에도 쓰지 않는다. 초과수익률은 저장된 스냅샷 전체의 시장 중앙값(`store.read_all_returns`)으로, 저장된 것이 없으면 비운다. 종목표에 없는 코드는 묻지 않는다. 모두 받으면 0, 아니면 1. 이 실행이 아무것도 쓰지 않는다는 것이 상태 줄과 다음 매일 실행의 전제이므로, `--codes` 경로에 쓰기를 넣지 않는다.

**대상과 시장.** 대상은 종목표의 `by_code`(같은 코드는 마지막 행) 중 `validate_code`를 통과한 것, 파일 순서. 시장은 `KOSPI` → KOSPI, `KOSDAQ`·`KOSDAQ GLOBAL` → KOSDAQ, 그 밖은 None(중앙값·초과수익률 없음). DB의 CHECK도 KOSPI·KOSDAQ만 받는다.

**DB 읽기·쓰기(`store.py`).** 쪽 나누기 읽기(`read_all_returns`)는 쪽마다 새 쿼리를 만든다 — postgrest-py의 `range()`는 offset·limit을 바꾸지 않고 덧붙인다. 코드 목록 읽기는 200개씩(`CODES_PER_QUERY`), upsert는 500행씩(`UPSERT_ROWS`), `stock_code`로 충돌 처리. 한 upsert 안의 행은 같은 열을 가져야 한다(빠진 열은 null로 쓰인다). "가장 최근 실행"은 `started_at` 내림차순, 같으면 `run_id` 내림차순.

**비밀 값.** 키·시크릿은 `PricesSettings`의 표시 글자에 나오지 않는다(`repr=False`). 출력과 실행 기록 메시지의 KIS 오류 문장은 클라이언트가 가린 것만 쓴다. 종목표 원인 문장은 경로를 담을 수 있으므로 명령 출력(이 PC 터미널)에만 쓰고 웹 응답에는 쓰지 않는다.

## 이 칸의 방식

| 파일 | 역할 |
|---|---|
| `__init__.py` | 창구: `register_jobs`, `snapshots`, `latest_run` |
| `jobs.py` | 명령 등록과 실행. 테스트가 바꿔 끼우는 이름: `utc_now`, `kis_client` |
| `logic.py` | 순수 계산(DB·네트워크·설정·파일 없음) |
| `store.py` | 두 표의 읽기·쓰기. 첫 인자는 supabase-py 클라이언트 |
| `service.py` | 창구 함수, DB 준비(처음 쓸 때, 잠금 안에서 한 번), 프로세스 전역 `_service` |
| `settings.py` | KIS 설정. 기본 주소는 `core.kis.DEFAULT_BASE_URL`과 같은 값을 따로 적어 둔다(설정 읽기가 KIS 클라이언트를 불러오지 않게; 테스트가 둘이 같은지 본다) |

- `scripts/run-prices.ps1`은 저장소 폴더로 옮겨 `-Python` → `RESEARCH_DESK_PY` → 저장소의 `.venv` → 기본 작업 폴더의 `.venv` 순서로 파이썬을 찾고, `python -u -m research_desk prices update`의 종료 코드를 그대로 돌려준다. 파이썬이 없으면 4. PowerShell 5.1 문법, UTF-8(BOM 포함), 한국어 안내.
- 조정 가능한 숫자(20%, 240일, 기간 거래일 수)는 `logic.py`·`jobs.py`의 상수다.

## 테스트

- 실행: 저장소 루트에서 `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider research_desk/features/prices`.
- `tests/fakes.py`의 `FakeSupabase`는 두 표를 마이그레이션 008처럼 흉내 낸다: 한 응답 1000행, NULL과 정렬 규칙, NOT NULL·CHECK, 처음 쓰는 행의 기본값, 같은 쿼리에 두 번째 `range()`를 부르면 거절, `fail_next`로 DB 장애. `FakeKis`는 한 실행의 KIS 클라이언트다. 실제 KIS·DB·`.env`에는 닿지 않는다.
- 꼭 덮을 것: 거래일 수 부족 → `short_history`, 중앙값에서 수익률 없는 종목 제외, KOSDAQ GLOBAL 합치기, 거래정지, 20% 경계(정확히 20%는 `partial`), `failed` 때 스냅샷 불변, 실행 기록 상태 전이, 토큰 실패(한 번만 묻고 종목 요청 없음), `--codes`가 아무것도 쓰지 않음, 종료 코드 0·1·4와 준비 문제 때 KIS·DB 미접촉, 창구 import가 무거운 패키지를 부르지 않음, 서비스를 만들 때 아무것도 읽지 않음, `.env`에 더한 값 반영.
- 스크립트 테스트(`test_run_prices_script.py`)는 인자·폴더를 기록하고 정한 코드로 끝나는 가짜 파이썬(배치 파일)으로 종료 코드 전달만 본다. 진짜 명령은 실제 `.env`를 읽고 실제 DB·KIS에 닿으므로 새 프로세스로 돌리지 않는다.
