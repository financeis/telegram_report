# research_desk/domain/ — 공용 기준: 리포트 분류 체계, 분석 대상 규칙, 종목표와 그 버전

## 맡는 일

- `vocabulary.yaml`: 리포트 값 집합의 원본. 리포트 종류, 분석 대상 외(OOS) 사유, 발행처 종류, 분류 상태, 신뢰도. 끝의 `precedence_rules`는 코드가 읽지 않는 참고 메모다(분기 규칙의 실제 구현은 분류기 노드에 있다).
- `reports.py`: 위 값 집합(튜플), "분석 대상" 규칙(`IN_SCOPE_STATUSES`, `is_in_scope`), "분석 대상 외 행 모양"(`OOS_CLEARED_COLUMNS`, `OOS_KEPT_COLUMNS`, `oos_row_shape`). 이 규칙들의 정의는 저장소에 여기 한 곳뿐이다.
- `stocks.py`: 종목표(KRX CSV)를 읽는 단 하나의 코드. 분류기의 종목 매핑, 웹의 기업 목록·리포트 화면·커버리지 이름이 모두 이것을 쓴다. 조회·검색·목록과 버전 정보 파일(내용 지문, `verify`, `write_version`)도 여기 있다.
- 이 칸이 따로 있는 이유: 분류기와 웹 기능이 같은 뜻으로 써야 하는 약속인데, 분류기는 웹 기능을 import할 수 없고 core는 업무 개념을 모르게 두기 때문이다.

## 맡지 않는 일

- DB. 연결하지 않고 표를 다루지 않는다. `supabase`·`asyncpg`를 import하지 않고, `.table('reports')`나 `FROM`/`UPDATE`/`INTO`/`JOIN <표>` 문자열도 두지 않는다(domain은 어느 표의 주인도 아니어서 `R11 표 주인`으로 잡힌다). 호출자가 행(dict)을 넘기고, 돌려받은 값을 자기 SQL·REST로 쓴다. 분석 대상 조건을 DB 질의로 옮기는 일은 `features/reports/store.py`가 한다.
- 웹. 화면 주소, FastAPI, HTTP 오류를 만들지 않는다.
- 설정. `.env`도 환경 변수도 읽지 않고 경로는 인자로 받는다. `KRX_CSV_PATH`를 읽는 것은 호출자(`core.settings.krx_csv_path()`)이고, `stocks set-version` 명령 연결은 `research_desk/cli.py`에 있다.
- 다른 칸 import. `research_desk.core`·`research_desk.domain`만 import한다(어기면 `R2 domain`). `fitz`·`pymupdf` 같은 외부 도구도 쓰지 않는다.
- 버전이 안 맞을 때의 대응. 분류 명령은 4로 멈추고 웹 기능은 경고 로그만 남기는데, 이 판단은 호출자 몫이다. `verify()`는 결과만 돌려준다.
- 웹 입력 코드를 6자리로 채우는 일. 웹 기능이 종목표 조회 직전에 한다(DB 조회에는 받은 그대로 쓰고, 분류기는 채우지 않는다). 조회 함수 안에서 채우지 않는다.
- 발행처 사전(`tagger/vocabulary/publishers.yaml`), LLM 응답 스키마와 프롬프트. 분류기 것이다.

## 늘 지켜야 할 것

값 집합.
- `REPORT_TYPES`(`단일종목`, `산업`, `섹터`, `IR자료`, `전략·시황`, `기타`), `OOS_REASONS`(`foreign`, `fund`, `digital`, `private`, `ir_self`), `PUBLISHER_TYPES`(`broker`, `data_provider`, `ir_agency`, `other`), `TAGGING_STATUSES`(`pending`, `processing`, `auto`, `review_needed`, `verified`), `TAGGING_CONFIDENCES`(`high`, `medium`, `low`). 값과 순서를 테스트가 고정한다.
- 값을 바꾸면 함께 바뀌어야 하는 곳이 있다. yaml만 고치고 끝내지 않는다.
  - DB의 CHECK 제약: 새 `migrations/NNN_*.sql`.
  - 분류기 LLM 스키마의 `Literal`: 같아야 한다는 테스트가 있다.
  - 분류기 시스템 프롬프트: 리포트 종류를 이 순서대로 박아 넣으므로 순서만 바꿔도 LLM 요청이 바뀐다.
  - 화면의 OOS 사유 목록 사본(`frontend/src/ReviewQueue.jsx`): 손으로 맞춘다.
  - 분류 상태 값: 분류기 SQL(`tagger/sql.py`)과 검토 처리(`features/review/`)에도 문자열로 박혀 있다.
