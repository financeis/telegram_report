# research_desk/features/review/ — 분류기가 정하지 못한 리포트(`review_needed`)의 수동 검토와 되돌리기

## 맡는 일

- 웹 주소: `GET /api/review?skipped=…`, `GET /api/review/{rid}/pdf`, `GET /api/review/{rid}/preview`, `GET /api/review/{rid}/pages/{page}`, `POST /api/review/{rid}/action`, `POST /api/review/undo/{token}`. 공개 창구는 `router` 하나다.
- `reports` 표에서 검토가 하는 읽기·쓰기(이 몫의 표 주인): 대기 수, 다음 대기 행, id로 한 행, 조건부 쓰기(검토 처리, 되돌리기).
- 처리별로 쓰는 값(`rules.py`)과 되돌리기 기록(서버 메모리).
- 처리 결과: 승인 → `verified`, 제외 → `verified` + 분석 대상 외 행 모양 + 사유, 재분류 → 분류 칸을 모두 비우고 `pending`(다음 `tag run`이 새로 분류한다).

## 맡지 않는 일

- `report_summaries`(분석 결과)는 `features/analysis`의 표다. 재분류해도 분석 결과는 건드리지 않는다. `failed_attempts`는 수집기의 표다(`R11 표 주인`).
- 분석 대상 외 행 모양(비우는 열·남기는 열)은 `domain.reports.oos_row_shape`에만 있다. 제외 처리는 그것을 쓰고 열 목록을 여기 다시 적지 않는다. 분류기 쓰기와 같은 모양이어야 하기 때문이다. 사유 값의 기준도 `domain.reports.OOS_REASONS`(`vocabulary.yaml`)다.
- 재분류가 쓰는 "되돌리는 모양"은 `domain.reports.pending_reset_shape`에만 있다. `rules.build_pending_reset_payload()`는 그것을 그대로 돌려준다. 분류기의 `tag requeue`도 같은 모양으로 행을 되돌리므로 열 목록을 여기 다시 적지 않는다.
- 다시 분류하는 일은 분류기(`python -m research_desk tag run`)가 한다. 검토는 행을 `pending`으로 되돌릴 뿐이고 `research_desk.tagger`를 import하지 않는다(`R5 기능`).
- PDF 경로 확인·쪽 수·쪽 그림은 `core.pdf`가 한다. `pymupdf`를 직접 import하지 않는다(`R9 외부 도구`). DB 클라이언트는 `core.db.supabase_client`로만 만든다.
- 커버리지 캐시는 coverage의 것이다. 검토는 `coverage.invalidate()`를 부르기만 한다. 분석 대상 리포트 조회는 `features/reports`가 한다.
- `web`·`cli`·`collector`·다른 기능의 하위 모듈은 import하지 않는다(`R5 기능`).

## 늘 지켜야 할 것

- 대기열(`GET /api/review`)
  - `tagging_status = 'review_needed'` 행 중 `tagged_at`이 가장 오래된 것 하나를 낸다. 건너뛴 id(`?skipped=1&skipped=2`)는 뺀다. 건너뛰기는 브라우저 세션에만 있고 서버는 기억하지 않는다.
  - `remaining`은 `review_needed` 전체의 정확한 개수다(`count='exact'`). 건너뛴 행도 센다.
  - 행은 모든 열에서 `file_path`만 빼고 `pdf_url: /api/review/{id}/pdf`를 붙인다. `file_hash_sha256`·`caption` 같은 나머지 열은 그대로 나간다. 빼야 하는 것은 로컬 경로뿐이고, 내용 지문은 민감한 값이 아니라 그대로 둔다.
  - 응답은 `{"remaining", "report"}`이고, 대기 행이 없으면 `"report": null`. `skipped`에 숫자가 아닌 값이 있으면 읽기 전에 422.
