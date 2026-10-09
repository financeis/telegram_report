# research_desk/features/analysis/ — 리포트 1건 재무 분석, 분석 결과 표(`report_summaries`)의 주인

## 맡는 일

- `report_summaries` 표의 유일한 주인. 분석 결과 저장, 현재 버전 결과 조회, 두 보고서 비교 결과(비교 칸 네 개) 저장을 모두 여기서 한다. 다른 기능은 공개 창구로만 이 표에 닿는다.
- 리포트 1건 분석 `analyze_report(row)`: reports가 읽어 넘긴 행 → PDF 글자 → AI 추출 → 후처리(목표주가 방향, 숫자 근거 확인) → 저장.
- 웹 서버 전체의 AI 동시 호출 한도 2개(`ai_slot()`). compare의 해석문 호출도 이 한도를 나눠 쓴다. 예외는 peers의 테마 검색 질의 임베딩 하나로, 그것은 peers 자체 자리(2개)를 쓰고 이 한도를 나눠 쓰지 않는다.
- 분석·비교의 AI 연결 준비 `phase2_llm()` → `(client, model, timeout_s)`. 모델 키·codex CLI 확인과 그 실패 `NotReady("분석", …)`도 여기서 낸다.
- Phase 2 설정: `LLM_MODEL_PHASE2`(옛 이름 `OPENAI_MODEL_PHASE2`, 기본 `gpt-6-luna`), `PHASE2_PER_REPORT_TIMEOUT_S`(180), `PHASE2_MAX_INPUT_TOKENS`(30000), `PHASE2_SUMMARY_VERSION`(`llm-summary@1.0`).
- 재무 추출 프롬프트(`prompts.py`)와 응답 모양(`ExtractionResult`, `FinancialDetails`).
- 공개 이름은 `__init__.py`가 묶은 다섯 개뿐이다: `analyze_report`, `summaries_for`, `save_comparison`, `ai_slot`, `phase2_llm`.

## 맡지 않는 일

- 웹 주소. analysis에는 `router`가 없고 웹 조립부 기능 목록(`FEATURES`)에도 넣지 않는다. `POST /api/reports/{rid}/analyze`는 `research_desk.features.reports`가 받아 행을 읽고(404) `analyze_report`를 부른다. 리포트 목록이 분석 결과를 붙이려면 reports가 analysis에 기대야 하는데, analysis가 행까지 직접 읽으면 둘이 서로 물리기 때문이다.
- `reports` 표. 읽지도 쓰지도 않는다(`R11 표 주인`). 필요한 값은 넘겨받은 행에서 꺼낸다: `id`, `report_type`, `file_path`, 메타데이터 4개(`publisher`, `stock_codes`, `published_at`, `title`).
- 다른 기능 import. reports·compare는 analysis에 직접, coverage·review·peers·freshness는 reports를 거쳐 기대므로 하나라도 import하면 `R6 순환 금지`에 걸린다. analysis는 기능 의존이 없는 칸이다.
- 비교 판단. 짝 규칙, 수치 대조, 해석문 프롬프트와 `DiffResult`는 `research_desk.features.compare` 몫이다. analysis는 받은 값을 비교 칸에 저장만 한다.
- 외부 도구 직접 import(`R9 외부 도구`). PDF는 `core.pdf`, AI는 `core.llm`, DB는 `core.db`, `.env`는 `core.settings`로만 닿는다. `collector`·`tagger`·`web`·`cli`와 옛 코드(`langgraph_tagger` 등)도 import하지 않는다.
- `SUPABASE_DB_URL`과 `PHASE2_MAX_CONCURRENT`. 분석은 Supabase REST만 쓰고 동시 호출 수는 고정값이라, 두 변수는 값이 있어도 읽지 않는다.

## 늘 지켜야 할 것

