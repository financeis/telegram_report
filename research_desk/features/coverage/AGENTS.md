# research_desk/features/coverage/ — 수집된 리포트의 커버리지 집계

## 맡는 일

- `GET /api/market`: 기간별(일·주·월) 산업(대·중)·제품별 건수, 종목 순위, 리포트 종류별 발행량.
- `GET /api/stocks/{code}/activity`: 한 기업의 발행 추이와 발행처 비중.
- 기간 행 묶음 캐시와 그 비우기.
- 종목별 리포트 수 `report_counts(codes, days=365)`: 유사 기업 화면이 쓰는 종목 리포트·섹터 언급·기타 리서치 세 숫자와 마지막 종목 리포트 날짜·발행처 수·라벨.
- 공개 창구는 `router`, `invalidate`, `report_counts`다.
- 숫자의 뜻은 "이 채널에서 다뤄진 횟수"다. 시장 전체의 관심도 지표로 이름 붙이거나 설명하지 않는다.
- 서버 폴더 이름은 `coverage`지만 화면의 보기 이름 `market`과 주소 `/api/market`은 그대로 둔다. 이름을 맞추려고 주소를 바꾸면 화면이 깨진다.

## 맡지 않는 일

- `reports` 표를 직접 읽지 않는다(coverage는 표 주인이 아니다, `R11 표 주인`). 행은 `reports.period_rows(since, include_oos)`, `reports.stock_rows(code, since)`, `reports.rows_for_stocks(codes, since)`로만 받는다. 읽을 열(16개), 분석 대상 조건, 1000행씩 나눠 읽기, 유효 날짜 거르기는 `features/reports`가 한다.
- 리포트 수를 어떻게 쓰는지(후보 조건, 화면 표시)는 `features/peers` 몫이다. 이 칸은 숫자와 라벨만 준다. peers를 import하지 않는다(`peers → coverage` 방향이 있어 고리가 생긴다).
- DB 연결을 만들지 않는다(`core.db`를 쓰지 않는다). DB 준비와 그 실패 `NotReady("리포트", …)`는 reports 창구 몫이다.
- `review`를 import하지 않는다. `review → coverage` 방향이 이미 있어 고리가 생긴다(`R6 순환 금지`). 검토 뒤 캐시 비우기는 review가 부른다.
- 분류기·수집기는 웹 기능을 모르므로 이 캐시를 비우지 못한다. 그쪽에서 바뀐 행은 캐시 유효 시간(180초) 안에서 늦게 보일 수 있다. 이것을 메우려고 tagger·collector가 coverage를 부르게 만들지 않는다(`R3 collector`·`R4 tagger` 위반).
- 종목표 파일 처리는 `domain.stocks`가 한다. `collector`·`tagger`·`web`·`cli` import도 하지 않는다(`R5 기능`).

## 늘 지켜야 할 것

- 인자: `days` 1~36500(기본 36500), `unit` `D`|`W`|`M`(기본 `W`), `level` `sectors_major`|`sectors_minor`|`products`(기본 `sectors_major`), `items` 반복 값(기본 없음), `include_oos`(기본 false). activity는 `days`와 `unit`만 받는다. 범위 밖이거나 모르는 값은 아무것도 읽기 전에 422다.
- 기간 시작일 = 한국 시간 오늘 − `days`일(`logic.period_start`).
- 집계 날짜(유효 날짜)는 `published_at`, 없으면 `sent_at`을 한국 시간 날짜로 바꾼 값이다. 분석 대상 외 행은 분류기가 `published_at`을 비우므로, 이 대체가 없으면 유형별 발행량에서 조용히 빠진다.
- 응답 키와 순서: market `total, inscope, oos, publishers, latest, earliest, available_items, coverage, ranking, types` / activity `timeline, publishers, total`.
  - 시계열의 `bucket`은 ISO 문자열(`2026-05-11T00:00:00.000` 꼴)이고 화면이 앞 10글자를 날짜로 쓴다. 주는 월요일, 월은 1일부터 묶고, 모르는 단위는 일 단위로 센다.
  - 항목을 고르지 않으면 시계열은 건수 상위 10개 항목, 순위는 상위 20개 종목이다. 항목을 고르면 그중 하나라도 가진 행을 센다.
  - activity의 발행처 비중은 건수 순이고, 발행처가 5곳을 넘으면 상위 5곳과 나머지를 합친 `기타`로 낸다.
