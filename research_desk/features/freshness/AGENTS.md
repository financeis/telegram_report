# research_desk/features/freshness/ — 모든 화면 맨 위 상태 줄의 자료 기준일(`GET /api/freshness`)

## 맡는 일

- 주소 하나 `GET /api/freshness`. 요청마다 주가 창구의 `prices.latest_run()`과 리포트 창구의 `reports.latest_report_sent_at()`을 읽어, 주가·리포트가 밀렸는지 판정해 답한다.
- 판정 규칙(`logic.py`): 기대 거래일, 주가 밀림 세 가지와 그 문장, 리포트 3일, 한국 시간 ISO 출력.
- 창구는 `router` 하나다. 명령이 없어서 창구가 라우터를 바로 묶는다.

## 맡지 않는 일

- 표·설정·파일이 없다. DB를 직접 읽지 않고(`R11 표 주인`) `core.db`를 쓰지 않는다. 주가·리포트 값은 주인 기능의 창구로만 받는다.
- 자기 준비가 없다. `AREA = "자료 기준일"`이 있지만 이 기능이 스스로 `NotReady`를 내는 경우는 없다. 주가·리포트 창구의 `NotReady("주가", …)`·`NotReady("리포트", …)`를 잡지 않고 그대로 올린다.
- 화면 표시. 문장을 빨간색으로 보여 주기, 실패 때 회색 `자료 기준일 확인 불가`, 리포트가 밀렸을 때의 문장은 `frontend/src/freshness/StatusLine.jsx`가 만든다(응답의 `reports`에는 문장이 없다).
- `peers`·`coverage` 등 다른 기능, `collector`·`tagger`·`web`·`cli` import.

## 늘 지켜야 할 것

- 응답 키와 순서: `prices{as_of, last_run_at, last_run_status, stale, note}`, `reports{latest_at, stale}`, `checked_at`. 시각은 한국 시간 ISO 초까지(`2026-10-08T09:12:00+09:00` 꼴), 모르는 값은 null. 화면이 이 키를 그대로 읽는다.
- 주가: `stale`은 `note`가 있을 때만 true다. 규칙은 이 순서로 첫 번째 맞는 것 하나: 성공 실행 없음(`as_of`가 None) → 마지막 실행 `failed` → `as_of`가 기대 거래일보다 이름. 밀림 문장 뒤에는 늘 `휴장일이면 정상입니다`를 붙인다(규칙이 공휴일을 모른다). `running`인 마지막 실행은 그 자체로 밀림이 아니다.
- 기대 거래일: 한국 시간으로 평일 `CUTOFF`(20:00) 이후면 오늘, 아니면 오늘 이전의 가장 가까운 평일. 매일 주가 갱신이 18:30에 시작하므로 끝난 뒤인 20:00이다. 스케줄러 시각을 바꾸면 이 값이 실행이 끝난 뒤인지 함께 본다.
- 리포트: 분석 대상 리포트가 없거나, 지금 − 가장 최근 `sent_at`이 3일(`REPORT_STALE_DAYS`)을 넘으면 밀림. 정확히 3일과 미래 시각은 밀림이 아니다.
- 실패 상태 값 `'failed'`는 주가 기능의 값을 옮겨 적은 것이다. 테스트가 둘이 같은지 본다 — 주가 쪽 값을 바꾸면 여기도 바꾼다.
- 캐시가 없다. 요청마다 두 창구를 다시 묻고 지금 시각을 다시 잰다. 주가 창구를 먼저 묻는다(DB 설정이 없으면 `주가 기능을 지금 쓸 수 없습니다: …`가 보인다).
- 서비스를 만들 때 아무것도 읽지 않는다(창구도 시계도). 창구 함수는 부를 때 모듈 속성으로 찾는다(`from research_desk.features import prices` 뒤 `prices.latest_run()`) — 이름을 미리 복사해 두면 테스트의 바꿔치기가 닿지 않는다.
- 핸들러는 `def`다(창구 읽기가 동기라 FastAPI가 스레드에서 돌린다). 주소와 핸들러 이름 `freshness`는 테스트가 고정한다 — 바꾸면 `/openapi.json`도 달라진다.

## 이 칸의 방식

- `logic.py`는 "지금"을 인자로 받는 순수 함수다(DB·네트워크·설정·파일 없음). 조정 가능한 값(`CUTOFF`, `REPORT_STALE_DAYS`, 문장)은 여기 상수다.
- `FreshnessService(clock=…)`가 시계를 받는다(기본: 한국 시간의 지금). 프로세스 전역 서비스는 `get_service()`.
- 새 예약 작업의 기준일을 상태 줄에 더할 때는 그 작업의 주인 기능이 창구에 "마지막 실행" 함수를 내고, 이 기능이 그것을 읽어 응답에 키를 더한다(화면도 함께 고친다).

## 테스트

- 실행: 저장소 루트에서 `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider research_desk/features/freshness`.
- 창구는 서비스가 찾는 자리에서 바꾼다: `monkeypatch.setattr(prices, 'latest_run', …)`, `monkeypatch.setattr(reports, 'latest_report_sent_at', …)`. 시각은 고정 시계로 준다.
- 꼭 덮을 것: 기대 거래일 경계(평일 19:59·20:00, 월요일 아침, 토·일), 주가 상태(실행 없음, 실패만 있음, 실패, 밀림, 정상, `running`), 리포트 3일 경계와 미래 시각, 응답 키 순서, 두 창구의 `NotReady`가 그대로 503이 되고 다음 요청에 회복, 예기치 못한 오류는 `NotReady`가 아님, 서비스를 만들 때 아무것도 읽지 않음, 실제 주가·리포트 창구를 그 기능들의 가짜 DB에 이어 끝까지 도는 경우.
