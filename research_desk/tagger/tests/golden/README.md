# Golden PDFs for research_desk.tagger

These PDFs were made for the v1 tagging rules, and the "Expected outcome" column
below records those v1 rules. The v2 rules the code follows differ: `IR자료` is
out of scope (`ir_self`), an unknown publisher or product no longer sends a row
to review, and `publisher_type=company` no longer exists. No test reads these
PDFs today; the classification rules are pinned by the parity fixtures
(`../parity/fixtures.json`). Do not change the code to match this table.

| File | Maps to | Expected outcome |
|---|---|---|
| `ipo_unlisted.pdf` | §6.5 Rule 4 (KRX-unmatched + IPO) | in-scope IPO, `stock_codes=[]`, `company_names=["ABC테크"]` |
| `domestic_with_foreign_peer.pdf` | `foreign_primary_coverage=false` (peer mention) | in-scope 단일종목, `stock_codes=["005930"]` |
| `ir_company_self.pdf` | §6.5 Rule 4 (`publisher_type=company`) | in-scope IR자료, `publisher="휴온스"` |
| `unknown_publisher_in_scope.pdf` | §6.6 `unknown_publisher` | `review_needed/low`, `notes="unknown_publisher:NewBoutique Research"` |
| `unknown_product_in_scope.pdf` | §6.6 `unknown_product` | `review_needed/low`, notes contains `"unknown_product:"` |
| `private_unlisted.pdf` | OOS private | `auto/medium`, `oos_reason="private"` |

## Synthesis

These PDFs are committed binary fixtures. They were generated via PyMuPDF
using the built-in `korea` CJK font (Helvetica cannot encode Hangul).

To regenerate them locally:

```bash
python -m research_desk.tagger.tests.golden._synthesize
```

The script (`_synthesize.py`) is idempotent and overwrites existing files.

## Note on Task 8 fixtures

`single_page_with_meta.pdf`, `page1_blank_meta_on_p2.pdf`, and
`no_meta_anywhere.pdf` are NOT committed — they are generated at test
runtime by an autouse fixture in `research_desk/tagger/tests/test_extract_pdf.py` and serve a
different purpose (exercising `extract_pdf`'s page-walk logic, not spec
boundary cases). The 6 PDFs above are the documented golden boundary
fixtures and ARE committed.
