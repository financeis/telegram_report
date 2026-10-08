# Research Desk

한국 증권 리서치 PDF를 텔레그램 채널 하나에서 모으고(`collect`), LLM으로 리포트 종류·발행처·종목·분석 대상 여부를 붙이고(`tag`), 로컬 웹앱에서 기업별로 읽고·재무 분석하고·두 보고서를 비교하고·애매한 분류를 수동 검토하는(`web`) 개인 리서치 도구다. 윈도우 PC 한 대에서 한 사람이 쓰고, 웹앱은 `127.0.0.1:8520`에서만 열리며 로그인이 없다. 리포트는 로컬 PDF 폴더(수십 GB)와 Supabase(클라우드 Postgres)의 `reports` 표에 수만 건 규모로 계속 쌓이고, 수집 → 분류 → 열람이 실제로 운영 중이다 — 운영 데이터를 깨지 않는 것이 첫째다. 파이썬은 `research_desk` 패키지 하나, 화면은 `frontend/`(React)다. 기능은 계속 늘어나며, 원칙은 "새 기능 = 새 폴더 하나 + 등록 한 줄"이다.

## 운영자

금융 현직자이고 개발자가 아니다. 보고와 질문은 쉬운 한국어로 하고, 전문 용어는 비유로 풀어 쓴다(예: 경쟁 조건 → "두 사람이 같은 줄을 동시에 잡으려다 꼬임"). 코드 세부보다 무엇이·왜·어떻게 돌아가는지를 먼저 말한다. 주된 관심은 "잘 돌고 있나 / 뭐가 문제인가 / 다음에 뭘 하면 되나"다. 커밋은 고친 것 하나에 하나씩 한다.

## 프로젝트 구조

```
telegram_report/
├── CLAUDE.md                        ← Claude Code가 읽는 이 안내 (AGENTS.md와 같은 내용)
├── AGENTS.md                        ← Codex가 읽는 이 안내 (CLAUDE.md와 같은 내용)
├── README.md                        ← 사람용 설치·명령 안내
├── docs/
│   ├── architecture.md              ← 구성 요소와 데이터 흐름, 코드 칸 지도, 표 주인, 외부 의존
│   ├── business-rules.md            ← 분류 상태 전이, 분류·분석·비교·커버리지·검토 규칙, 종목표 버전, 값 집합
│   ├── security.md                  ← 로컬 전용 접근, 호스트·출처 제한, 비밀 값, PDF 경로 제한, 외부로 나가는 데이터
│   ├── standards.md                 ← 위반하면 실패하는 규칙: 커밋 검사, 칸 경계, 준비 실패, 종료 코드, 설정, 운영 값
│   ├── engineering-notes.md         ← 함정, 디버깅하지 말 무해한 경고, 반복 작업 점검표
│   ├── operations.md                ← 설치, 명령, 웹앱, 운영 원칙(동시 처리 2·배치 10·백필·자동 처리 사건), 모니터링, 모델, 설정 목록
│   ├── contracts.md                 ← 화면이 쓰는 웹 API, 명령·스크립트의 인자·출력·종료 코드
│   ├── tracking/
│   │   ├── status.md                ← 끝난 것 / 남은 것
│   │   ├── decisions/               ← 되돌리기 어려운 선택과 그 이유 (index.md + 번호별 기록)
│   │   └── findings.md              ← 아직 못 푼 문제
│   ├── llm-models.md                ← 모델 비교 측정 기록
│   └── stock_data/                  ← 종목표 CSV + 버전 정보 파일 (KRX_stocks_data.version.json)
├── research_desk/
│   ├── AGENTS.md                    ← 명령 입구(cli.py·__main__.py), 테스트 공통 준비, 구조 검사
│   ├── core/AGENTS.md               ← 설정·DB 연결·AI 호출·PDF (업무 개념을 모르는 공용 설비)
│   ├── domain/AGENTS.md             ← 분류 체계 값, 분석 대상 규칙, 종목표와 버전
│   ├── collector/AGENTS.md          ← 텔레그램 → PDF + pending 행
│   ├── tagger/AGENTS.md             ← pending → auto / review_needed (LangGraph 행 그래프)
│   ├── features/
│   │   ├── AGENTS.md                ← 기능 폴더 모양, 공개 창구, 기능 사이 의존, 새 기능 붙이는 법
│   │   ├── companies/AGENTS.md      ← 기업 목록·관심 기업
│   │   ├── reports/AGENTS.md        ← 분류 끝난 리포트 조회, 목록·PDF·분석 실행 주소
│   │   ├── analysis/AGENTS.md       ← 리포트 1건 재무 분석, report_summaries 표, 웹 AI 자리 2개
│   │   ├── compare/AGENTS.md        ← 두 보고서 비교
│   │   ├── coverage/AGENTS.md       ← 커버리지 집계
│   │   └── review/AGENTS.md         ← 수동 검토와 되돌리기
│   └── web/AGENTS.md                ← 웹 서버 조립, 공통 보안 장치, 기능 등록 목록
├── frontend/                        ← React 화면 (빌드 결과 frontend/dist를 웹 서버가 내줌)
├── migrations/                      ← DB 구조·데이터 변경 SQL (번호 순서, 수동 적용)
├── scripts/                         ← run-batches.ps1(백필 반복 분류), start-workspace.ps1(웹앱 빌드·실행)
└── .githooks/                       ← 커밋·병합 커밋마다 전체 테스트
```