- `vocabulary.yaml`에 버전 필드를 두지 않는다(테스트). `reports.taxonomy_version`은 이 파일이 아니라 종목표의 버전이다. 분류 체계를 바꾸면 어차피 마이그레이션이 같이 가야 해서 따로 버전을 둘 이유가 없다.
- 값 집합은 import할 때 yaml에서 한 번 읽는다. yaml 문법이 깨지면 이 모듈을 쓰는 곳(분류기, 검토, 리포트 기능)이 모두 import 단계에서 실패한다.

분석 대상.
- `is_in_scope(row)`는 `tagging_status`가 정확히 `auto`·`verified` 중 하나(대소문자 구분)이고 `out_of_scope_reason`이 None일 때만 참이다. 사유 키가 없으면 None으로 본다(SQL의 `IS NULL`과 같게). 상태 키가 없으면 거짓.
- DB 질의로 같은 조건을 만들 때도 `IN_SCOPE_STATUSES`를 가져다 쓴다. `('auto', 'verified')`를 손으로 다시 적거나 규칙을 다른 칸에 새로 정의하지 않는다.

분석 대상 외 행 모양(`oos_row_shape(row, reason)`). 분류기의 쓰기와 검토의 "제외" 처리가 같은 함수를 쓰므로, 여기를 바꾸면 두 쓰기가 함께 바뀐다.
- 돌려주는 키는 정확히 `out_of_scope_reason`, 비우는 열(`published_at` → None, `stock_codes`·`company_names`·`sectors_major`·`sectors_minor`·`products` → `[]`), 남기는 열(`report_type`·`publisher`·`publisher_type`·`analysts`·`title`·`stock_codes_raw`·`company_names_raw`, 행의 값 그대로)이다.
- `tagging_status`는 넣지 않는다. 분류기는 자기 판정을, 검토는 `verified`를 각자 넣는다. 신뢰도·메모·버전·id·파일 열도 넣지 않는다.
- `report_type`을 `기타`로 덮어쓰지 않는다. 분류 결과를 그대로 남기는 것이 지금 규칙이다.
- 배열은 새 리스트로 복사하고 None 배열은 `[]`로 만든다. 넘겨받은 행은 바꾸지 않는다. `row`가 None이면 남기는 열은 None이나 `[]`다.
- `reason`이 `OOS_REASONS`에 정확히 들어 있지 않으면(대문자, None, 빈 값 포함) `ValueError`.
- `row`는 DB 열 이름으로 넘긴다. 분류기는 LLM의 `publisher_canon`을 `publisher`로 바꿔서 넘긴다.

종목표.
- 머리줄은 칸 안의 줄바꿈과 앞뒤 공백을 지운 뒤 정확히 `종목코드, 종목명, 시장, 산업명(대), 산업명(중), 주요제품`(순서까지, 6칸)이어야 한다. 실제 파일의 첫 칸은 따옴표 안에 줄바꿈이 든 `"종목\n코드"`다. 다르면 `StockListError`("머리줄이 다릅니다").
- UTF-8로만 읽는다(맨 앞 BOM 허용). 엑셀이 cp949로 다시 저장한 파일은 "UTF-8로 읽을 수 없습니다"로 거절한다. 파일이 없으면 "파일이 없습니다", 폴더이거나 열 수 없으면 "파일을 열 수 없습니다". 6칸보다 짧은 행은 건너뛰고, 칸은 앞뒤 공백을 지우고, 7번째 칸부터는 버린다.
- 조회는 정확히 일치해야 한다. `lookup`은 0을 채우지 않는다(`5930`으로는 `005930`을 못 찾는다). `validate_code`는 `^[0-9A-Z]{6}$`(대문자·숫자, `0008Z0` 같은 스팩 코드 허용)이면서 목록에 있어야 참이다. `lookup_by_name`은 공백과 대소문자를 무시한다.
- 같은 코드가 여러 행이면 마지막 행이, 같은 이름이 여러 행이면 첫 행이 이긴다. 예전 색인과 같게 둔 것이니 바꾸지 않는다.
- `catalog()`는 파일 순서대로 `{code, name, sector_major, sector_minor}`를 주고, 빈 값은 `""`이며, 부를 때마다 새 dict다. 웹의 `/api/workspace` 응답 모양이 이것이다. `search`는 코드 앞부분 일치나 이름 포함(대소문자 무시)으로 고르고, 빈 질의면 전부를 준다. 결과는 늘 파일 순서다.
- `StockListError`(`ValueError`의 하위)의 문구는 한국어이고 파일 경로가 들어 있다. 명령 출력에는 그대로 써도 되지만 웹 응답에는 넣지 않는다. 웹 기능은 고정 문장의 `NotReady`로 바꾸고 원인은 로컬 로그에만 남긴다.

