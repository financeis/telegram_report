# research_desk/features/reports/ — 분류가 끝난 리포트 읽기 창구, 기업별 리포트 목록·PDF·분석 요청 주소

## 맡는 일

- `reports` 표에서 분류가 끝난 행 읽기. 이 표는 단계별로 주인이 나뉘고, 이 기능의 몫은 읽기다(목록·상세·PDF·커버리지 집계 재료). 다른 기능은 리포트 행을 이 공개 창구로만 읽는다.
- 웹 주소 세 개: `GET /api/stocks/{code}/reports`, `GET /api/reports/{rid}/pdf`, `POST /api/reports/{rid}/analyze`.
- 리포트 공개 모양 `public_report(row, summary)`: 브라우저로 나가는 리포트의 유일한 모양이다. compare 응답의 `left`·`right`도 이 모양이다.
- 다른 기능용 창구: `report_row`(내부 행), `get_report`(공개 모양 + 저장된 요약), `period_rows(since, include_oos)`, `stock_rows(code, since)`. compare는 `get_report`, coverage는 `period_rows`·`stock_rows`를 쓴다.
- 준비 실패 `NotReady("리포트", …)`: DB 접속 설정, 종목표 파일.

## 맡지 않는 일

- `reports` 표 쓰기. 새 행은 collector, 분류 결과·잠금은 tagger, 검토 결과(승인·제외·재분류·되돌리기)는 `research_desk.features.review`가 쓴다. 구조 검사는 이 칸도 `reports`의 주인으로 보기 때문에 여기에 `insert`/`update`/`upsert`/`delete`를 넣어도 잡지 못한다. 그래도 넣지 않는다.
- `report_summaries` 표(주인 analysis, `R11 표 주인`). 요약은 `analysis.summaries_for`, 분석은 `analysis.analyze_report`로만 닿는다. 409·422·재사용·키·PDF 글자 확인은 analysis 몫이라 여기서 미리 흉내 내지 않는다. 미리 하면 확인 순서와 AI 슬롯 대기 여부가 달라진다.
- `failed_attempts` 표(collector).
- 집계와 캐시. 기간별 묶기, 배열 펼치기, 날짜 단위 나누기, 180초 캐시는 `research_desk.features.coverage`가 한다. 창구는 행을 DataFrame 그대로 넘긴다.
- 두 보고서 비교(compare), 검토 대기열과 `/api/review/…` 주소(review).
- import 금지: compare·coverage·review(셋 다 reports에 기대므로 `R6 순환 금지`), `collector`·`tagger`·`web`·`cli`(`R5 기능`), 다른 기능의 하위 모듈(`research_desk.features.analysis.service` 등). `supabase`·`pymupdf`는 `core.db`·`core.pdf`로만 쓴다(`R9 외부 도구`).
- 종목표 CSV 직접 읽기. `domain.stocks.StockList`만 쓴다.
- AI 호출.

## 늘 지켜야 할 것

**읽기 규칙** (`store.py`).
- "분석 대상" 거르기는 `domain.reports.IN_SCOPE_STATUSES`와 `out_of_scope_reason IS NULL`로만 만든다(`_in_scope`). 상태 값을 손으로 적지 않는다.
- 모든 읽기는 `EXPECTED_COLS` 15열을 그 순서로 고른다(`file_path` 포함, `caption`·`file_hash_sha256` 등은 빠짐). `select('*')`로 바꾸지 않는다. 결과 DataFrame은 비어 있어도 15열을 갖는다. coverage가 열 이름으로 바로 꺼내 쓰기 때문이다.
- 여러 행 읽기는 `.range()`로 1000행(`PAGE`)씩, 1000행보다 짧은 쪽이 올 때까지 읽는다. 마지막 쪽이 꽉 차 있으면 한 번 더 읽는다. Supabase REST는 기본 설정에서 한 응답을 1000행으로 자르므로, 쪽 나누기를 빼면 오류 없이 덜 읽힌다. 단일 행 조회만 한 번에 읽는다.
- 기간 읽기(분석 대상만): 서버에서 `published_at >= 시작일`로 거른다. 분류기가 분석 대상 행에는 늘 `published_at`을 채우므로 안전하다.
- 기간 읽기(`include_oos=True`): 분석 대상 외 행은 `published_at`이 NULL이라 서버에서 날짜로 거르면 조용히 빠진다. 서버에서는 최종 상태(`IN_SCOPE_STATUSES`)만 거르고, 받은 뒤 유효 날짜(`published_at`, 없으면 `sent_at`의 한국 시간 날짜)로 거른다. UTC 15:00은 한국 시간으로 다음 날 00:00이다. 두 날짜가 다 없는 행은 뺀다. 거른 뒤 index를 0부터 다시 매긴다.
- 종목 행: `.contains('stock_codes', [code])` + `published_at >= 시작일`이고, 코드는 받은 그대로 쓴다(0을 채우지 않는다). `.cs('stock_codes', '{코드}')`처럼 문자열로 배열을 만들지 않는다. 따옴표 없는 배열(`{001440}`)이 되어 PostgREST가 잘못 해석했던 적이 있다.
- query builder 체인은 옛 쿼리와 같은 순서로 둔다. MagicMock 테스트가 호출 순서까지 고정한다.

