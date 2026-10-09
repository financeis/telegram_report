# research_desk/features/compare/ — 같은 기업 두 보고서의 비교와 비교 해석문

## 맡는 일

- 웹 주소 두 개: `GET /api/compare?left=&right=`(비교 모양, AI를 부르지 않음), `POST /api/compare/analyze` `{"left", "right"}`(비교 모양 + 해석문).
- 고를 수 있는 짝, 앞·뒤 순서, 같은 발행처 판정, 수치 대조, 목표주가 변화, 저장된 해석문을 보여 줄지의 판단.
- 해석문 AI 호출 `diff_one`, 그 프롬프트(`prompts.py`)와 응답 모양 `DiffResult`.
- 비교 모양: `left, right, same_publisher, metrics, narrative, target_price_change`(이 순서). `left`·`right`는 리포트 공개 모양(저장된 요약 포함)이다.

## 맡지 않는 일

- 표. compare에는 `store.py`가 없다. 리포트는 `reports.get_report`로 읽고, 해석문은 `analysis.save_comparison`으로 저장한다. compare 안에 `.table(...)`이나 SQL 문자열을 두면 `R11 표 주인` 위반이다.
- `reports.report_row`. `file_path`가 든 내부 행이라 비교 응답에 실리면 안 된다. `get_report`만 쓴다.
- Phase 2 설정(`LLM_MODEL_PHASE2`, `OPENAI_MODEL_PHASE2`, `PHASE2_*`). 모델 이름·호출 시간 한도·AI 클라이언트는 `analysis.phase2_llm()`이 준다. compare에는 `settings.py`가 없다.
- 동시 호출 제한. 자기 세마포어를 만들지 않고 `analysis.ai_slot()`을 쓴다.
- 리포트 분석(요약 만들기)은 analysis 몫이다. 요약이 없으면 422로 돌려보낼 뿐 대신 분석하지 않는다.
- 자기 이름(`비교`)으로 내는 `NotReady`. compare는 따로 준비할 것이 없다. DB 설정이 없으면 reports의 `NotReady("리포트", …)`, 키가 없으면 analysis의 `NotReady("분석", …)`가 그대로 화면까지 간다. 잡아서 바꾸지 않는다.
- import는 `research_desk.features.analysis`·`research_desk.features.reports` 공개 창구만 쓴다. 하위 모듈(`…analysis.service`, `…reports.store` 등)을 import하면 `R5 기능` 위반이다. `openai`·`anthropic`은 `core.llm`으로만 쓴다(`R9 외부 도구`). `collector`·`tagger`·`web`·`cli`, 옛 코드도 import하지 않는다.

## 늘 지켜야 할 것

**읽기.** 두 주소 모두 요청의 `left`를 먼저, 그다음 `right`를 `reports.get_report`로 읽는다. 없거나 분석 대상이 아닌 보고서는 reports의 404 `기업 보고서를 찾을 수 없습니다.`이고, `left`가 404면 `right`는 읽지 않는다. 쿼리·본문의 `left`/`right`가 없거나 정수가 아니면 FastAPI 422로 끝나고 아무것도 읽지 않는다.

**짝 규칙** (`logic.comparison`, 이 순서로 확인, 모두 422).
1. 둘 중 하나라도 `report_type`이 `단일종목`도 `None`도 아니면 `금융 비교는 단일종목 보고서 두 개를 선택해 주세요.` 같은 보고서를 두 번 골랐어도 이 확인이 먼저다.
2. 같은 `id`면 `서로 다른 보고서 두 개를 선택해 주세요.`
3. `stock_codes`가 하나도 겹치지 않으면 `같은 기업의 보고서를 선택해 주세요.` 하나만 겹쳐도 된다. 코드가 `None`이나 `[]`인 보고서끼리는 같은 기업이 아니다.

