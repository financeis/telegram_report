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

The 19 cases below cover the 6 v2 `report_type`s, all 5 OOS reasons
(`foreign` / `fund` / `digital` / `private` / `ir_self`) and the
`krx_name_code_mismatch` boundary scenario (the first 12 cases, whose
expected values did not change when the picture and publisher rules came
in), then the publisher checks (a filename tag pointing to another
publisher, an alias answer stored as null, no tag and no publisher, an
IR자료 with a broker's filename tag) and picture-only PDFs (no text on the
page, so page 1 is drawn and sent to the AI). Reference docs:

- `docs/business-rules.md` (분류 규칙) for the v2 policy (oos_gate / mark_oos_reason /
  decide_status); the original v2 design note is kept off git.
- `research_desk/tagger/llm_schemas.py` for the LLMExtraction v2 fields.
- `research_desk/tagger/sql.py::UPDATE_SQL` for the 19-arg payload.
- `research_desk/tagger/vocabulary/publishers.yaml` for canonical publisher
  names. The LLM answers `publisher_canon` only: one canonical name or
  null. Anything else (an alias such as `Eugene`, a typo) is stored as
  null without an error, and `publisher_type` comes from the dictionary
  section of the stored name, never from the LLM. The filename tag
  (`_YYYYMMDD_<tag>_<digits>.pdf`) only adds `publisher_suspect:*` notes.
- `research_desk/tagger/nodes/decide_status.py::apply_final_rules` for the
  rules applied last: a row read from the page picture (`page_image`) or
  with a suspect publisher is at most `medium` (`low` stays `low`), and its
  notes are the base note, then `page_image`, then
  `publisher_suspect:<why>`, joined by `;`.
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
| `publisher_filename_mismatch` | tag `MERITZ`, AI 키움증권 → stored 키움증권 + `publisher_suspect:filename_mismatch` | auto / medium |
| `publisher_alias_answer_null` | AI answers the alias `Eugene` → stored null, tag `Eugene` → `publisher_suspect:filename_mismatch` | auto / medium |
| `publisher_unknown_null`     | no filename tag, AI null → `publisher_suspect:unknown`  | auto / medium        |
| `oos_ir_self_broker_filename_tag` | IR자료 (해당기업) with tag `MERITZ` → ir_self + `publisher_suspect:filename_mismatch` | auto / medium |
| `picture_only_단일종목`      | no text → page 1 picture, KRX-matched 단일종목, tag `Kiwoom` agrees → `page_image` | auto / medium |
| `picture_only_oos_foreign`   | no text → page 1 picture, foreign_primary_coverage=true → `page_image` | auto / medium |
| `picture_only_unmatched_suspect_review` | no text → page 1 picture, KRX 미매칭 단일종목, AI null, no tag → `krx_unmatched_in_scope:ipo_pending_or_unknown;page_image;publisher_suspect:unknown` | review_needed / low |

## Field semantics

`expected` keys map directly to columns in the UPDATE_SQL bind tuple
(no `_contains` suffix in v2 — assertions are exact equality, except
list-valued columns which compare element-wise after `list()` cast).

OOS rows preserve the LLM-emitted `report_type` / `title` / `analysts` /
`stock_codes_raw` / `company_names_raw` (audit) and the checked
`publisher` / `publisher_type` (a canonical name and its dictionary
section, or null), but force `stock_codes` / `company_names` /
`sectors_*` / `products` to empty.

For `단일종목_mismatch_medium` the KRX entry name overwrites
`company_names` while `company_names_raw` retains the LLM-emitted name
so reviewers can audit the discrepancy.

The PDF body in each fixture is synthesized by the test driver via
PyMuPDF with `fontname="korea"` so Hangul roundtrips through
`extract_pdf` cleanly. The `picture_only_*` cases have an empty
`pdf_text`: the page has no text, so `extract_pdf` draws page 1 and the
mock model (`gpt-5.4-mini`, which takes images) answers from the picture.
