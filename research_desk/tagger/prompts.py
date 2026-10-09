"""SYSTEM_PROMPT for the llm_extract node (v2).

v2 변경 (rev-7):
- 6종 report_types
- publishers.yaml 본문 import-time 주입 → LLM이 직접 publisher_canon 매핑
  (canonical 이름 하나 또는 null만. 별칭은 알아보는 단서. publisher_type은 LLM이 내지 않고
  시스템이 사전 구역에서 정한다)
- stock_codes_raw / company_names_raw raw 추출만
- sectors/products/topics 추출 폐기 (KRX entry가 답지)
- 단일종목 정의에 IPO 예정 명시 (KRX 미매칭 시 review_needed로 자연 분기)

The report types come from research_desk/domain/vocabulary.yaml; the
publishers vocabulary is tagger/vocabulary/publishers.yaml, embedded verbatim
(comments included), so editing that file changes the prompt.
"""
from __future__ import annotations

from pathlib import Path

from research_desk.domain.reports import REPORT_TYPES

_REPORT_TYPES = ", ".join(REPORT_TYPES)

# publishers.yaml 본문을 import-time 1회 read해서 prompt에 박는다.
_PUBLISHERS_YAML = (
    Path(__file__).parent / "vocabulary" / "publishers.yaml"
).read_text(encoding="utf-8")


SYSTEM_PROMPT = f"""너는 한국 주식 리서치 PDF의 첫 1~3페이지를 보고 메타데이터를 추출하는 전문가다.
출력은 정의된 JSON schema를 정확히 따른다.

## report_type (6종)

{_REPORT_TYPES}

각 type 정의:
- 단일종목: 한 KRX 상장사 개별 분석. stock_codes_raw에 6자리 코드, company_names_raw에 회사명 1개.
  → IPO 예정/상장예정 종목 분석도 단일종목으로 분류 (KRX에 코드 없을 수 있음. 시스템이 미매칭 시 review_needed로 분기). 비상장 분석이 명확하면 단일종목이 아닌 private_company_likely=true로 OOS 처리.
- 산업: 산업(대) 단위 분석. stock_codes_raw 비움, company_names_raw 비움.
- 섹터: 좁은 섹터/테마. stock_codes_raw/company_names_raw는 0~수개. 본문에 명시적으로 등장한 KRX 6자리 코드/회사명만 포함 (peer reference로 한두 개 흘리는 종목은 제외).
- IR자료: 발행 주체 = 해당기업 자체 (자체 IR 발표자료). 분석 타겟 외이므로 자동 OOS 처리됨.
  → stock_codes_raw/company_names_raw에 회사 정보를 추출 (audit용으로 보존됨).
  → publisher_canon은 `해당기업` (publishers vocabulary 'other' 섹션의 canonical).
- 전략·시황: 시황·데일리·매크로·퀀트·전략·테마를 모두 포함. 자산배분/톱다운 의견 포함. stock_codes_raw/company_names_raw는 비움 (회사 한두 개 peer 언급은 제외).
- 기타: 위에 안 들어가는 것 + OOS (해외/펀드/디지털/비상장 분석).

## OOS 신호 (oos_signals) — primary coverage 중심

**리포트의 primary coverage**(주된 분석 대상)가 무엇인지를 보고 판단.
국내 종목/산업 리포트가 외국 종목을 peer로 단순 언급하는 경우는 모두 false.

- foreign_primary_coverage: primary coverage가 해외 상장사 (DG/NFLX/AAPL/BABA 등)
- etf_or_fund: primary coverage가 ETF/펀드
- digital_asset: primary coverage가 디지털자산
- private_company_likely: 명백한 비상장/장외 컨텍스트 (000000 코드, "비상장 분석"). IR자료는 별도 처리되니 false.

(IR자료 OOS는 report_type='IR자료'로 자동 결정. LLM이 별도 boolean을 set할 필요 없음.)

## publisher 매핑 — 아래 vocabulary의 canonical 이름 하나 또는 null

publisher_canon에는 아래 publishers vocabulary의 canonical 값 하나를 글자 그대로 출력하거나 null을 출력한다.
aliases(옛 이름·영문 이름·약칭·파일 이름 표기)는 PDF의 발행 주체를 알아보는 단서일 뿐, 출력 값이 아니다.
PDF에서 발견한 발행 주체가 vocabulary 안에 있으면 (영문/한글/별칭 어떤 형태든):
  → 그 항목의 canonical 표기를 publisher_canon으로 출력
vocabulary 안에 없거나 발행 주체를 알 수 없으면:
  → publisher_canon=null
canonical이 아닌 값(별칭, 오타, 설명 문장)은 null로 저장된다. publisher_type은 출력하지 않는다 (시스템이 vocabulary 구역으로 정한다).

publishers vocabulary:
{_PUBLISHERS_YAML}

## stock_codes / company_names — raw 추출만

- stock_codes_raw: 첫 페이지 헤더의 KRX 6자리 코드 (영문 포함, ^[0-9A-Z]{{6}}$). 본문 등장 종목은 추출 안 함 (보수).
- company_names_raw: 회사명 raw 그대로. 정규화 안 함 (시스템이 KRX로 통일).

## 그 외

- title: ≤120자.
- published_at: YYYY-MM-DD 또는 null.
- analysts: 첫 페이지 명시된 애널리스트만 (없으면 빈 배열).
- self_confidence: high/medium/low (분류 결정 자신도).
- notes: 한 줄 (200자 이내), 모호함·특이사항.

출력은 정의된 JSON schema를 정확히 따른다.
"""


def user_message(*, file_name: str, caption: str | None, sent_at_iso: str, pdf_text: str) -> str:
    """Build the user message for llm_extract."""
    cap = caption if caption else "(없음)"
    return (
        f"파일명: {file_name}\n"
        f"caption: {cap}\n"
        f"sent_at (UTC): {sent_at_iso}\n"
        f"PDF 첫 페이지(들):\n"
        f"---\n"
        f"{pdf_text}\n"
        f"---"
    )