- `coverage`·`ranking`·`available_items`·`inscope`는 늘 분석 대상 행만 센다. 분석 대상 외 행은 `include_oos=true`일 때만 `types`, `total`, `oos`, `publishers`, `latest`, `earliest`에 들어간다. 순위의 이름은 종목표에서 붙이고, 종목표에 없는 코드는 이름이 `""`다.
- 분류기는 산업·전략·시황 리포트에 종목·업종·제품을 붙이지 않는다. 그래서 이 리포트들은 `coverage`·`ranking`·`available_items`와 기업별 activity에 잡히지 않고, `types`와 전체 건수·발행처 수(`total`, `inscope`, `publishers`)에만 들어간다.
- activity는 받은 코드 그대로 DB에 묻고(0을 채우지 않는다), 리포트 종류로 거르지 않는다. 그 종목 코드를 가진 행이면 종류와 상관없이 센다. `total`은 받은 행 수다.
- 같은 PDF가 다른 메시지로 다시 올라오면 행이 따로라 두 번 센다(알려진 문제). 내용 지문(`file_hash_sha256`)으로 묶으면 숫자가 바뀌는 동작 변경이므로 조용히 바꾸지 않는다.
- 리포트 수(`report_counts`, 계산은 `logic.count_reports`)
  - 기준 행: `reports.rows_for_stocks(codes, period_start(days))`가 준 분석 대상 행 중, 유효 날짜가 한국 오늘 − `days` 이후(그날 포함)이고 `stock_codes`에 그 코드가 있는 행. 코드는 받은 그대로(0을 채우지 않음), 각 한 번, 받은 순서. 문자열 하나를 넘기면 `TypeError`.
  - 한 행은 그 코드에 대해 많아야 하나에 센다, 이 순서로: 발행처 종류가 `broker`이거나 비었고 리포트 종류가 `단일종목`·`기타`·빈 값이고 `stock_codes`가 정확히 `[코드]` → `stock_reports` / 발행처 종류가 `broker`이거나 비었고 리포트 종류 `섹터` → `sector_mentions`(종목 수와 상관없이) / 발행처 종류 `data_provider`·`ir_agency`·`other` → `other_research` / 그 밖은 세지 않는다. 빈 값은 None·NaN·빈 문자열 모두다.
  - 발행처 종류가 빈 행을 증권사로 치는 것은 의도다(증권사 리포트가 있는 회사를 "리포트 없음"으로 잘못 띄우지 않게). 바꾸지 않는다.
  - `last_stock_report_date`는 종목 리포트의 가장 늦은 유효 날짜, `brokers`는 종목 리포트의 서로 다른 빈 값이 아닌 `publisher` 수. 라벨: 종목 리포트 0 → `none`, 3건(`COVERED_MIN_REPORTS`) 이상이고 마지막이 오늘 − 180일(`FRESH_DAYS`) 이후 → `covered`, 그 밖 → `few`.
  - 행이 없는 코드도 0·None·`none`으로 답에 넣는다. 캐시하지 않고 부를 때마다 읽는다. 종목표가 필요 없다. 리포트 창구의 `NotReady("리포트", …)`는 그대로 올린다.
  - 값 이름(`broker`, `단일종목`, `기타`, `섹터`, 다른 발행처 종류)은 `logic.py`에 옮겨 적었다. 분류 체계 값이 바뀌면 함께 고친다(테스트가 공용 기준과 같은지 본다).
- 캐시
  - 캐시하는 것은 DB에서 읽은 행 묶음이고 응답이 아니다. `(시작일, include_oos)` 한 묶음만, 행이 도착한 때부터 180초 들고 있다. 다른 키가 오면 새로 읽고 앞 묶음은 버린다.
  - `level`·`unit`·`items`에 따른 집계는 요청마다 새로 한다. 응답을 캐시하면 선택을 바꿔도 지난 답이 보인다.
  - 기간 읽기는 한 번에 하나다. 같은 키로 기다리던 요청은 앞 요청이 넣은 행을 쓴다. 읽기가 실패하면 아무것도 남기지 않는다.
  - `invalidate()`는 캐시를 바로 비우고 진행 중인 읽기를 기다리지 않는다. 그 읽기의 행은 요청한 쪽에는 돌려주되 캐시에 넣지 않는다. 검토 변경보다 오래된 행일 수 있기 때문이다.
  - activity는 캐시하지 않는다.
  - 캐시된 같은 DataFrame을 여러 요청이 나눠 쓴다. `logic.py` 함수는 받은 DataFrame을 제자리에서 바꾸지 않는다(복사본에 열을 더한다).
- 준비
  - market은 행을 읽기 전에 종목표부터 준비한다. 못 읽으면 `NotReady("커버리지", "종목표 파일을 읽을 수 없습니다")`이고 DB는 읽지 않는다. 응답에는 경로·원인을 넣지 않고, 원인은 경고 로그로만 남긴다.
  - activity는 종목표가 필요 없다. 종목표가 없어도 동작한다.
  - DB 설정이 없을 때 reports 창구가 내는 `NotReady("리포트", …)`는 잡지 않고 그대로 올린다. 화면에는 `리포트 기능을 지금 쓸 수 없습니다: …`가 보여야 한다.
  - 종목표 지문 불일치·버전 정보 없음은 경고 로그만 남긴다(로그 문장에 `커버리지`가 들어간다).