**확인 순서** (`analyze_report`). 앞에서 막히면 뒤는 일어나지 않는다. 행 확인(404 `기업 보고서를 찾을 수 없습니다.`)은 reports가 1번보다 먼저 한다.
1. 같은 `id`가 진행 중이면 409 `이 보고서는 분석 중입니다. 잠시 후 새로고침해 주세요.` 슬롯을 기다리지 않고 바로 거절한다. 진행 표시(`_analyzing`)는 슬롯을 기다리기 전에 걸고, 결과와 상관없이 `finally`에서 푼다.
2. 여기부터 끝까지 AI 슬롯 하나를 잡고 진행한다. 두 슬롯이 다 차 있으면 아래의 422와 재사용 응답도 기다린다. 옛 앱 동작을 그대로 지킨 것이므로 422·재사용을 슬롯 밖으로 빼지 않는다.
3. `report_type`이 `단일종목`이 아니면(`None` 포함) 422 `금융 정보 분석은 단일종목 보고서를 선택해 주세요.` DB 설정·키가 없어도 이 응답이 나와야 한다.
4. 현재 버전 결과에 `financial_details`가 있으면 그것을 돌려준다(`reused=True`). 키·PDF·AI를 건드리지 않는다. `financial_details`가 없는 옛 결과는 다시 분석한다.
5. `phase2_llm()`으로 키·codex CLI를 확인한다. 재사용·422·409가 키 없이 동작하도록 AI 호출 직전, PDF 확인보다 앞에 둔다. 키도 PDF도 없으면 404가 아니라 `NotReady("분석", …)`가 나와야 한다.
6. PDF는 `core.pdf.resolve_in_storage(storage_base_dir(), row['file_path'])`로 찾는다. 저장 폴더 밖이거나 `.pdf`가 아니면 404 `PDF를 찾을 수 없습니다.`, 파일이 없으면 404 `로컬 PDF 파일이 없습니다. 수집 상태를 확인해 주세요.`, 글자가 비면 422 `PDF에서 읽을 수 있는 텍스트가 없습니다.`
7. AI 호출 → 목표주가 방향 산수 → 숫자 근거 확인 → 저장 → `(payload, False)`.
- `analyze_report`에는 `reports.report_row`가 준 내부 행을 넘긴다. 공개 모양(`get_report`)에는 `file_path`가 없어 6번에서 `KeyError`가 난다.

**AI 동시 호출 2개.** `AI_CONCURRENCY = 2`는 웹 서버 전체에서 분석과 비교를 합친 수다. 설정으로 만들지 않는다. 분석과 비교의 AI 호출은 모두 `ai_slot()` 안에서 하고, 따로 세마포어를 만들지 않는다.

**쪽 표시 형식.** PDF 글자는 쪽마다 `--- Page N ---\n<글자>\n`이고, `N`은 PDF의 물리적 쪽 순서(1부터)다. 세 곳이 여기에 기댄다: `ground_metrics`가 `--- Page (\d+) ---`로 쪽을 나누고, 프롬프트가 `source_pages`·`evidence.page`를 이 표시에서 고르게 하고, 화면이 그 번호로 `pdf_url#page=N` 링크를 만든다. 형식도 번호 매기는 방식(인쇄된 쪽 번호, 0부터 세기)도 바꾸지 않는다.

**입력 한도.** 쪽 덩어리(표시 포함)마다 `max(1, len(덩어리) // 3)`을 토큰으로 어림해 더한다. 합이 `PHASE2_MAX_INPUT_TOKENS`를 넘게 만드는 첫 쪽에서 멈추고, 그 뒤 쪽은 작아도 넣지 않는다. 한도와 딱 같으면 잘린 것이 아니다. 잘렸으면 `input_truncated=True`만 남기고 글자 안에는 아무 표시도 넣지 않는다. 첫 쪽부터 한도를 넘으면 글자가 비어 422가 된다.
- 글자 층이 없는 쪽(스캔본)도 쪽 표시는 들어가므로 글자가 비지 않은 것으로 보고 AI를 부른다. 422는 파일을 못 열었거나, 쪽이 없거나, 첫 쪽이 한도를 넘을 때만 나온다. 옛 동작이므로 조용히 바꾸지 않는다.

**결과 버전.** 저장할 때 `summary_version`에 `PHASE2_SUMMARY_VERSION`(기본 `llm-summary@1.0`)을 넣고, `summaries_for`는 이 값과 같은 행만 돌려준다(100개씩 나눠 조회). 다른 버전 행은 "결과 없음"으로 보인다. 표의 기본 키가 `report_id`라 보고서당 한 행이므로 다음 분석이 옛 버전 행을 덮어쓴다. 그래서 버전 값을 바꾸면 기존 결과가 모두 화면에서 사라지고, 다시 분석해야 채워진다. 저장된 이름 `llm-summary@1.0`은 바꾸지 않는다.