**순서.** `(published_at or '', id)` 오름차순으로 정렬해 앞 문서를 `left`, 뒷 문서를 `right`로 둔다. 날짜가 없는 보고서가 앞, 같은 날이면 `id`가 작은 쪽이 앞이다. 요청에서 어느 쪽에 넣었든 결과는 같다. 모든 "이전/현재"가 이 순서를 따른다. 수치의 `previous`는 앞 문서, `current`는 뒷 문서이고, 화면은 `previous_evidence`를 `left`의 PDF, `current_evidence`를 `right`의 PDF 쪽 링크로 연다. 순서를 뒤집으면 근거 링크가 엉뚱한 문서를 연다.

**같은 발행처.** 두 `publisher`가 모두 있고(빈 문자열은 없는 것) 같을 때만 `same_publisher = True`다. 하나라도 모르면 다른 발행처로 본다. 분류기는 발행처를 발행처 사전의 정식 이름이나 빈 값으로만 저장한다(사전에 없거나 AI가 정식 이름으로 답하지 못하면 빈 값). 모르는 둘을 같은 데스크로 묶으면 안 된다. 비교는 저장된 글자 그대로다 — 이름을 맞추는 일은 분류기와 사전의 몫이고 compare는 별칭을 풀지 않는다. 사전 규칙상 기술분석보고서는 표지의 작성기관과 상관없이 `한국IR협의회`로 저장되므로, 작성기관이 다른 두 기술분석보고서도 같은 발행처가 된다.
- `same_publisher`는 GET마다 지금 값으로 다시 계산한다. 반면 저장된 해석문과 `prev_match_type`·`comparison_details.previous_publisher`는 만들 때의 발행처로 쓰인 기록이라, 나중에 다시 분류해 발행처가 바뀌어도 저절로 바뀌거나 지워지지 않는다. compare는 그것을 감지하지 않는다. 필요하면 발행처 사전을 고친 뒤의 운영 절차로 비운다.

**수치 대조** (`compare_financials(앞 요약, 뒷 요약)`).
- 짝 열쇠는 (`metric`, `fiscal_period`, `unit`, `currency`, `accounting_basis`, `value_type`, `scenario`) 7개이고, 문자열 그대로 모두 같아야 한다. 단위 환산, 연도 넘김(2026 → 2027), 비슷한 이름 맞추기를 하지 않는다.
- 열쇠가 없어 비교하지 않는 지표: `fiscal_period`가 비었음, `accounting_basis`가 `연결`/`별도`가 아님(`미기재` 등), `scenario`가 `기본`/`낙관`/`비관`이 아님.
- 같은 열쇠가 어느 한쪽에라도 둘 이상 있으면 그 열쇠는 통째로 뺀다. 덮어써서 하나를 고르지 않는다.
- `value_type`이 `실적`이면 비교하지 않는다. `추정`·`가이던스`는 같은 종류끼리 짝지어진다. 어느 쪽 `value`든 `None`이면 뺀다.
- 행 순서는 뒷 문서의 지표 순서다. 행 키는 `metric, fiscal_period, unit, currency, accounting_basis, value_type, scenario, previous, current, delta, change_pct, change_label, previous_evidence, current_evidence` 순서다. `previous_evidence`는 앞 문서 지표의 `evidence`, `current_evidence`는 뒷 문서 지표의 `evidence`다(한 보고서 안의 `previous_value`·`previous_evidence`는 쓰지 않는다).
- 이 7개 기준은 analysis 추출 프롬프트가 모델에게 "모두 같아야 짝이 된다"고 설명하는 기준과 같다. 한쪽을 바꾸면 다른 쪽 문구도 맞춘다.

**변화 표시** (`numeric_change`, `change_label`은 화면에 그대로 나간다).
- 한쪽 값이 없으면 `delta`·`change_pct`가 `None`이고 `비교값 없음`.
- `unit`이 `%`면 차이를 %p로 쓴다(`+2.00%p`, `change_pct`는 `None`). 0이나 음수가 끼어도 같다.
- 0이나 음수가 끼면 증감률을 계산하지 않는다(`change_pct` `None`). 음수→양수는 이익 지표(`영업이익`, `순이익`, `지배주주순이익`, `세전이익`, `EPS`)면 `흑자 전환`, 아니면 `음수→양수`. 양수→음수는 `적자 전환` / `양수→음수`. 그 밖(둘 다 음수, 0이 낀 경우)은 `+5.00 억원 (증감률 미표시)` 꼴.
- 둘 다 양수면 `change_pct = delta / previous * 100`, 표시는 `+20.00%`.
- 목표주가 변화는 두 문서 각자의 `summary.target_price_new`로만, 단위 `원`으로 계산한다. 하나라도 없으면 `None`. 한 문서 안의 `target_price_old`는 쓰지 않는다.

