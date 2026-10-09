# LLM 모델 구성과 선택 근거

## 구성

| 용도 | 설정 | 기본값 | 실행 경로 |
|---|---|---|---|
| 태깅 | `LLM_MODEL_DEFAULT` | `claude-haiku-5-5` | Anthropic API (`ANTHROPIC_API_KEY`) |
| 태깅 재처리 (escalate) | `LLM_MODEL_ESCALATION` | `gpt-5.4` | OpenAI API (`OPENAI_API_KEY`) |
| 재무 분석·리포트 비교 | `LLM_MODEL_PHASE2` | `gpt-6-luna` (운영 `.env`는 `codex:gpt-6-luna`) | `codex:` 접두사 → 로컬 `codex exec` |
| 유사 기업 프로필(사업 요약 카드) | `LLM_MODEL_PEERS` | `claude-sonnet-5-5` | Anthropic API (`ANTHROPIC_API_KEY`) |
| 프로필 재추출(근거율 0.8 미만) | `LLM_MODEL_PEERS_ESCALATION` | `gpt-5.4` | OpenAI API (`OPENAI_API_KEY`) |
| 프로필·테마 검색 임베딩 | `PEERS_EMBED_MODEL` | `text-embedding-3-large` (1536차원) | OpenAI API (`OPENAI_API_KEY`) |

모델 이름이 실행 경로를 정한다 ([core/llm.py](../research_desk/core/llm.py)):
`claude-*` → Anthropic API, `codex:<model>` → Codex CLI, 그 외 → OpenAI API.
바꾸거나 되돌릴 때는 `.env`의 모델명만 고치고 워커·Research Desk를 재시작한다.
구 이름 `OPENAI_MODEL_*`도 `LLM_MODEL_*`이 없을 때 읽는다.

## 구조화 출력 방식

| 경로 | 방식 |
|---|---|
| OpenAI API | `chat.completions.parse` (strict JSON schema) |
| Anthropic, 태깅·비교 | `output_config.format` (스키마를 디코딩 grammar로 강제) |
| Anthropic, 재무 추출 (`constrained=False`) | 스키마를 프롬프트에 넣고 JSON 텍스트 → pydantic 검증 |
| Codex CLI | `codex exec --output-schema` (OpenAI와 같은 strict schema) |