**저장 값.**
- upsert payload = `ExtractionResult.model_dump()`(`financial_details`는 근거 확인을 거친 값 + `unsupported_numeric_values`) + `report_id`, `input_truncated`, `input_pages_used`, `input_total_pages`, `summary_version`, `llm_model`(설정한 이름 그대로, `codex:` 포함), `llm_tokens_input`, `llm_tokens_output`, 비교 칸 네 개(`prev_report_id`, `prev_match_type`, `diff_narrative`, `comparison_details`) = `None`. 키와 순서는 테스트가 고정한다.
- 다시 분석하면 그 보고서 행의 비교 칸이 비워진다(그 보고서를 뒷 문서로 쓴 해석문이 사라진다). `upsert_summary`는 앞의 세 비교 칸이 `None`이 아니면 `assert`로 거절한다. upsert로 비교 값을 채우지 않는다. 다른 보고서 행은 건드리지 않으므로, 앞 문서를 다시 분석해도 뒷 문서에 저장된 해석문은 남는다.
- `save_comparison(뒷 문서 id, 앞 문서 id, match_type, narrative, comparison_details)`는 `.update()` + `.eq('report_id', …)`로 비교 칸 네 개만 쓴다. upsert로 바꾸면 분석 결과가 날아간다. 요약 행이 없는 id면 아무것도 쓰지 않고 오류 없이 끝나므로, 부르는 쪽이 두 요약이 있는지 먼저 확인한다.
- payload 키는 `report_summaries`의 실제 열이어야 하고, `Literal` 값 집합은 DB CHECK 제약과 같다: `target_price_dir` {상향, 불변, 하향, 신규, N/A}, `recommendation` {매수, 중립, 매도, N/A}, `recommendation_dir` {유지, 상향, 하향, 신규, N/A}, `extraction_confidence` {high, medium, low}, `prev_match_type` {same_publisher, cross_publisher, none} 또는 NULL, `source_pages`는 모두 1 이상. 필드나 값을 더하면 `migrations/NNN_*.sql`도 함께 만든다.

**후처리.**
- 목표주가 방향: 새 값과 옛 값이 둘 다 있으면 산수로 `상향`/`하향`/`불변`을 덮어쓴다. 둘 다 없으면 `N/A`. 하나만 있으면 모델 판단(`신규` 등)을 그대로 둔다.
- 숫자 근거: 지표 값이 자기 인용문(`evidence.quote`)과 인용한 쪽의 글자 양쪽에 숫자로 있어야 지표가 남는다. 아니면 지표를 빼고 1을 센다. `previous_value`는 `previous_evidence`의 인용문과 그 쪽에 이전 값·현재 값이 모두 있어야 남고, 아니면 이전 값만 지우고(지표는 남김) 1을 센다. 인용문이 `12M Fwd`·`12개월 선행`·`NTM`인데 기간이 연도(`2026`, `2026E`)면 빼고 1을 센다. 센 수가 `financial_details.unsupported_numeric_values`이고, 화면에 "원문 근거와 일치하지 않은 수치 N"으로 나온다. `평가기간 변경` 이유는 옛·새 기준이 둘 다 있고 서로 다를 때만 남긴다(세지 않는다).
- 숫자는 쉼표를 지우고, 괄호는 음수로, 유니코드 마이너스는 `-`로 바꾼 뒤 float 값으로 맞춘다. 단위 환산은 하지 않는다(프롬프트도 인쇄된 숫자 그대로를 요구한다).

