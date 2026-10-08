# Telegram Report Collector

PDF research-report collector for Korean securities. Listens to a single Telegram channel and downloads new PDF attachments to local disk + Supabase metadata.

See [design spec](docs/superpowers/specs/2026-05-05-telegram-report-collector-design.md) for full design rationale.

## Setup (one-time)

1. **Clone / download** this repo.

2. **Create venv and install dependencies:**

   ```bash
   python -m venv .venv
   .venv\Scripts\activate            # Windows
   # source .venv/bin/activate       # Mac/Linux
   pip install -r requirements.txt
   ```

3. **Create Supabase tables.** Open your Supabase project → SQL Editor → paste the contents of `migrations/001_init.sql` → Run.

4. **Configure environment.** Copy the template and fill in your secrets:

   ```bash
   copy .env.example .env             # Windows
   # cp .env.example .env             # Mac/Linux
   ```

   Then edit `.env`:
   - `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`: from https://my.telegram.org
   - `TELEGRAM_CHANNEL`: channel username (default `sunstudy1004`)
   - `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`: from Supabase dashboard → Settings → API → `service_role` key (⚠️ secret — never commit)

5. **First run** (will prompt for SMS verification once):

   ```bash
   python main.py
   ```

## Usage

| Command | Effect |
|---|---|
| `python main.py` | Normal run: collect new PDFs since last run (parallel by default, N=4) |
| `python main.py --dry-run` | List what would be downloaded; write nothing |
| `python main.py --cutoff-days 7` | Override INITIAL_CUTOFF_DAYS for this run (FIRST run only) |
| `python main.py --backfill-days 365` | One-off historical backfill: fetch from N days ago, skip already-downloaded |
| `python main.py --dry-run --backfill-days 365` | Preview backfill: count "new" vs "already-known skipped" before committing |
| `python main.py -v` | Verbose (DEBUG level) logging |

Mutually exclusive: `--cutoff-days` and `--backfill-days` cannot be used together.

### Concurrency tuning

`MAX_CONCURRENT_DOWNLOADS` env var (default `4`) controls how many PDFs
download in parallel. Bump to `8` for faster backfill if FloodWait
warnings are absent; lower to `1` for strictly sequential behavior.
Single Semaphore is shared by Stage A retries and Stage B new fetches,
so total in-flight downloads stay bounded.

### Exit codes

- `0` Complete success
- `1` Total failure (config / auth / network)
- `2` Partial failure — some messages added to `failed_attempts` table; will be auto-retried next run

## Research Desk (React workspace)

The new company research UI runs at **http://127.0.0.1:8520/**. It uses the
existing Supabase reports, financial summaries, PDF files and favorites.

```powershell
.venv/Scripts/python.exe -m pip install -r requirements-workspace.txt
pwsh -File scripts/start-workspace.ps1
```

Node.js 22.12+ (or 20.19+) is needed to build the frontend. The launcher installs
frontend dependencies on first use, builds the UI and runs the local server.
After building, `python -m langgraph_tagger.workspace` starts it directly.
For frontend development, run `npm --prefix frontend run dev` alongside the
Python server; Vite proxies `/api` to port 8520.

- Search companies by name/code; reuse the existing favorites.
- Browse all saved company reports with publisher, report type, period and analysis filters.
- Explore industry/product coverage, report-type volume (including out-of-scope
  material), company activity and publisher shares. Switch day/week/month, filter
  periods, inspect the data table or export CSV.
- Review the manual queue alongside the first three PDF pages: approve, mark
  out-of-scope with a reason, queue retagging, skip, and undo the last action.
- Open a report's summary, financial estimates, valuation and page-level sources.
- Select any two reports for the same company and compare target prices,
  compatible estimates, investment theses and valuations; open both PDFs together.
- Check any number of single-company reports and click **선택한 N건 분석**.
  Only those IDs are submitted; existing detailed analyses are reused. Progress
  shows each result, supports stopping remaining work, and retries only failures.
  Individual report analysis remains available. Two LLM calls at most run
  concurrently within this server.