## 절대 규칙

1. **분류 LLM 동시 호출은 2, 백필 배치는 10이다.** 실제 병목이 분당 토큰 한도라서, 공급자 한도 확인과 LangSmith 토큰 추이 검증 없이 올리지 않는다.
2. **운영 데이터를 깨지 않는다.** DB 구조 변경은 `migrations/NNN_*.sql`로만 한다. 데이터를 직접 고칠 때는 바뀔 행을 먼저 백업하고 한 트랜잭션 안에서 바뀐 행 수를 확인한 뒤 커밋한다. 저장 버전 이름(`langgraph-tagger@2.0`, `llm-summary@1.0`, 종목표 `KRX@…`)과 화면이 쓰는 웹 API 모양을 바꾸지 않는다.
3. **커밋마다 전체 테스트(구조 검사 포함)가 돈다. `--no-verify`로 건너뛰지 않고, 테스트를 지워서 통과시키지 않는다.** 칸 경계·표 주인 위반은 `research_desk/tests/test_architecture.py`가 막는다.
4. **키·서비스 키·DB 주소·로컬 파일 경로는 브라우저 응답에 넣지 않는다.** 비밀 값은 `.env`에만 두고 커밋하지 않는다. 커밋은 파일 경로를 지정해서 올린다(`git add -A`·`git add .` 금지 — 미추적 사용자 파일이 있다).
5. **종목표 CSV를 바꾸면 `python -m research_desk stocks set-version --as-of <자료 기준일>`로 버전 정보 파일을 같이 고치고 한 커밋에 올린다.** 안 하면 분류 명령이 종료 코드 4로 멈춘다.

## 작업 전에 읽을 것

- 항상: `docs/standards.md`, `docs/engineering-notes.md`, 고칠 폴더의 `AGENTS.md`.
- **새 기능을 붙이기 전:** `research_desk/features/AGENTS.md`, `docs/architecture.md`의 코드 칸 지도와 표 주인, `docs/engineering-notes.md`의 "새 기능 붙이기" 점검표.
- **분류기(`research_desk/tagger`)나 백필을 건드리기 전:** `docs/operations.md`의 운영 원칙·자동 처리 사건 표, `docs/business-rules.md`의 상태 전이와 분류 규칙.
- **DB 쿼리·표·마이그레이션을 건드리기 전:** `docs/standards.md`의 표 주인 규칙과 데이터 규칙, `docs/engineering-notes.md`의 "운영 DB 데이터 고치기" 점검표(1000행 끊어 읽기 함정 포함).
- **웹 주소나 응답을 건드리기 전:** `docs/contracts.md` — 화면이 그 모양에 그대로 의존한다.
- **분류 체계 값(리포트 종류·사유·발행처 종류)이나 종목표를 바꾸기 전:** `docs/business-rules.md`의 값 집합과 종목표 버전 — DB 제약과 LLM 응답 모양을 함께 바꿔야 한다.
- **호스트·출처 검사, PDF 내주기, 비밀 값을 건드리기 전:** `docs/security.md`.
- **`.ps1` 스크립트나 `.githooks/`를 고치기 전:** `docs/standards.md`의 스크립트 규칙(PowerShell 5.1, BOM, LF).

## 문제가 생겼을 때

바로 사용자에게 알릴 것:
- 운영 DB 데이터가 의도와 다르게 바뀌었거나 바뀔 위험이 있을 때(백업 없는 대량 UPDATE·DELETE, 이상한 상태로 바뀐 행).
- 반복 분류 스크립트가 종료 코드 1(한 배치가 3번 다 실패)이나 3(되돌리기 실패)으로 멈췄거나, 행이 `processing`에 묶여 백필이 진행되지 않을 때.
- `tag inspect`의 비율(신뢰도·`review_needed`·분석 대상 외)이 짧은 시간에 크게 흔들릴 때 — 백필을 먼저 멈춘다.
- 키·서비스 키·세션 파일이 로그·응답·커밋에 노출됐을 때.
- 테스트나 구조 검사를 통과시킬 방법이 테스트 삭제·검사 우회밖에 없어 보일 때.

그 밖의 문제는 `docs/tracking/findings.md`에 "조건 → 증상, 영향, 지금 못 고치는 이유, 방법"으로 적는다.