**AI 호출.**
- 재무 추출은 `client.parse(…, schema=ExtractionResult, constrained=False)`다. Claude가 이 스키마를 문법으로 컴파일하지 못해("compiled grammar is too large"), 스키마를 시스템 프롬프트 뒤에 붙이고 받은 JSON을 pydantic으로 검증하는 경로다. `True`로 바꾸지 않는다.
- 시도마다 `asyncio.wait_for(…, timeout_s)`(`PHASE2_PER_REPORT_TIMEOUT_S`)를 건다. 일시 오류(429·5xx·연결·시간 초과)는 `core.llm.call_with_retry`가 5초 뒤 한 번만 다시 하고, 또 실패하면 `TransientLLMError`다. 그 밖의 오류와 거부(`parsed is None` → `RuntimeError("LLM이 응답을 거부했습니다: …")`)는 바로 실패한다. 어떤 실패에서도 저장하지 않고 이전 결과를 그대로 둔다. 실패는 그 요청의 오류로 올라가 웹에서 일반 503 문구가 된다.
- 클라이언트는 SDK 기본 재시도(`max_retries=2`)를 쓰고 클라이언트 단위 시간 한도는 없다. `async with client:`로 쓰고 닫는다.
- 시스템 메시지는 모든 보고서에 같은 글이어야 한다(Anthropic 시스템 프롬프트 캐시). 보고서마다 다른 것(메타데이터 4개, 쪽 글자)은 user 메시지에만 넣는다.

**준비 실패 문구.** 화면에 그대로 나간다. `NotReady`의 기능 이름은 `분석`이다.
- 키 없음: `<변수>가 설정되지 않았습니다. .env에 추가 후 분석 다시 시도하세요.` 변수는 모델 이름이 정한다(`claude-*` → `ANTHROPIC_API_KEY`, `codex:`가 아닌 그 밖 → `OPENAI_API_KEY`).
- `codex:` 모델인데 CLI가 없음: ``codex CLI를 찾을 수 없습니다. 설치 후 `codex login`으로 로그인하세요.`` API 키가 있어도 CLI를 대신하지 않는다.
- DB 설정 없음(`summaries_for`, `save_comparison`, 재사용 확인): `DB 접속 설정(SUPABASE_URL, SUPABASE_SERVICE_KEY)이 없습니다`.
- 이유 문구에 경로·키 값·오류 추적을 넣지 않는다.

**스키마와 프롬프트는 한 쌍이다.** 프롬프트 글은 운영 중인 계약이라, 고치면 모델 동작 변경으로 다룬다.
- 프롬프트가 직접 적은 개수·길이(지표 48개, 긍정·위험 포인트 0~5개, 한 줄 요약 90자, `source_pages` 10개, 투자 논리 5개)는 스키마 제한과 함께 고친다. 한쪽만 바꾸면 모델이 한도를 넘겨 검증 실패, 곧 분석 실패가 난다.
- 모델 클래스에 docstring이나 `Field(description=…)`을 더하면 모델이 받는 JSON 스키마가 바뀐다(Claude 경로는 스키마를 프롬프트에 싣고, codex·OpenAI 경로는 엄격 스키마로 보낸다).
- 정규화 규칙 3은 `target_price_dir` 값으로 `유지`를 적고 있지만 허용 값은 위의 다섯 개다. 옛 문구를 그대로 옮긴 것이다. 스키마에 `유지`를 더하지 않는다.
- 정식 지표 이름(`지배주주순이익` 등)과 "7개 기준이 모두 같아야 짝"이라는 설명은 compare가 두 요약의 지표를 문자열 그대로 짝지을 때의 기준이다. 이름을 바꾸면 이전 요약과의 짝이 끊긴다.

**설정 읽기.** 부를 때 읽고 import 때 읽지 않는다. `phase2_llm()`은 매번 `load_env()`부터 하므로 `.env`에 *추가한* 키는 다음 시도에 반영되고, *바꾼* 값은 웹앱을 다시 켜야 반영된다. 모델 이름이 빈 값이면 없는 것으로 보고 옛 이름, 그다음 기본값을 쓴다. 숫자 설정이 숫자가 아니면 `ValueError`(웹 일반 503)이고 `NotReady`가 아니다.

## 이 칸의 방식

- **준비 두 가지.**
  - AI: `phase2_llm()` = `load_env()` → `load_settings()` → `LLMClient(…)` → `require_key(model)`. 실패는 `NotReady("분석", …)`로 바꾼다. 결과를 들고 있지 않고 부를 때마다 새 클라이언트를 만든다.
  - DB: `_supabase()`가 처음 쓸 때 `load_env()` 후 `core.db.supabase_client(url, key)`로 만들고 프로세스 동안 들고 있다(`threading.Lock`). 실패하면 다음 호출에서 다시 만든다. reports의 클라이언트를 빌려 쓰지 않는다(import하면 순환). `summaries_for([])`는 DB 없이 `{}`다.