- PDF·미리보기·쪽 그림
  - 행은 id로 읽고 상태를 보지 않는다. 행이 없으면 404 `검토할 보고서가 없습니다.`
  - 파일은 `STORAGE_BASE_DIR`(기본 `./reports`) 안의 `.pdf`만 낸다. 밖이거나 PDF가 아니면 404 `PDF를 찾을 수 없습니다.`, 파일이 없으면 404 `로컬 PDF 파일이 없습니다. 수집 상태를 확인해 주세요.`(리포트 PDF와 같은 문구).
  - 미리보기는 `{"page_count", "preview_pages": min(3, page_count)}`.
  - 쪽 그림은 1~3쪽만, 120dpi PNG, `Cache-Control: private, max-age=300`. 1~3 밖은 아무것도 읽기 전에 404 `미리보기는 첫 3페이지까지 제공됩니다.`, PDF에 없는 쪽은 404 `페이지가 없습니다.`
- 처리(`POST /api/review/{rid}/action`, 본문 `{"action": "verify"|"oos"|"retag", "reason"}`)
  - `oos`인데 사유가 없거나 null이면 읽기 전에 422 `분석 대상 제외 사유를 선택해 주세요.` 화면이 이 문장을 보여 줄 수 있게 문자열 `detail`로 낸다. 그 밖의 action·사유 값(대소문자 다름, 빈 문자열 포함)은 FastAPI 검증 422다. `verify`·`retag`에 함께 온 사유는 무시한다.
  - 순서: 행 읽기(없으면 404) → `review_needed`가 아니면 409 `다른 작업에서 처리한 보고서입니다. 목록을 새로고침해 주세요.` → 쓸 값 만들기 → `tagging_status = 'review_needed'`일 때만 쓰기. 그 사이 상태가 바뀌어 쓴 행이 없으면 409 `보고서 상태가 바뀌었습니다. 새로고침해 주세요.`이고, 아무것도 쓰지 않으며 되돌리기 기록도 남기지 않는다.
  - 승인은 `tagging_status = 'verified'`만 쓴다. 분류는 그대로다.
  - 제외는 `{'tagging_status': 'verified', **oos_row_shape(row, reason)}`를 쓴다. 발행일은 NULL, 종목·회사명·업종(대·중)·제품은 `[]`, 리포트 종류·발행처·발행처 종류·애널리스트·제목·원문 종목·원문 회사명은 행의 값 그대로, 그리고 사유.
  - 재분류는 `domain.reports.pending_reset_shape()`로 스냅샷 22열을 모두 쓴다. `tagging_status = 'pending'`, 배열 열(`analysts`, `stock_codes`, `company_names`, `stock_codes_raw`, `company_names_raw`, `sectors_major`, `sectors_minor`, `products`)은 `[]`, 나머지는 NULL. 배열 열은 DB에서 `NOT NULL`이라 NULL을 쓰면 쓰기가 실패한다. 이 값은 모양을 domain으로 옮기기 전과 같다(재분류 테스트를 바꾸지 않고 통과한다).
  - 처리가 쓰는 열은 스냅샷 22열(분류 칸 14개 + 분류 메타 8개, `rules.SNAPSHOT_COLUMNS`) 안에만 있다. 수집기 열(`id`, `message_id`, `chat_username`, `file_path`, `file_name`, `file_size_bytes`, `file_hash_sha256`, `caption`, `downloaded_at`, `sent_at`)은 처리도 되돌리기도 쓰지 않는다. 22열 밖을 쓰는 처리를 만들면 되돌릴 수 없게 된다.
  - 응답은 `{"undo_token": <32자리 16진수>, "report_id"}`다. 되돌릴 때 브라우저는 리포트 내용을 보내지 않는다.
