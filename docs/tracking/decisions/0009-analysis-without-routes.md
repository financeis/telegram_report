# 0009 — 분석 기능은 웹 주소 없이 받은 행만 분석하고, 분석 주소는 리포트 기능이 맡는다

## 배경
리포트 목록은 행마다 분석 결과를 붙여 보여 준다. 그러려면 리포트 기능(`features/reports`)이 분석 기능(`features/analysis`)을 써야 한다. 그런데 분석 기능이 분석할 리포트 행을 직접 읽으면 두 기능이 서로를 쓰게 되어 의존이 순환한다(구조 검사가 금지). 검토 처리 직후 커버리지 숫자를 바로 바꾸는 연결도 어디에 둘지 정해야 했다.

## 결정
- `features/analysis`는 웹 주소가 없다. 행을 받아 분석만 하고(`analyze_report(row)`), 분석 결과 표(`report_summaries`)의 주인이다. 웹 서버 전체의 AI 자리 2개(`ai_slot()`)와 AI 연결(`phase2_llm()`)도 여기서 공개한다.
- `POST /api/reports/{rid}/analyze`는 `features/reports`가 맡는다: 행을 읽고 `analysis.analyze_report`를 부른다.
- `features/compare`는 `reports`로 리포트를 읽고, `analysis`의 AI 자리·연결·저장 창구를 쓴다. 비교 결과 저장도 `analysis`를 거친다.
- `features/review`가 `features/coverage`의 `invalidate()`를 불러 커버리지 캐시를 비운다. 웹 조립부에는 이 연결을 두지 않는다(조립부는 등록만).

## 버린 대안
- **분석이 리포트를 직접 읽음**: `reports ↔ analysis` 순환.
- **조립부(web)가 검토 후 캐시 비우기를 연결**: 조립부에 업무 처리가 생긴다.
- **분석·비교가 각자 AI 동시 제한**: 웹 서버 전체 동시 AI 호출 2개라는 동작이 깨진다.

## 결과
- 기능 의존 방향은 `reports → analysis`, `compare → reports, analysis`, `coverage → reports`, `review → coverage`다. 새 기능은 이 방향에 순환을 만들면 안 된다.
- 분석과 비교는 같은 AI 자리 2개를 나눠 쓴다. 분석 2개가 돌고 있으면 비교 해석문은 기다린다.
- 확인 순서상 분석 기능은 AI 자리를 잡은 뒤 단일종목·재사용을 확인한다. 그래서 재사용될 요청도 자리가 빌 때까지 기다릴 수 있다.