**공개 모양.**
- `id, title, file_name, published_at, publisher, report_type, stock_codes, company_names, sectors_major, sectors_minor, products, summary, pdf_url` 13키, 이 순서. 행에 없는 열은 `null`이고 `pdf_url`은 `/api/reports/{id}/pdf`다.
- `file_path`, 저장 폴더 경로, 서비스 키·URL은 어떤 응답에도 넣지 않는다. 키를 더하거나 빼는 것은 화면 계약 변경이다.
- `report_row`는 15열 내부 행(`file_path` 포함)을 그대로 준다. analysis가 PDF를 찾는 것처럼 서버 안 작업에만 쓴다. 브라우저로 나갈 리포트는 반드시 `public_report`를 거치고, 다른 기능이 응답에 실을 리포트가 필요하면 `get_report`를 쓴다.
- `get_report`는 행이 없으면 요약을 읽지 않고 404다.

**주소.**
- 목록: 종목표 조회는 `code.zfill(6)`, DB 조회는 받은 코드 그대로다. 종목표에 없는 코드는 DB를 읽기 전에 404 `종목을 찾을 수 없습니다.`(7자리 이상은 자르지 않으므로 404). 2000-01-01 이후 분석 대상 행을 `published_at` 내림차순, 같으면 `id` 내림차순으로 준다. 리포트 종류로 거르지 않는다(산업 리포트도 그 코드를 가지면 나온다). 요약은 나열한 id 전부로 `analysis.summaries_for`를 한 번 불러 붙인다. 업종 빈 칸은 `""`다.
- PDF: 분석 대상 행만 내준다(`review_needed`·`pending`·`processing`·분석 대상 외 → 404 `기업 보고서를 찾을 수 없습니다.`). 경로는 `core.pdf.resolve_in_storage(core.settings.storage_base_dir(), file_path)`로만 푼다. 저장 폴더 밖이거나 `.pdf`가 아니면 404 `PDF를 찾을 수 없습니다.`, 파일이 없으면 404 `로컬 PDF 파일이 없습니다. 수집 상태를 확인해 주세요.`
- 분석 요청: 행을 먼저 읽고(404), 그다음 `analysis.analyze_report(row)`를 부른다. analysis가 409를 낼 상황이어도 행이 없으면 404가 먼저다. analysis가 낸 `HTTPException`과 `NotReady("분석", …)`은 잡지 않고 그대로 올린다. 응답은 공개 모양 + `"analysis_reused": bool`이다.
- `rid`가 숫자가 아니면 FastAPI 422로 끝나고 DB를 읽지 않는다.
- 주소는 정확히 이 셋이다. 처리 함수 이름(`stock_reports`, `pdf`, `analyze`)과 인자는 옛 앱과 같고 docstring이 없다. docstring을 달면 `/openapi.json`에 설명이 생겨 옛 앱과 달라진다. 주소를 더하면 웹 조립부 테스트의 전체 주소 목록이 깨지므로 화면 계약 변경으로 다룬다.

**준비.**
- DB는 모든 읽기에 필요하다. 설정이 없으면(빈 값 포함) `NotReady("리포트", "DB 접속 설정(SUPABASE_URL, SUPABASE_SERVICE_KEY)이 없습니다")`. 창구 함수도 같은 오류를 내고, 부른 기능은 이를 자기 이름으로 바꾸지 않는다.
- 종목표는 목록 주소만 필요하다. 못 읽으면(파일 없음, 머리줄 틀림, UTF-8 아님, 빈 파일, 폴더) `NotReady("리포트", "종목표 파일을 읽을 수 없습니다")`이고, 경로와 원인은 로컬 로그에만 남긴다. PDF·분석 주소와 창구 함수는 종목표 없이 돌아야 한다.
- 버전 정보 파일이 없거나 깨졌거나 내용 지문이 다르면 경고 로그만 남기고 그대로 동작한다. 웹앱은 종목표를 이름 표시에만 쓰고 버전 값을 저장하지 않기 때문이다.
- 목록 주소는 DB를 종목표보다 먼저 준비한다(둘 다 없으면 DB 이유가 보인다).
- 실패한 준비는 다음 요청에서 다시 한다. 한 번 성공하면 프로세스가 끝날 때까지 들고 있으므로, CSV나 DB 설정 값을 바꾸면 웹앱을 다시 켜야 반영된다. `ReportsService()`를 만드는 것만으로는 아무것도 읽지 않는다.
- 이유 문구는 고정 문장이다. 경로·키 값·원인을 넣지 않는다.