종목표 버전 정보 파일.
- 자리: CSV와 같은 폴더의 `<CSV 파일 이름에서 확장자를 뺀 것>.version.json`(`KRX_stocks_data.csv` → `KRX_stocks_data.version.json`, `b.c.csv` → `b.c.version.json`).
- 내용: `{"version": "KRX@YYYY-MM-DD", "content_hash": "<소문자 16진수 64자리>"}`. 날짜는 종목표의 **자료 기준일**이다. 명령을 돌린 날도, 파일 수정 시각도 아니다(수정 시각은 더는 쓰지 않는다). 분류된 행의 `taxonomy_version`에는 이 `version` 값이 그대로 저장된다.
- 내용 지문(`content_hash`)은 머리줄까지 넣어 이렇게 계산한다.
  1. 바이트를 UTF-8로 읽고 맨 앞 BOM을 뺀다.
  2. CSV로 해석한다(따옴표 안 줄바꿈은 칸 안에 남는다).
  3. 머리줄 칸은 안의 `\r`·`\n`을 모두 지우고, 나머지 칸은 안의 `\r\n`·`\r`을 `\n`으로 바꾸고, 모든 칸의 앞뒤 공백을 지운다.
  4. 한 행의 칸은 `\x1f`로, 행은 `\n`으로 잇는다.
  5. 그 문자열의 UTF-8 바이트로 SHA-256을 구해 소문자 16진수로 쓴다.
- 그래서 줄바꿈(LF·CRLF·CR), BOM 유무, 따옴표 표기, 칸 앞뒤 공백, 머리줄 칸 안의 줄바꿈 방식, 파일 끝 줄바꿈 유무가 달라도 지문은 같다. 칸 값 하나나 행 순서가 바뀌면 달라진다. 지문은 읽어 들인 종목 목록이 아니라 CSV로 해석한 모든 행과 칸을 덮는다. 빈 줄 하나, 6칸 뒤에 붙은 칸, 건너뛰는 짧은 행도 지문을 바꾼다. 종목 목록이 그대로여도 이런 차이가 생기면 `set-version`을 다시 해야 한다.
- 줄바꿈을 무시하는 이유: 저장소가 `core.autocrlf=true`라서 CSV는 git 안에는 LF로 들어 있지만 Windows 작업 폴더에는 CRLF로 풀린다. 파일 바이트로 지문을 만들면 작업 폴더마다 값이 달라져 같은 내용이 "다르다"가 된다. 계산 방식을 바꾸면 이미 기록된 지문이 모두 무효가 되므로 바꾸지 않는다.
- `verify()`는 부를 때마다 버전 정보 파일을 새로 읽는다(캐시 없음). `VersionCheck.reason`은 셋 중 하나다.
  - `missing`: 파일 없음, 또는 메모리에서 만든 목록.
  - `invalid`: 못 읽음, UTF-8·JSON·객체가 아님, `version`이 `KRX@`와 실제 날짜가 아님, `content_hash`가 소문자 64자리가 아님.
  - `mismatch`: 지문이 다름. 이때도 `version`은 채워 준다.
  - BOM이 붙은 버전 정보 파일(메모장으로 저장한 것)은 받아들인다. 비교하는 지문은 `StockList.load`가 읽은 시점의 CSV 내용이다.
- `write_version(csv, as_of)`는 날짜 확인 → 머리줄 확인(읽기) → 지문 → 같은 폴더의 임시 파일(`.<이름>.…tmp`)에 LF로 쓰고 fsync한 뒤 `os.replace`로 교체, 이 순서다.
  - 어느 단계에서 실패해도 `StockListError`이고, 버전 정보 파일은 그대로이며, 임시 파일은 지운다(폴더에는 CSV와 버전 정보 파일만 남는다).
  - 날짜를 CSV보다 먼저 보므로 날짜가 틀리면 CSV가 없어도 날짜 오류다.
  - 같은 날짜로 다시 쓰는 것은 허용한다. 버전 이름이 "자료 기준일"이라는 뜻이기 때문이다.
  - JSON은 `ensure_ascii=False`, 들여쓰기 2, 키 순서 `version` → `content_hash`, 끝에 줄바꿈 하나다.
- 날짜는 ASCII 숫자로 된 정확한 `YYYY-MM-DD`이면서 실제 있는 날이어야 한다. 정규식과 `date.fromisoformat`을 둘 다 쓰는 이유: Python 3.11의 `fromisoformat`은 `20260508` 같은 다른 ISO 형식도 받아 주고, 정규식만으로는 `2026-02-30`을 못 거른다. 하나로 줄이지 않는다.
- 저장소에 함께 들어 있는 종목표(`KRX_CSV_PATH`의 기본값 `core.settings.DEFAULT_KRX_CSV_PATH`)와 그 버전 정보 파일은 늘 맞아야 한다. `test_bundled_csv_matches_its_version_file`이 버전 정보 파일의 모양(`version`·`content_hash` 두 키, `KRX@<실제 날짜>`, 64자리 지문)과 지문이 CSV 내용과 같은지를 확인한다. 버전 이름은 테스트에 박지 않고 버전 정보 파일에서 읽는다. 그래서 CSV만 바꾼 커밋은 커밋 검사에서 막히고, `set-version`까지 한 커밋은 이 테스트를 고치지 않아도 통과한다.
  - 코드 작업 중에 이 CSV를 고치거나 다른 형식으로 다시 저장하지 않는다. 버전 정보 파일의 `content_hash`를 손으로 고치지 않는다. 테스트에 버전 이름(`KRX@2026-05-08` 같은)을 다시 박지 않는다 — 종목표를 바꿀 때마다 커밋이 막힌다.
  - 사용자가 종목표를 실제로 바꿀 때는 저장소 루트에서 `python -m research_desk stocks set-version --as-of <자료 기준일>`을 실행하고, CSV와 버전 정보 파일을 한 커밋에 넣는다.