**저장된 해석문 보여 주기.** 뒷 문서 요약의 `prev_report_id`가 앞 문서 `id`일 때만 그 `diff_narrative`를 `narrative`로 보여 준다. 다른 짝으로 저장된 것, 앞 문서에 저장된 것은 보여 주지 않는다(`None`). GET은 AI를 부르지 않고, 저장하지 않고, 키가 필요 없다.

**새 해석문** (POST).
1. 두 요약 중 하나라도 없으면 422 `선택한 두 보고서를 먼저 분석해 주세요.` 슬롯·키 확인보다 먼저다.
2. 이 짝의 저장된 해석문이 있으면 그대로 돌려준다(응답이 GET과 같다). 슬롯·키·AI·저장 모두 없다.
3. 아니면 `analysis.ai_slot()` 안에서 `analysis.phase2_llm()`(슬롯을 잡은 뒤에야 키 확인, 없으면 `NotReady("분석", …)`) → `diff_one`(앞 요약이 이전, 뒷 요약이 현재, `same_publisher`/`cross_publisher`, 모르는 발행처는 프롬프트에만 `미상`) → `analysis.save_comparison(뒷 id, 앞 id, match, narrative, {"metrics": 응답의 metrics, "previous_publisher": 앞 발행처 원래 값, "previous_published_at": 앞 발행일})`. 응답은 비교 모양에서 `narrative`만 새 값이다.
- 1·2번과 404·422는 슬롯을 기다리지 않는다. 슬롯은 키 없음·AI 실패·거부 뒤에도 풀린다.
- `prev_match_type`에는 `same_publisher`와 `cross_publisher`만 쓴다.
- 뒷 문서 하나에 해석문은 하나다. 다른 앞 문서와 새로 만들면 덮어쓰고, 뒷 문서를 다시 분석하면 analysis가 비운다.
- 모델이 `diff_narrative`를 `null`로 주면 그대로 저장하고 `null`을 돌려준다. 다음 POST는 저장된 해석문이 없다고 보고 AI를 다시 부른다.
- AI 실패·거부면 아무것도 저장하지 않는다. 재시도·시간 한도는 analysis와 같다: 시도마다 `wait_for(timeout_s)`, 일시 오류는 `core.llm.call_with_retry`가 5초 뒤 한 번 더, 그 밖의 오류와 거부(`RuntimeError("LLM이 응답을 거부했습니다: …")`)는 바로 실패.
- `comparison_details`는 기록으로 저장만 한다. 화면과 GET은 읽지 않고, GET의 `metrics`는 매번 두 요약에서 새로 계산한다.

**해석문 프롬프트와 스키마.** 프롬프트 글은 운영 중인 계약이고 테스트가 문구 일부를 고정한다.
- 같은 발행처면 그 데스크의 시계열 수정으로 쓰고, 다른 발행처면 두 데스크의 견해 비교로 쓴다. 다른 발행처를 같은 애널리스트의 수정이나 시장 컨센서스로 부르게 하지 않는다. 수집한 리포트 안의 비교일 뿐이다.
- "이전 요약이 없으면 `diff_narrative=null`" 방어 줄을 지우지 않는다.
- AI는 두 요약 JSON만 받는다. user 메시지는 `<comparison_context>` + 두 요약(`ensure_ascii=False`, 한글 그대로) + 빈 `<optional_previous_excerpt>`·`<optional_current_excerpt>` 블록이고, PDF 원문은 보내지 않는다.
- `DiffResult`는 `diff_narrative: Optional[str] = None` 한 칸이고 클래스 docstring이 없다. docstring을 달면 스키마 `description`이 생겨 모델이 받는 내용이 바뀐다. 호출은 `constrained=True`다(Claude는 구조화 출력, codex·OpenAI는 엄격 스키마).