- 되돌리기(`POST /api/review/undo/{token}`)
  - 토큰 기록이 없으면(서버 재시작 포함) DB를 읽기 전에 409 `되돌릴 작업이 없거나 서버가 재시작되었습니다.`
  - 행이 사라졌으면 404 `검토할 보고서가 없습니다.`
  - 지금 행의 22열이 '처리 후' 기록과 하나라도 다르면 409 `후속 작업이 처리한 보고서라 되돌릴 수 없습니다.`
  - '처리 전' 22열을 쓰되, `tagging_status`, `tagged_at`, `tagging_locked_at`, `tagging_worker_id`가 '처리 후' 값 그대로일 때만 쓴다(NULL은 `IS NULL`로 비교). 그 사이 분류기가 행을 가져갔으면 쓰지 않고 409 `후속 작업이 시작되어 되돌릴 수 없습니다.`
  - 성공한 되돌리기만 토큰을 지운다. 거절된 되돌리기는 기록이 남아 다시 시도할 수 있다.
  - 기록은 서버 메모리에 최대 100개다. 넘치면 가장 오래된 것부터 버리고, 서버를 다시 켜면 모두 사라진다.
- 처리와 되돌리기는 프로세스 안 잠금 하나로 한 번에 하나만 돈다. 같은 행에 동시에 들어온 처리는 하나만 쓰고 나머지는 409 `다른 작업에서 처리한 보고서입니다. 목록을 새로고침해 주세요.`
- 처리·되돌리기가 성공하면 쓰기가 끝난 바로 뒤(잠금 밖)에 `coverage.invalidate()`를 한 번 부른다. 거절·실패·준비 안 됨·읽기 요청에서는 부르지 않는다. 검토 결과가 커버리지 숫자에 바로 보이게 하는 장치다.
- DB 설정이 없거나 빈 값이면 DB가 필요한 주소는 `NotReady("검토", "DB 접속 설정(SUPABASE_URL, SUPABASE_SERVICE_KEY)이 없습니다")` → 503. 요청 값 검사, 쪽 범위, 모르는 토큰은 준비보다 먼저라 설정이 없어도 제 답(422·404·409)이 나간다.
- 응답에 `file_path`, 저장 폴더 경로, Supabase URL·키를 넣지 않는다.
- 사유 목록은 세 곳에 있다: `domain/vocabulary.yaml`(기준), `router.py`의 `ReviewAction.reason` `Literal`(`/openapi.json`의 `enum`을 그대로 두려고 직접 적었다), 화면의 `frontend/src/ReviewQueue.jsx`. 앞의 둘이 같은지는 테스트가 확인하지만 화면 쪽은 확인하지 않는다. 사유를 바꾸려면 이 셋과 DB 제약(마이그레이션)을 함께 바꾼다.
- 검토 쪽에서 막을 수 없는 알려진 경쟁: `tag escalate`와 `tag run --row-ids`는 상태를 보지 않고 행을 다시 쓰므로 검토 결과를 덮을 수 있다. 검토의 조건부 쓰기는 자기 쓰기만 지킨다. `tag requeue --apply`는 웹앱이 켜져 있으면 실행을 거절하고(종료 코드 1), `verified` 행은 고르지 않는다.

## 이 칸의 방식