- 코드가 CSV를 새로 쓰게 되면(종목표 자동 갱신 같은 기능) 같은 자리에서 버전 정보 파일도 함께 쓴다. CSV만 바뀌면 다음 `tag run`·`tag escalate`가 4로 멈춘다.

## 이 칸의 방식

- 규칙과 기준 데이터만 둔다. 행과 경로를 인자로 받아 값을 돌려주고, 실패는 예외(`StockListError`, `ValueError`)나 결과 값(`VersionCheck`)으로 알린다. `NotReady`는 던지지 않는다. 받는 쪽이 이렇게 바꾼다.
  - 분류 명령(`tag run`·`tag escalate`): 못 읽으면 `종목표 파일을 읽을 수 없습니다: <이유>`, `verify()`가 ok가 아니면 이유와 상관없이 `stocks set-version` 안내. 둘 다 행을 가져가기 전에 종료 코드 4.
  - `stocks set-version`: 이유를 붙여 4.
  - 웹 기능(`companies`·`reports`·`coverage`): 못 읽으면 `NotReady(<기능 이름>, "종목표 파일을 읽을 수 없습니다")`, 버전 문제는 경고 로그만.
- 쓰는 곳마다 `StockList.load`를 직접 부른다. 분류 명령은 시작할 때 한 번, 웹 기능은 처음 쓸 때 한 번 읽어 프로세스가 끝날 때까지 들고 있고, 실패했으면 다음 요청에 다시 읽는다. `load`는 파일을 한 번만 읽어 머리줄 확인·항목·지문을 한꺼번에 얻는다.
- 새 공용 규칙(분류기와 웹 기능이 같은 뜻으로 써야 하는 값이나 판정)은 여기에 둔다. 기능 폴더에 두면 분류기가 쓸 수 없다. 새 값 집합은 `vocabulary.yaml`에 넣고 `reports.py`에서 튜플로 내보낸다.

## 테스트

- 저장소의 실제 종목표는 읽기만 한다(`test_stocks.py`의 모듈 단위 `krx` fixture). 나머지는 모두 `tmp_path`에 바이트로 쓴 작은 CSV를 쓴다(`write_csv`로 줄바꿈과 BOM을 골라서). 실제 종목표로 `write_version`을 부르지 않는다. 저장소의 버전 정보 파일을 다시 쓰게 된다.
- 시험용 CSV의 머리줄에는 실제 파일처럼 따옴표 안 줄바꿈을 넣는다(`HEADER_LINE`).
- 반드시 있어야 하는 경우:
  - 지문이 같은 경우(LF·CRLF·CR·BOM·따옴표·공백·머리줄 줄바꿈)와 다른 경우(칸 하나, 행 순서), 계산 방식 그대로 만든 기대값.
  - 함께 들어 있는 종목표와 버전 정보의 일치(LF·CRLF 사본 모두).
  - `verify`의 세 이유, BOM 허용, 매번 다시 읽기, 수정 시각 무시.
  - `write_version`의 성공·재기록·나쁜 날짜 전부·머리줄 오류·CSV 없음·교체 실패.
- 실패 테스트는 실패 전후의 버전 정보 파일을 바이트로 비교하고, 폴더의 파일 이름 목록으로 임시 파일이 남지 않았는지 본다. 교체 실패는 `stocks.os.replace`를 예외를 던지는 함수로 바꿔 만든다.
- `reports` 쪽: 값 집합과 yaml의 일치, 버전 필드 없음, 검토 행과 LLM 필드 각각의 정확한 OOS 모양, 분류 없음(None·`{}`), None 배열, 복사(원본 불변), 틀린 사유, 분석 대상의 모든 상태·사유 조합.
- 다른 칸의 테스트에서 종목표가 필요하면 임시 CSV에 `write_version`을 불러 버전 확인을 통과시킨다. 메모리에서 만든 `StockList(entries)`는 경로와 지문이 없어 `verify()`가 늘 `missing`이다.
