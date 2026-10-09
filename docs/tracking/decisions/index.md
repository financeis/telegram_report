# 결정 기록

| 번호 | 결정 | 날짜 |
|---|---|---|
| [0001](0001-single-package-with-boundary-checks.md) | 파이썬 코드를 패키지 하나에 두고, 칸 경계는 자동 검사로 지킨다 | 2026-10-08 |
| [0002](0002-replace-old-commands-without-forwarders.md) | 옛 명령을 넘겨주는 연결 없이 `python -m research_desk`로 완전히 바꾼다 | 2026-10-08 |
| [0003](0003-stock-list-version-file-and-stop-on-mismatch.md) | 종목표 버전은 내용 지문을 담은 버전 정보 파일이 정하고, 내용이 다르면 분류를 멈춘다 | 2026-10-08 |
| [0004](0004-exit-code-4-for-preparation-problems.md) | 다시 해도 안 풀리는 준비 문제는 종료 코드 4로 구분한다 | 2026-10-08 |
| [0005](0005-per-feature-readiness-isolation.md) | 웹 기능은 처음 쓰일 때 각자 준비하고, 준비 실패는 그 기능만 멈춘다 | 2026-10-08 |
| [0006](0006-full-test-suite-on-every-commit.md) | 커밋과 병합 커밋마다 전체 테스트를 돌린다 | 2026-10-08 |
| [0007](0007-tagging-concurrency-2-batch-10.md) | 분류 LLM 동시 호출 2, 백필 배치 10 | 2026-05 |
| [0008](0008-analysis-model-codex-luna.md) | 재무 분석·비교 모델은 `codex:gpt-6-luna`, Claude에서는 형식 강제 없이 검증 | 2026-10-08 |
| [0009](0009-analysis-without-routes.md) | 분석 기능은 웹 주소 없이 받은 행만 분석하고, 분석 주소는 리포트 기능이 맡는다 | 2026-10-08 |
| [0018](0018-page-picture-for-text-less-pdfs.md) | 글자 없는 PDF는 첫 장 그림을 AI에게 보여 준다 | 2026-10-10 |
| [0019](0019-closed-publisher-list-filename-tag-only-for-suspects.md) | 발행처는 사전의 닫힌 목록 안에서 AI가 고르고, 파일 이름 표기는 의심 표시·다시 분류 대상 고르기에만 쓴다 | 2026-10-10 |
| [0020](0020-requeue-to-pending-for-retagging.md) | 다시 분류는 대기 줄로 되돌리는 `tag requeue`로 한다 | 2026-10-10 |
