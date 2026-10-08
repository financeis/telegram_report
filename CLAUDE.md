# CLAUDE.md — telegram_report

운영 중인 한국 증권 리서치 PDF 수집·태깅 파이프라인. 새 세션에서 작업 시 이 문서의 원칙을 따른다. 사람용 셋업 가이드는 [README.md](README.md), 설계 근거는 [docs/superpowers/specs/](docs/superpowers/specs/).

## 구조 한눈에

- **Phase 1 — 수집기 (루트)**: `collector.py` + `telegram_client.py` + `storage.py` → 텔레그램 채널 → PDF + Supabase `reports` 행 (status='pending')
- **Phase 2 — 태깅기 ([langgraph_tagger/](langgraph_tagger/))**: 8노드 LangGraph가 `pending` 행을 LLM으로 분류·KRX 매핑 → `auto`/`review_needed`로 마감
- **운영 백필 진입점**: [scripts/run-batches.ps1](scripts/run-batches.ps1) (PowerShell wrapper)
- **LLM 모델** (`.env`가 기준, 바꾼 뒤엔 워커·대시보드 재시작 — README "Analysis models"):
  - 태깅 기본 `LLM_MODEL_DEFAULT=claude-haiku-5-5`
  - 태깅 재처리(escalate) `LLM_MODEL_ESCALATION=gpt-5.4`
  - 요약·재무 분석 `LLM_MODEL_PHASE2=codex:gpt-6-luna` — API 대신 로컬 `codex exec`(ChatGPT 로그인 한도). 사용자가 고른 리포트만 분석하므로 호출량이 적음. Haiku는 재무 지표를 절반만 뽑고 형식 실패 12.5%라 제외(2026-10-08 12건 비교)
  - 모델 이름이 provider를 정함 ([langgraph_tagger/llm_provider.py](langgraph_tagger/llm_provider.py)): `claude-*` → `ANTHROPIC_API_KEY`, `codex:*` → 로컬 codex CLI, 그 외 → `OPENAI_API_KEY`. 롤백 = `.env` 모델명만 교체. 구 이름 `OPENAI_MODEL_*`도 인식.
  - 재무 분석(ExtractionResult)은 스키마가 커서 Claude에선 grammar 강제 대신 "스키마를 프롬프트에 넣고 JSON 텍스트 → pydantic 검증" 경로 (`constrained=False`). 태깅·비교는 grammar 강제.

## 운영 원칙 (위반 금지)

### 1. `MAX_CONCURRENT_LLM=2`가 운영 천장
- `.env.example`의 10은 **잘못된 값**. 실 운영 `.env`는 2.
- 실제 병목은 RPM이 아니라 **TPM**(분당 토큰 한도). gpt-5.4-mini 실측: 한도 200K, 동시성 2에서 피크 ≈134K (67%). 동시성 10이면 TPM 한도 초과로 429 빈발 → 토큰 비용 누적 + throughput 저하.
- 위 수치는 **gpt-5.4-mini(OpenAI) 기준**. 태깅은 이제 `claude-haiku-5-5`(Anthropic)라 한도 체계(입력·출력 토큰 한도 분리)와 행당 토큰(≈6K 입력/≈0.4K 출력)이 다르다. 재검증 전까지 천장 2를 유지.
- 올리려면 Anthropic Console(Limits)에서 Haiku 5.5의 토큰 한도 확인 + LangSmith trace에서 토큰 추이 검증 후 단계적으로 (예: 3 → 4) 시도. 임의 상향 금지.

### 2. 백필 BatchSize는 10이 정석
- `TAGGER_BATCH_SIZE_DEFAULT=10`. wrapper(commit `d7ee4e0`)가 이 단위로 end-to-end 검증됨.
- 동시성=2가 바인딩 제약이라 batch 크기를 100으로 키워도 총 throughput은 동일. batch=10의 이점:
  - 한 배치 실패 시 reset 범위 10건으로 작음
  - iteration 단위 JSON 보고가 자주 찍혀 모니터링 용이
- **임의 값(100, 200 등) 사용 금지.**

### 3. 백필 실행
```powershell
pwsh -File scripts\run-batches.ps1 -Iterations N -BatchSize 10
```
- 한 배치 ≈22~25초. 1,500 iter ≈ ~10시간이 ~15K건 백필 실측 추산 (gpt-5.4-mini 기준). Haiku 5.5 dry-run에선 행당 2~11초였음 — 첫 실운영 백필에서 재측정.
- Ctrl+C 안전. 다시 실행 = idempotent (pending 기준).

### 4. 자동 처리되는 사건 (수동 개입 금지)
| 사건 | 처리 |
|---|---|
| LLM 429·5xx·timeout·네트워크 (Anthropic/OpenAI) | orchestrator → 행 `pending` revert → 다음 iter 재시도 |
| per-row 90초 초과 | 동일 |
| invocation crash | wrapper → 워커 scope reset → 최대 2회 재시도 |
| **3회 연속 실패** | wrapper exit 1로 stop. **이때만 사람 개입.** |

### 5. 알려진 무해 노이즈 (디버깅하지 말 것)
- Pydantic serializer warning (`LLMExtraction` 직렬화 시) — 기능 영향 0.
- Windows native crash `exit=-1073741569` — wrapper가 자동 복구. 데이터 손실 0 검증됨 (3 iter 테스트 중 1회 발생, 5건 자동 reset 후 retry 성공).

## 모니터링

```bash
# 큐 분포 (Supabase)
python -m langgraph_tagger inspect

# LLM trace 에러 (LangSmith — 인증 필요)
langsmith trace list --project telegram_report --error --last-n-minutes 30
```

**정상 베이스라인** (2026-05 기준):
- confidence: high ~79% / medium ~18% / low ~3%
- `review_needed` ~2.6%
- OOS ~6% (ir_self가 최다)
- review 사유 분포: `krx_unmatched_in_scope` > `type_indeterminate` > `first_page_unreadable`

이 비율이 단시간에 크게 흔들리면 백필 멈추고 원인 파악.
태깅 모델이 2026-10-08부터 `claude-haiku-5-5`로 바뀌어 위 베이스라인(이전 모델 기준)이 다소 달라질 수 있다. 행에 모델명은 저장되지 않으므로, 모델별로 나눠 볼 땐 `tagged_at`으로 구분.

## 스키마·버전 불변식

- 태그된 행: `tagger_version='langgraph-tagger@2.0'`, `taxonomy_version='KRX@2026-05-08'`.
- v2 `out_of_scope_reason` ∈ {foreign, fund, digital, private, **ir_self**}.
- v2 `report_type` ∈ {단일종목, 산업, 섹터, IR자료, 전략·시황, 기타}.
- v2 `publisher_type` ∈ {broker, data_provider, ir_agency, other}.
- 마이그레이션 003 적용 완료, v1 잔재 0건. 새 마이그레이션은 `migrations/004_*.sql` 형식으로 추가.

## 사용자 컨텍스트

운영자(이 레포 소유자)는 **금융 현직자, 비전공자**다. 코드 디테일보다 **무엇이 / 왜 / 어떻게 돌아가는지**의 큰 그림을 우선시한다. CS 전문 용어는 비유로 풀어 설명할 것 (race condition → "두 명이 같은 줄 잡으려다 꼬임" 등). 결과 중심: "잘 돌고 있냐 / 뭐가 문제냐 / 다음 뭐 하면 되냐"가 주된 관심.