- `service.py`의 `ReviewService`가 대기열·PDF·처리·되돌리기와 준비를 맡는다. 되돌리기 기록과 잠금이 서비스 객체에 있으므로 모든 요청이 `get_service()`의 프로세스 전역 서비스 하나를 쓴다. 웹 서버를 여러 프로세스로 띄우면 다른 프로세스에서는 토큰이 없는 것이 된다.
- 준비: 처음 DB를 쓸 때 `settings.load_env()` → `SUPABASE_URL`·`SUPABASE_SERVICE_KEY` → `core.db.supabase_client`. 성공하면 클라이언트 하나를 계속 쓰고, 실패하면 다음 호출에서 다시 준비한다. PDF 폴더는 요청마다 `settings.storage_base_dir()`로 읽는다.
- `store.py`의 `ReviewStore`는 Supabase REST로 네 가지만 한다: 대기 수, 다음 대기 행, id로 한 행, `update_if(rid, values, expected)`. `update_if`는 `id`로 고른 뒤 `expected`의 열을 주어진 순서대로 `eq`(None이면 `is_('null')`)로 걸고, 쓴 행을 돌려받는다(supabase-py 기본 `return=representation`). 빈 목록이면 행이 바뀌었거나 없어서 아무것도 쓰지 않은 것이다.
- `rules.py`는 DB 없이 쓸 값만 만든다(제외는 `oos_row_shape`, 재분류는 `pending_reset_shape`를 부른다). 호출마다 새 목록을 돌려줘 호출끼리 목록을 나눠 갖지 않는다.
- 핸들러는 `def`로 둔다. 서비스가 동기 DB 호출과 `threading.Lock`을 쓰므로 FastAPI 스레드에서 돌아야 한다.
- 핸들러 이름(`review`, `review_pdf`, `review_preview`, `review_page`, `review_action`, `review_undo`), 인자, 요청 본문 모델 `ReviewAction`, 주소 순서는 `/openapi.json`을 그대로 두려고 고정했다. 바꾸거나 docstring을 달지 않는다.

## 테스트

- 실행: 저장소 루트에서 `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider research_desk/features/review`.
- `tests/conftest.py`
  - `clean_env`(autouse): 검토가 읽을 수 있는 변수를 지우고 `core.db.supabase_client`를 거부하게 바꾼다.
  - `invalidations`(autouse): `coverage.invalidate`를 창구 모듈에서 바꿔 끼워 부른 횟수를 센다. `probe`를 걸면 불린 순간의 행 상태를 남겨, 쓰기 뒤에 불렸는지 확인할 수 있다.
  - `db`: DB 설정을 넣고 `FakeSupabase`를 건넨다. 건넨 (url, key)를 `made`에 남겨 클라이언트를 한 번만 만드는지 본다. `memory`: 옮겨 온 테스트용 단순 가짜 `MemoryDB`. `storage`: 임시 PDF 폴더를 `STORAGE_BASE_DIR`로 준다.
- `tests/fakes.py`의 `FakeSupabase`는 PostgREST처럼 동작한다. NULL은 `eq`·`in`과 그 부정을 모두 통과하지 못하고 `is_('null')`만 통과한다. 오름차순 정렬에서 NULL은 맨 뒤, `count='exact'`는 `limit`과 상관없이 센다, `update`는 쓴 행을 돌려준다. `before_write`에 넣은 함수는 다음 쓰기 직전에 한 번 돌아, 읽기와 쓰기 사이에 다른 작업자가 행을 바꾼 상황을 만든다. `waiting(rid)`는 수집기·분류기가 남기는 모든 열을 갖춘 `review_needed` 행이다.
- PDF는 `pymupdf`로 임시 폴더에 만든다(테스트는 `R9 외부 도구`에서 빠진다). 쪽 그림은 `Matrix(120/72)`로 그린 PNG와 바이트까지 같아야 한다.
- 꼭 지킬 검증: 처리별로 쓰는 값이 고정값과 정확히 같음(제외 = `verified` + `oos_row_shape`), 조건부 쓰기의 필터와 그 순서, 404·409·422 문구, 422·쪽 범위 404·모르는 토큰 409는 DB를 읽기 전, 22열 스냅샷과 4열 비교 후 쓰기, 토큰은 한 번만·거절되면 남음·최대 100개·오래된 것부터 버림, 한 번에 하나, `invalidate()`는 성공 뒤에만, 준비 실패 503 문구·다음 요청 회복·`.env`에 더한 값 반영, 응답에 경로·키 없음, 사유 `Literal` = `OOS_REASONS`, `/openapi.json` 항목.
- 프로세스 전역 서비스로 시험할 때는 `window` fixture(`_service = None`)로 시작한다.
