# Parity fixtures (v2)

This directory holds the hand-curated regression cases that verify the
LangGraph row-graph reproduces the documented v2 classification policy.
Each `case` in `fixtures.json` records a `(input PDF + LLM mock)
→ (expected DB UPDATE payload)` triple. `test_parity.py` runs the entire
row graph headlessly per fixture and asserts the payload columns match
the v2 19-arg UPDATE_SQL bind tuple.

## Fixture provenance

Inputs (`pdf_text`, `caption`, `sent_at`) and `llm_mock` payloads are
**hand-curated** to exercise a single representative path per case,
informed by the v2 spec rules and the KRX index entries shipped with
the repo.

The 12 cases below cover the 6 v2 `report_type`s, all 5 OOS reasons
(`foreign` / `fund` / `digital` / `private` / `ir_self`), and the
`krx_name_code_mismatch` boundary scenario. Reference docs:

- `docs/superpowers/specs/2026-05-07-report-metadata-tagging-routine-design.md`
  for the v2 policy (oos_gate / mark_oos_reason / decide_status).
- `research_desk/tagger/llm_schemas.py` for the LLMExtraction v2 fields.
- `research_desk/tagger/sql.py::UPDATE_SQL` for the 19-arg payload.
- `research_desk/tagger/vocabulary/publishers.yaml` for canonical publisher
  names. The LLM emits `publisher_canon` / `publisher_type` directly
  in v2 (no system-side alias resolution).
- `docs/stock_data/KRX_stocks_data.csv` for KRX validation / enrichment.
  Stock codes referenced (`005930` 삼성전자, `000660` SK하이닉스) are
  both present in the snapshot used by tests, with sector_major=반도체
  and sector_minor=메모리반도체.

## Cases

| Case                         | Path through graph                                     | Status / Confidence  |
|------------------------------|--------------------------------------------------------|----------------------|
| `단일종목_auto_high`         | KRX-matched 단일종목                                   | auto / high          |
| `단일종목_unmatched_review`  | KRX 미매칭 단일종목 (IPO 후보 등)                      | review_needed / low  |
| `단일종목_mismatch_medium`   | stock_code 매칭이지만 회사명 raw mismatch               | auto / medium        |
| `산업_auto_high`             | KRX skip, sectors 빈 배열                               | auto / high          |
| `섹터_aggregates_n`          | 005930 + 000660 union (sector aggregate)                | auto / high          |
| `섹터_zero_match_auto`       | 0개 매칭이어도 섹터는 auto                              | auto / high          |
| `전략시황_auto_high`         | 전략·시황: KRX skip                                     | auto / high          |
| `oos_foreign`                | foreign_primary_coverage=true (report_type 보존)        | auto / high          |
| `oos_fund`                   | etf_or_fund=true                                        | auto / high          |
| `oos_digital`                | digital_asset=true                                      | auto / high          |
| `oos_private`                | private_company_likely=true + KRX 미매칭                | auto / medium        |
| `oos_ir_self`                | report_type='IR자료' → 자동 OOS ir_self                 | auto / high          |

## Field semantics

`expected` keys map directly to columns in the UPDATE_SQL bind tuple
(no `_contains` suffix in v2 — assertions are exact equality, except
list-valued columns which compare element-wise after `list()` cast).

OOS rows preserve the LLM-emitted `report_type` / `publisher` /
`publisher_type` / `title` / `analysts` / `stock_codes_raw` /
`company_names_raw` (audit) but force `stock_codes` / `company_names` /
`sectors_*` / `products` to empty.

For `단일종목_mismatch_medium` the KRX entry name overwrites
`company_names` while `company_names_raw` retains the LLM-emitted name
so reviewers can audit the discrepancy.

The PDF body in each fixture is synthesized by the test driver via
PyMuPDF with `fontname="korea"` so Hangul roundtrips through
`extract_pdf` cleanly.