`ExtractionResult`는 Anthropic grammar 컴파일러 한도를 넘는다 ("compiled grammar is
too large"). 필드를 전부 required로 바꾸거나 nullable을 줄여도 넘어서 텍스트 경로를 쓴다.
비엄격 tool 입력도 시험했으나 중첩 객체를 JSON 문자열로 돌려주는 경우가 있어 버렸다.

모든 경로의 재무 수치는 저장 전에 `ground_metrics`가 원문 인용·해당 페이지와 대조한다.

## 선택 근거 (2026-10-08 측정, DB 미기록 dry-run)

### 태깅: Haiku 5.5
기존 태그가 있는 35건(유형별 층화 표본)을 재태깅.
- 오류 0, 행당 2~11초, 행당 약 6K 입력 / 0.4K 출력 토큰.
- report_type 일치 27/35. 차이는 주로 산업↔섹터 경계. 기존 review_needed 3건이 auto로 해소.

### 유사 기업 프로필: Sonnet 5.5 (2026-10-10, 사용자 결정)
FY2024 사업보고서(로컬 원본 940곳)로 34곳 시험 실행(`peers build --pilot`), Haiku 5.5.
- 34/34 성공, 정보 부족 3곳, 근거율 0.8 미만으로 `gpt-5.4` 재추출 6곳(18%).
- 회사당 입력 약 9.6K / 출력 약 1.7K 토큰, 회사당 약 5초(동시 2).
- 한 줄 요약과 이웃은 그럴듯했다(피델릭스 "레거시 DRAM·NOR Flash·MCP 메모리 팹리스" → DB하이텍·파두·삼성전자).

카드 품질이 유사도 전체를 정하므로 사용자가 비교 실행 없이 Sonnet 5.5로 정했다. 930곳 전체 비용 어림은
약 35~60달러(Haiku 약 5~11달러), 가격은 외부 가격 사이트 기준이다.

### 재무 분석: Haiku 5.5 대신 luna
단일종목 12건, 같은 입력·같은 파이프라인.

| | gpt-5.6-luna | Haiku 5.5 |
|---|---|---|
| 목표주가·투자의견·밸류에이션 방식 일치 | 기준 | 12/12 |
| 둘 다 뽑은 지표의 값 일치 | 기준 | 94/94 |
| 재무 지표 수 (평균) | 18.1 | 9.2 |
| 형식 실패 | 0/12 | 3/24 |

Haiku는 정확하지만 과거 실적·업종 KPI·이전 추정치를 절반 정도만 뽑고, grammar 강제를
못 써 8회 중 1회꼴로 형식이 깨졌다.

### 추출 프롬프트 개편
[prompts.py](../research_desk/features/analysis/prompts.py)의 `_EXTRACTION_SYSTEM`.
- 결과 용도(카드, 리포트 비교의 정확 일치 매칭) 설명. "간결하게"와 "최대 48개"가 충돌하던 지시 제거.
- 증권사 리포트 구조(표지, 투자지표, 실적 추정 변경, 분기 실적, 목표주가 산정, 변동 추이,
  컴플라이언스)와 필수 범위(연간 표 전 연도, 수정 전·후 전 행, 최근 분기, 업종 KPI) 지정.
- 표준 지표명(`지배주주순이익`, `BPS` 등) 지정 — `compare_financials`가 이름 완전 일치로 짝짓는다.
- 주가 연동 배수(PER·PBR 등), 증감률, 컨센서스는 metrics에서 제외.

같은 12건, 예전 → 새 프롬프트:

| | 5.6 예전 | 5.6 새 | 6 예전 | 6 새 |
|---|---|---|---|---|
| 재무 지표 수 (평균) | 18 | 45 | 21 | 46 |
| 표준 지표명 비율 | 85% | 95% | 87% | 97% |
| 회계기준 미기재 | 15% | 7% | 20% | 7% |
| 근거 대조로 제외된 수치 | 8 | 5 | 11 | 2 |
| 평균 / 최대 시간 (API) | 28 / 39초 | 47 / 57초 | 43 / 60초 | 83 / 105초 |

같은 기업 리포트 쌍에서 비교 가능한 추정치: 삼성증권(NH→KB) 4·9 → 10·18,
대한전선(NH→유안타) 12 → 24. 목표주가·투자의견은 모든 조합에서 동일.
출력이 늘어 `PHASE2_PER_REPORT_TIMEOUT_S` 기본값을 90 → 180초로 올렸다.

### Codex CLI 경로
재무 분석은 사용자가 Research Desk에서 고른 리포트만 한 건씩 분석하고 결과를 재사용한다
(자동 배치 없음). 호출량이 적어 API 대신 ChatGPT 로그인 한도로 돌린다.
- `--ignore-user-config`로 개인 codex 설정(MCP 서버·훅·알림)을 건너뛰고, 빈 임시
  폴더에서 읽기 전용으로 실행한다.
- 사고 깊이 `CODEX_REASONING_EFFORT`: `high`(기본)가 API 결과와 같은 수준(현대그린푸드
  지표 44개, 73초). `medium`은 30초지만 28개.
- 요청마다 Codex 자체 지시문이 붙어 입력 토큰이 API보다 약 1.5만 많다.
- 시간 초과 시 프로세스 트리를 종료한다 (Windows는 npm shim → node → codex.exe).
- 한도 초과나 로그인 만료는 분석 카드 오류로 나타난다 → `codex login`.

태깅은 수천 건 배치라 Codex/`claude -p` 경로가 맞지 않는다: 요청당 토큰이 3~7배이고,
같은 구독 한도를 코딩 작업과 나눠 쓰며, 한도에 걸리면 배치가 멈춘다.