## 이 칸의 방식

- `router.py`가 `period_start(days)`로 시작일을 만들어 서비스에 넘긴다. `service.py`의 `CoverageService`가 캐시·준비·주소 일을 맡고, `logic.py`는 DB·설정·파일 없이 pandas 계산만 한다.
- reports 창구는 `from research_desk.features import reports`로 들고 있다가 `reports.period_rows(...)`처럼 부르는 순간 속성으로 찾는다. 테스트가 이 속성을 바꿔 끼운다.
- 창구 함수 `invalidate()`는 `get_service()`의 프로세스 전역 서비스를 비운다. 라우터도 같은 `get_service()`를 쓰므로 둘이 같은 캐시를 본다. 캐시를 든 서비스를 따로 만들지 않는다.
- 잠금은 둘이다. `_read_lock`은 기간 읽기 전체를, `_cache_lock`은 캐시와 세대 번호만 지킨다. `invalidate()`는 `_cache_lock`만 잡고 세대 번호를 올리므로 읽기를 기다리지 않는다. 읽는 사이에 세대가 바뀌었으면 그 행은 캐시에 넣지 않는다.
- 시간은 `CoverageService(clock=...)`로 받는다(기본 `time.monotonic`).
- 종목표는 첫 market 요청에서 `settings.load_env()` → `StockList.load(settings.krx_csv_path())`로 읽어 `code, name, sector_major, sector_minor` 열의 DataFrame으로 프로세스 내내 들고 있다. 그래서 종목표를 바꾸면 웹앱을 다시 켜야 순위 이름이 바뀐다. 실패하면 다음 요청에서 다시 읽으므로, 파일을 채우거나 `.env`에 `KRX_CSV_PATH`를 더하면 재시작 없이 회복한다.
- 핸들러 이름 `market`, `activity`와 인자는 `/openapi.json`을 그대로 두려고 정한 것이다. 바꾸거나 docstring을 달지 않는다.

## 테스트

- 실행: 저장소 루트에서 `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider research_desk/features/coverage`.
- `reads` fixture(autouse)가 `reports.period_rows`·`reports.stock_rows`·`reports.rows_for_stocks`를 창구 모듈에서 바꿔 끼우고 읽기마다 기록한다. 진짜 reports 서비스와 Supabase 클라이언트는 생기지 않는다. `clean_env`(autouse)는 관련 변수를 지우고 `core.db.supabase_client`를 거부하게 바꾼다.
- 캐시 시간은 `FakeClock`으로 움직인다. 실제로 기다리지 않고 180초 경계를 정확히 시험한다.
- `invalidate()` 경로는 `dependency_overrides`가 아니라 `_service`를 바꾼 `window` fixture로 시험한다. `dependency_overrides`로 끼운 서비스는 `coverage.invalidate()`가 비우는 캐시와 다른 객체다.
- `logic.py`는 DataFrame만으로 시험한다: `tests/conftest.py`의 `inscope_df`·`with_oos_df`, `tests/rows.py`의 `frame()`·`wider_frame()`과 리포트 수용 `report()`·`frame_of()`(reports 창구와 같은 16개 열). 분석 대상 외 행은 `published_at=None`과 `sent_at`만 두어 유효 날짜 대체를 시험하고, UTC 오후 늦은 `sent_at`(한국은 다음 날)으로 날짜 경계를 확인한다.
- 리포트 수의 꼭 지킬 검증: 세 숫자의 분류(`broker`·빈 값·`data_provider`·`ir_agency`·`other` × `단일종목`·`기타`·빈 종류·`섹터`), 한 종목만 매핑된 `섹터`는 종목 리포트가 아님, 여러 종목 단일종목 리포트는 어디에도 안 셈, 세 숫자가 겹치지 않음, 365일·180일 경계, 라벨, 행 없는 코드, `NotReady("리포트", …)` 통과.
- 꼭 지킬 검증: 인자 범위·기본값과 읽기 전 422, 응답 키 순서, 180초·한 묶음·선택마다 다시 집계·읽기는 하나씩·`invalidate()`가 기다리지 않고 진행 중 결과를 남기지 않음, 실패한 읽기는 남기지 않음, `NotReady("리포트", …)` 그대로 통과, 종목표 못 읽음 → `커버리지` 503이고 DB 미독, activity는 종목표 불필요, 버전 문제는 경고만, 순위 이름과 없는 코드의 `""`.
- 동시성 테스트는 `threading.Event`로 읽기 도중을 붙잡는다. 기다림마다 몇 초의 시간 한도를 둬 테스트가 멈춰 버리지 않게 한다.