Comparison numbers are calculated for the selected pair. A saved AI comparison
narrative appears only when it belongs to that exact pair. Different publishers
are labeled as differing views, and missing or incompatible estimates remain
uncompared. A new comparison narrative runs only through the explicit button
for the selected two reports; viewing or selecting reports never starts an LLM.

This is a **local, single-user application** bound to `127.0.0.1`; Supabase and
LLM API credentials remain on the Python server. All former Streamlit dashboard
and review functionality now lives here. The old `python -m langgraph_tagger.analytics`
and `python -m langgraph_tagger.review_viewer` commands also launch Research Desk
(coverage and review entry views, respectively).

## Analysis models

Tagging (`LLM_MODEL_DEFAULT`) defaults to `claude-haiku-5-5`; financial
extraction/report comparison (`LLM_MODEL_PHASE2`) defaults to `gpt-6-luna`,
which extracted about twice as many financial metrics as Haiku with no format
failures in a side-by-side check. A `codex:` prefix (e.g.
`LLM_MODEL_PHASE2=codex:gpt-6-luna`) runs the model through the local Codex CLI
(`codex exec`, using its ChatGPT login instead of an API key); this suits Phase 2
because it only runs on reports a user selects. `CODEX_REASONING_EFFORT`
(default `high`) sets its reasoning depth. Tagging escalation
(`LLM_MODEL_ESCALATION`) defaults to `gpt-5.4`. The model name picks the provider:
`claude-*` models use `ANTHROPIC_API_KEY`, `codex:*` models use the Codex CLI login,
anything else uses `OPENAI_API_KEY`, so switching or rolling back is a `.env`
change only (e.g. `LLM_MODEL_PHASE2=gpt-6-luna` for the API). The
legacy `OPENAI_MODEL_*` names are still read when the `LLM_MODEL_*` ones are unset.
Claude thinking depth is set by `ANTHROPIC_EFFORT` (default `medium`).
Measurements behind these choices: [docs/llm-models.md](docs/llm-models.md).
Restart running workers and the dashboard after changing these environment
variables. Saved analyses keep their original model metadata and are reused;
changing models does not trigger a bulk reanalysis.

## Financial research details

Research Desk's report detail and comparison views show:

- **실적 전망**: fiscal-period estimates, actuals/guidance, units, accounting basis,
  explicitly reported prior estimates, and page-level evidence.
- **밸류에이션**: valuation method, assumptions, target-price change drivers, and
  the publisher's original recommendation labels and definitions.
- **투자 논리·촉매**: claims, causal mechanisms, monitoring metrics, catalysts,
  timing and conditions. Reconsideration conditions distinguish explicit source
  statements from model-derived implications.
- **보고서 비교**: same-publisher changes or cross-publisher differences, with
  numerical comparisons only for matching periods, units, basis and scenarios.

For an existing database, apply `migrations/006_financial_details.sql` once before
running the updated application. It adds two nullable JSONB fields to the existing
summary table. Existing basic summaries remain readable.

Analyze reports from Research Desk (see above). Each analysis extracts financial
details. Reports that already have a detailed analysis are reused; reports with
only a basic summary are analyzed again when selected.

Numeric observations without support in their cited quote and PDF page are omitted.
This check does not guarantee correct table-column alignment. Missing figures stay
empty, and source evidence remains available for review. Comparisons use previously
saved analyses; they do not claim complete market consensus. OCR, collection,
tagging, market data and backtesting are outside this feature.

## Development tests

Run tests:

```bash
pip install -r requirements-dev.txt
pytest -v
```

## Troubleshooting

- **"Missing required env var: X"** — Add the key to `.env`.
- **SMS code prompt every run** — The `sessions/samstudy.session` file is missing or got deleted. Telethon re-authenticates each time.
- **Persistent failures in `failed_attempts`** — Check the `error_message` and `attempt_count` columns. If `attempt_count > 10` for a row, the message is likely permanently broken; manually inspect or DELETE the row to stop retrying.

## Security

- `.env` and `*.session` files contain credentials and account access. They are in `.gitignore` — keep it that way.
- `SUPABASE_SERVICE_KEY` bypasses Row-Level Security. Treat it as a master password.
