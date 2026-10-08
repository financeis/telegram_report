# 0002 — 옛 명령을 넘겨주는 연결 없이 완전히 바꾼다

## 배경
옛 명령은 `python main.py`(수집), `python -m langgraph_tagger run|inspect|escalate|reset-worker`(분류), `python -m langgraph_tagger.workspace` / `.analytics` / `.review_viewer`(웹앱 세 입구)였다. 구조를 바꾸면서 옛 이름을 새 명령으로 넘겨주는 얇은 연결을 남길지 정해야 했다.

## 결정
모든 실행은 `python -m research_desk <명령>` 하나다: `collect`, `tag run|inspect|escalate|reset-worker`, `web [--view reports|market|review]`, `stocks set-version`. 옛 명령과 옛 경로는 남기지 않는다(사용자 선택).

## 버린 대안
- **옛 명령을 새 명령으로 넘겨주는 연결 유지**: `langgraph_tagger` 폴더가 계속 남아 이름과 내용이 어긋나고, 새 코드가 옛 이름을 따라 하게 된다.

## 결과
- `python main.py`와 `python -m langgraph_tagger …`는 이제 오류가 난다. 옛 루트 모듈을 import하던 사용자 개인 스크립트(예: `.pytest_cache/`에 있던 9월 수집 점검 스크립트)도 동작하지 않는다.
- 웹 서버의 보기 화면 선택은 옛 세 입구 대신 `web --view …` 하나다.
- 스크립트(`scripts/*.ps1`)와 안내 문서의 명령도 새 이름이다. 새 명령을 붙일 때도 이 입구 하나에 등록한다.