## 이 칸의 방식

- `ReportsService`가 두 준비를 따로 들고 있다: `store()`(DB, `connect()`)와 `stock_list()`(종목표, `load_stock_list()`). 둘 다 `load_env()` → 설정 확인 → 만들기 순서이고, 이중 확인 잠금(`threading.Lock`)으로 한 번만 만든다. 동기 주소는 스레드 풀에서, 분석 요청의 행 읽기는 `asyncio.to_thread`에서 돌아 여러 스레드가 같은 서비스를 동시에 쓰기 때문이다.
- 서비스는 프로세스에 하나다(`get_service()`). 라우터는 `Depends(get_service)`로 받고(테스트는 `app.dependency_overrides[get_service]`로 바꾼다), 모듈 수준 창구 함수는 `get_service()`를 직접 부른다.
- analysis는 `from research_desk.features import analysis` 후 `analysis.summaries_for(...)`처럼 부를 때 속성으로 찾는다. `from research_desk.features.analysis import summaries_for`로 이름을 복사해 오면 테스트의 바꿔치기가 닿지 않는다.
- 새 읽기를 더할 때: `ReportStore` 메서드(15열, `_in_scope`/`_final_status`, `_paged_fetch`, `_to_frame`) → `ReportsService` 메서드 → 모듈 수준 창구 함수 → `__init__.py` 최상위에 직접 묶기. 묶음·집계는 부르는 쪽이 한다.
- 설정은 `core.settings`로 읽는다: `supabase_url()`, `supabase_service_key()`, `storage_base_dir()`(현재 폴더 기준), `krx_csv_path()`.

## 테스트

- **꼭 덮을 것.** 모든 `tagging_status` × 사유 조합에서 읽기 결과가 `domain.reports.is_in_scope`와 같은지, 15열·쪽 크기·쪽 창(1000행 경계 앞뒤), 유효 날짜의 한국 시간 경계, 코드 0 채우기는 종목표 조회에만, 공개 모양의 키·순서와 응답 본문에 `file_path`·저장 경로·키가 없음, 404 문구, 분석 전 404, 준비 실패·재시도·`.env` 추가 반영·종목표 유지·버전 경고.
- **가짜 DB.** `tests/fakes.py`의 `FakeSupabase`는 일부러 읽기 전용이다. 쓰기 메서드가 없어서 쓰기 코드가 생기면 테스트가 깨진다. 쓰기 메서드를 더하지 않는다. PostgREST처럼 NULL은 `eq`·`in_`·`gte`·`contains`를 통과하지 못하고 `is_(col, 'null')`만 통과한다. `select`는 고른 열만 돌려준다.
- **밖에 기대지 않게.** autouse `clean_env`가 관련 환경 변수를 지우고 `core.db.supabase_client`가 오류를 내게 바꾼다(진짜 클라이언트 금지). `db` 고정물만 가짜를 내준다. 종목표는 `tmp_path`에 진짜와 같은 머리줄(첫 칸이 `"종목\n코드"`처럼 따옴표 안에서 줄을 바꾼다)로 쓰고 `domain.stocks.write_version`으로 버전 파일을 만든다. `.env` 다시 읽기는 `env_file` 고정물로만 시험한다.
- **analysis 바꿔치기.** 이 기능이 찾는 자리에서 바꾼다: `monkeypatch.setattr(analysis, 'summaries_for', …)`, `monkeypatch.setattr(analysis, 'analyze_report', …)`.
- **주소 테스트.** `build_app(service)` = 라우터 + `get_service` 바꿔치기 + `NotReady` → 503 처리. 웹 조립부와 같은 503 모양이 나온다.
- **함정.** 창구 함수 테스트는 모듈 전역 `_service`를 `window` 고정물로 비우고 시작한다. 진짜 analysis 창구를 함께 돌릴 때는 analysis의 `_supabase_client`·`_analyzing`도 비운다(`real_analysis`). 안 비우면 준비된 가짜 클라이언트가 다른 테스트로 샌다.