- **슬롯 구현.** 실행 중인 이벤트 루프마다 `asyncio.Semaphore(2)`를 처음 쓸 때 하나씩 만든다(`WeakKeyDictionary`). 세마포어는 다른 루프에서 쓸 수 없으므로 import 때 만드는 전역 세마포어로 바꾸지 않는다(테스트는 테스트마다 새 루프를 쓴다).
- **막히는 일은 작업 스레드로.** DB 조회·저장과 PDF 읽기는 `asyncio.to_thread`로 돌리고, AI 호출만 이벤트 루프에서 기다린다.
- **자리.** `.table('report_summaries')`는 `store.py`에만 둔다(첫 인자로 supabase-py 클라이언트를 받는다). 목표주가 방향과 숫자 근거 확인처럼 DB·네트워크 없이 시험할 계산은 `logic.py`·`financials.py`, 쪽 표시 글자는 `pdf_text.py`(`core.pdf.page_texts` 위)에 둔다.
- **더할 때.**
  - 새 공개 이름은 `__init__.py` 최상위에 `from .service import 이름`으로 직접 묶는다. `__all__`에만 적으면 구조 검사가 공개 이름으로 치지 않는다.
  - 새 AI 호출은 `phase2_llm()`이 준 클라이언트·모델·시간 한도로, `ai_slot()` 안에서, `call_with_retry` + `wait_for` 꼴로 만든다.
  - 새 설정은 `settings.py`에서 `core.settings` 도우미(`get_int`, `optional`, `model_name`)로 읽는다.

## 테스트

- **꼭 덮을 것.** 확인 순서(DB·키 없이 422, 키 없이 재사용, 키·PDF가 둘 다 없으면 `NotReady`), 같은 보고서 409가 슬롯을 기다리지 않음, 2개 한도와 compare와의 공유, 저장 payload의 키·순서, 쪽 표시 글자의 정확한 일치, 한도 경계(딱 같음, 첫 쪽 초과, 뒤의 작은 쪽 제외), 숫자 근거 규칙, 재시도·시간 초과·거부, `.env` 다시 읽기.
- **밖에 기대지 않게.**
  - 모듈마다 읽힐 수 있는 환경 변수를 전부 지운다(`ENV` 목록). 새 설정을 더하면 목록에도 더한다. 빠지면 개발자 셸에 있던 값이 결과를 바꾼다.
  - `core.llm._codex_bin`을 `None`을 돌려주게 바꾼다. codex CLI가 깔린 PC(분석 모델이 `codex:`인 운영 PC)에서는 안 바꾸면 codex 모델 결과가 달라진다.
  - `service._supabase_client`와 `service._analyzing`을 `monkeypatch.setattr`로 비운다. 모듈 전역이라 안 비우면 앞 테스트의 가짜 DB가 다음 테스트로 샌다.
  - DB는 `core.db.supabase_client`만, AI는 `LLMClient.parse`만 가짜로 바꾼다. 그래야 `phase2_llm`·`extract_one`·`call_with_retry`·후처리가 진짜로 돈다.
  - 실제 `.env` 읽기는 `research_desk/conftest.py`가 모든 테스트에서 끈다. 다시 읽기는 `env_file` 고정물(임시 `.env`)로만 시험한다.
  - PDF는 `tmp_path`의 저장 폴더에 `pymupdf`로 만든다(테스트는 외부 도구 규칙에서 빠진다). 기본 글꼴에 한글 글리프가 없으니 본문은 ASCII로 쓴다.
- **함정.**
  - PDF 읽기와 DB가 작업 스레드에서 돌아 루프 몇 바퀴로는 판정할 수 없다. 시계 기준으로 기다리고(`until`), 멈출 수 있는 await에는 `LIMIT_S` 한도를 건다. 슬롯이 새면 멈추는 대신 실패하게 하려는 것이다.
  - 슬롯을 시험할 때 `ai_slot`을 바꿔치지 않는다. 같은 루프에서 두 슬롯을 직접 잡아(`async with ai_slot(), ai_slot():`) 상황을 만든다.
  - 가짜 DB는 없는 열이나 제약 밖 값도 받아 준다. 스키마·payload를 바꿨을 때 마이그레이션이 필요한지는 테스트가 알려 주지 않는다.