**주소 모양.** 주소는 정확히 둘이다. 처리 함수 `compare(left: int, right: int)`, `comparison_analysis(body: ComparisonBody)`, 본문 모델 `ComparisonBody{left: int, right: int}`이고 셋 다 docstring이 없다. `/openapi.json`의 이 항목들이 옛 앱과 글자까지 같아야 하고, 테스트가 그대로 비교한다.

## 이 칸의 방식

- GET은 동기 함수라(FastAPI가 스레드 풀에서 돌린다) `reports.get_report`를 바로 부른다. POST는 비동기라 읽기와 저장을 `asyncio.to_thread`로 돌리고, AI 호출만 이벤트 루프에서 기다린다.
- 다른 기능의 이름은 부를 때 패키지 속성으로 찾는다: `reports.get_report(...)`, `analysis.ai_slot()`, `analysis.phase2_llm()`, `analysis.save_comparison(...)`. 이름을 복사해 import하면 테스트의 바꿔치기가 닿지 않는다. 반대로 `diff_one`은 `service.py`가 이름으로 가져와 쓰므로, 바꿔치려면 `llm.diff_one`이 아니라 `service.diff_one`을 바꾼다.
- `logic.py`는 DB·네트워크 없이 계산하고, 짝 규칙 위반은 `HTTPException(422, …)`로 바로 낸다.
- 비교에 새 데이터가 필요하면 그 데이터의 주인 기능(리포트 행은 reports, 분석 결과는 analysis)이 공개 창구에 이름을 더하고, compare는 그 이름을 쓴다.
- `phase2_llm()`이 준 클라이언트는 `async with`로 쓰고 닫는다.

## 테스트

- **꼭 덮을 것.** 짝 규칙 세 문구와 확인 순서(두 주소 모두), 정렬(같은 날 `id`, 날짜 없음), 발행처의 모든 경우(`None`, `''` 포함), 열쇠 7개 하나만 달라도 짝 없음, 중복 열쇠, 실적 제외, 변화 표시 문구, 저장된 해석문의 정확한 짝, POST 흐름(두 요약 필요, 저장된 것은 AI·슬롯 없이, 새 해석문은 뒷 문서에 저장, `null` 해석문), 슬롯 공유(분석 두 건이 슬롯을 잡고 있으면 기다림, 세 건이 동시에 오면 두 건씩, 실패·거부·키 없음 뒤 슬롯 풀림), 준비 실패가 `리포트`/`분석` 이름으로 보임, `/openapi.json` 항목, `DiffResult`가 `anthropic.transform_schema`와 `openai.pydantic_function_tool` 엄격 스키마를 거친 모양.
- **고정물** (`tests/conftest.py`). autouse `clean_env`가 관련 환경 변수를 모두 지우고, `core.llm._codex_bin`을 없음으로, `core.db.supabase_client`를 오류로 바꾼다. 그래서 진짜 `analysis.phase2_llm`은 테스트가 키를 넣지 않는 한 `NotReady("분석", …)`를 낸다. `world`는 `reports.get_report`(분석 대상 규칙·공개 모양·404)와 `analysis.save_comparison`(기존 요약의 비교 칸 네 개)을 흉내 낸다. `ai`는 `analysis.phase2_llm`을 바꿔 호출 기록·최대 동시 수·게이트를 준다.
- `analysis.ai_slot`은 바꿔치지 않는다. 진짜 2개 한도를 analysis와 함께 쓰는지가 시험 대상이다.
- 진짜 창구 테스트(`test_real_windows.py`)는 `LLMClient.parse`와 `core.db.supabase_client`만 바꾸고, `fresh_windows`로 reports의 `_service`, analysis의 `_supabase_client`·`_analyzing`을 비운다. 여기서 실행된 모든 쿼리가 reports·analysis 것인지(compare가 직접 실행한 쿼리가 없는지)도 본다.
- 대기는 시계 기준(`until`, `never`)으로 한다. 읽기가 작업 스레드에서 돌아 루프 몇 바퀴로는 판정할 수 없다. 기다리는 await에는 `LIMIT_S` 한도를 건다.
