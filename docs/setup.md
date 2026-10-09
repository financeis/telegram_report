# Setup and commands

The project overview, screens and diagrams are in the [README](../README.md). This page is the
setup and command reference (in English); operating procedures are in [operations.md](operations.md).

Research Desk collects Korean securities research PDFs from a single Telegram channel and downloads
new PDF attachments to local disk + Supabase metadata.

The same Python package, `research_desk`, then tags each report with an LLM (`tag`) and serves
Research Desk, the local web app for reading, analyzing, comparing and reviewing the reports (`web`).
Two batch jobs feed it more: a daily stock price snapshot from the KIS Open API (`prices update`)
and a yearly build of business profiles from annual business reports (`peers build`) that finds
companies with a similar business — the peers tab and the theme search in Research Desk.

Design: [architecture.md](architecture.md) and the decision records in [tracking/decisions/](tracking/decisions/index.md).

## Setup (one-time)

1. **Clone / download** this repo.

2. **Create venv and install dependencies:**

   ```bash
   python -m venv .venv
   .venv\Scripts\activate            # Windows
   # source .venv/bin/activate       # Mac/Linux
   pip install -r requirements.txt
   ```

3. **Turn on the commit checks.** They run the test suite, so install the development
   requirements too:

   ```bash
   pip install -r requirements-dev.txt
   git config core.hooksPath .githooks
   ```

   From then on every commit and every merge commit runs the full test suite
   (`python -m pytest`, including the architecture check) with the Python in the repository's
   `.venv`; a failing test, or no `.venv` Python, blocks the commit. Never skip the checks:
   committing with `--no-verify` is forbidden.

4. **Create Supabase tables.** Open your Supabase project → SQL Editor → paste the contents of each file in `migrations/`, in number order starting with `001_init.sql` → Run.

5. **Configure environment.** Copy the template and fill in your secrets:

   ```bash
   copy .env.example .env             # Windows
   # cp .env.example .env             # Mac/Linux
   ```

   Then edit `.env`:
   - `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`: from https://my.telegram.org
   - `TELEGRAM_CHANNEL`: the channel's username (a private channel also needs `TELEGRAM_CHANNEL_ID`; see `.env.example`)
   - `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`: from Supabase dashboard → Settings → API → `service_role` key (⚠️ secret — never commit)
   - `SUPABASE_DB_URL`: from Supabase project settings → Database → Connection string (URI); every `tag` command needs it
   - `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`: the key of each provider your models use (see [Analysis models](#analysis-models)); the peers features always need `OPENAI_API_KEY` for embeddings
   - `KIS_APP_KEY`, `KIS_APP_SECRET`: the Korea Investment & Securities Open API key of a real account, for `prices update` (⚠️ secret — the same key as the masterdb project)

6. **First run** (will prompt for SMS verification once):

   ```bash
   python -m research_desk collect
   ```

   Run every command from the repository root: `sessions/` and the relative paths in `.env`
   are resolved from the current folder.

## Commands

Every command is `python -m research_desk <command>`; `--help` on any command lists its options.

| Command | Effect |
|---|---|
| `python -m research_desk collect` | Normal run: collect new PDFs since last run (parallel by default, N=4) |
| `python -m research_desk collect --dry-run` | List what would be downloaded; write nothing |
| `python -m research_desk collect --cutoff-days 7` | Override INITIAL_CUTOFF_DAYS for this run (FIRST run only) |
| `python -m research_desk collect --backfill-days 365` | One-off historical backfill: fetch from N days ago, skip already-downloaded |
| `python -m research_desk collect --dry-run --backfill-days 365` | Preview backfill: count "new" vs "already-known skipped" before committing |
| `python -m research_desk collect -v` | Verbose (DEBUG level) logging |
| `python -m research_desk tag run` | Claim a batch of `pending` rows and tag them; prints a JSON report. Options: `--batch-size N` (default `TAGGER_BATCH_SIZE_DEFAULT`, 10), `--model M` (default `LLM_MODEL_DEFAULT`), `--dry-run` (calls the model, writes nothing, and lists each row's result under `rows`), `--row-ids 1,2,3` (re-tag these rows whatever their status), `--max-concurrent-llm N` (default `MAX_CONCURRENT_LLM`, 2), `--worker-id W` |
| `python -m research_desk tag inspect` | Print the queue as JSON: `pending`, `processing`, `auto`, `review_needed`, `verified`, `oos_total`, `last_24h` |
| `python -m research_desk tag escalate --since <ISO time>` | Re-tag the `review_needed` rows tagged since that time (e.g. `2026-05-08T09:00`, read as UTC unless it has an offset such as `+09:00`) with `LLM_MODEL_ESCALATION`. Options: `--model M`, `--max-concurrent-llm N` |
| `python -m research_desk tag reset-worker --worker-id W` | Put one worker's `processing` rows back to `pending` (cleanup after a crashed run) |
| `python -m research_desk tag requeue <criteria> [--apply]` | Send classified `auto` / `review_needed` rows that meet the chosen criteria back to `pending`, so the usual backfill re-tags them under today's rules (no AI call; `verified` rows are never touched). Criteria, at least one: `--unreadable`, `--publisher-not-in-dictionary`, `--publisher-filename-mismatch`, `--publisher-type-mismatch`, `--krx-unmatched`. Without `--apply` it only prints a preview; `--apply` refuses while a backfill, escalate, collect or the web app runs, writes a CSV backup to `backups/requeue/` and reverts the rows in one transaction. See [docs/operations.md](operations.md) |
| `python -m research_desk web` | Start Research Desk on http://127.0.0.1:8520/; `--view market` / `--view review` open the coverage / review views (default `--view reports`) |
| `python -m research_desk stocks set-version --as-of YYYY-MM-DD` | Record the stock list's version after replacing it (see [Stock list update](#stock-list-update)) |
| `python -m research_desk prices update` | Fetch every stock of the stock list from KIS and refresh the price snapshot and its run record (daily, through `scripts\run-prices.ps1`) |
| `python -m research_desk prices update --codes 005930,080220` | A check: fetch only these stocks and print the computed rows; writes nothing |
| `python -m research_desk peers build` | The yearly peers build: business profiles from the annual reports (MongoDB `FS.A001_v2`), embeddings, and a public build for the peers tab and the theme search. Options: `--fiscal-year N`, `--codes …` / `--limit n` (fewer targets), `--pilot` (a trial run that is never published and prints each target's ten nearest companies) |
| `python -m research_desk peers inspect` | Print profile counts and tokens by fiscal year, profile version and status, and the latest builds, as JSON |

Mutually exclusive: `--cutoff-days` and `--backfill-days` cannot be used together.

`peers build` needs the web packages too (`requirements-workspace.txt`): it asks the reports
feature whether tagging is running, and that loads FastAPI. It does not start while tagging is
running (exit `1`), and a tagging backfill should not be started while it runs. The procedures for
the daily prices and the yearly build are in [docs/operations.md](operations.md).

`tag inspect` and `tag reset-worker` need only `SUPABASE_DB_URL`; `tag requeue` needs it and a
readable publisher dictionary, but no API key; `tag run` and `tag escalate` also check the
model's API key (or, for a `codex:` model, the codex CLI) and the stock list before they take
any row.

### Download concurrency (`collect`)

`MAX_CONCURRENT_DOWNLOADS` env var (default `4`) controls how many PDFs
download in parallel. Bump to `8` for faster backfill if FloodWait
warnings are absent; lower to `1` for strictly sequential behavior.
Single Semaphore is shared by Stage A retries and Stage B new fetches,
so total in-flight downloads stay bounded.

### Exit codes

`collect`:

- `0` Complete success
- `1` Total failure (config / auth / network)
- `2` Partial failure — some messages added to `failed_attempts` table; will be auto-retried next run

`tag` (every subcommand), `stocks set-version`, `prices update` and `peers build` / `peers inspect`:

- `0` Done (`prices update`: an `ok` or `partial` run; `peers build`: a `done` or pilot build)
- `4` Preparation problem — a missing setting (`<NAME> is required`, or a Korean sentence for
  `prices` and `peers`), no codex CLI for a `codex:` model, a stock list that cannot be read or
  does not match its version file, a bad `--as-of` date, an unreachable MongoDB or no business
  report texts, and for `tag requeue` an unreadable publisher dictionary, `--unreadable` with a
  tagging model that takes no images, or a process list that cannot be read. The reason is
  printed on stderr; `tag` stops before it takes or changes any row, `stocks set-version` leaves
  the version file as it was, `prices update` calls neither KIS nor the DB, and `peers build`
  starts no build. Fix the cause, then run again — retrying alone does not help.
- `1` Any other error; also a `failed` price run (more than 20 % of the stocks not received —
  the snapshot is left as it was) and an `incomplete` peers build, a peers build refused because
  tagging or another build is running, and `tag requeue --apply` refused because a backfill,
  escalate, collect or web app runs on this PC (nothing changed; stop or wait for it, then run
  again). `tag requeue` prints one line on stderr and nothing on stdout.

Every command: missing or wrong arguments print the usage and exit `2`; `--help` exits `0`.

## Tagging backfill

To work through many `pending` rows, run consecutive `tag run` batches with the wrapper script:

```powershell
powershell -File scripts\run-batches.ps1 -Iterations N -BatchSize 10
```

- The scripts run on Windows PowerShell 5.1 (`powershell`); PowerShell 7 (`pwsh`) is not needed.
- **Batch size 10 is the standard, and tagging concurrency stays at 2 (`MAX_CONCURRENT_LLM=2`).**
  Do not raise either casually: the model provider's tokens-per-minute limit is the real
  bottleneck. The operating principles in [docs/operations.md](operations.md) give the measurements and the procedure for raising them.
- Each iteration is one batch with its own worker id. If a batch fails, the script puts that
  worker's rows back to `pending` (`tag reset-worker`) and retries it up to 2 more times; when
  all 3 attempts fail it stops with exit `1` (exit `3` if the reset itself fails).
- A preparation problem (`tag run` exit `4`, see [Exit codes](#exit-codes)) stops the script at
  once with exit `4`, without reset or retry. Follow the message, then start it again.
- Ctrl+C is safe; starting it again continues with the remaining `pending` rows.
- The script finds the repository's `.venv` Python itself; `-Python <path>` or
  `$env:RESEARCH_DESK_PY` override it.

## Stock list update

Tagging matches company codes and names against the stock list
`docs/stock_data/KRX_stocks_data.csv` (`KRX_CSV_PATH`). The version file next to it,
`KRX_stocks_data.version.json`, records the list's version `KRX@<as-of date of the data>` with a
hash of its content, and every tagged row stores that version in `taxonomy_version`.

After replacing the CSV, record its version:

```bash
python -m research_desk stocks set-version --as-of <as-of date of the data, YYYY-MM-DD>
```

It checks the date and the CSV header, writes the version file and prints the new version. On a
failure it prints the reason, exits `4` and leaves the version file as it was; writing the same
date again is allowed. Until you run it, `tag run` and `tag escalate` stop with exit `4` and tell
you to run it, while Research Desk only logs a warning and keeps working. Commit the CSV together
with its version file: a test checks that they match, so the commit checks block a CSV change
without its version.

Rows already sent to review because a stock was missing from the old list are not re-tagged by
themselves: re-tag them with `tag escalate --since <ISO time>` or the review screen's retag.

## Research Desk (React workspace)

The new company research UI runs at **http://127.0.0.1:8520/**. It uses the
existing Supabase reports, financial summaries, PDF files and favorites.

```powershell
.venv/Scripts/python.exe -m pip install -r requirements-workspace.txt
powershell -File scripts\start-workspace.ps1
```

Node.js 22.12+ (or 20.19+) is needed to build the frontend. The launcher installs
frontend dependencies on first use, builds the UI and runs the local server.
After building, `python -m research_desk web` starts it directly; `--view market`
and `--view review` open the coverage and review views.
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
- On a company page, the **유사 기업** tab lists companies with a similar business (whole company
  or one segment), with their broker report counts, the price reaction against the market and a
  highlight for candidates (no broker report, not yet risen); **테마로 기업 찾기** finds companies
  by a phrase such as "레거시 DRAM". Both need a published `peers build` and the price snapshot.
- A status line on top of every screen shows the dates of the price snapshot and of the newest
  report, in red with the reason when one is behind.
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
LLM API credentials remain on the Python server.

Each feature prepares itself on first use. If one cannot — for example without the DB settings
(`SUPABASE_URL`, `SUPABASE_SERVICE_KEY`), with an unreadable stock list, or without the analysis
model's API key or codex CLI when an analysis or a comparison narrative is about to call the
model — only that feature's screens answer with the reason
(`<기능> 기능을 지금 쓸 수 없습니다: <이유>`); the rest keeps working. After you fix the cause,
the next request tries again: a restored file or a value newly added to `.env` needs no restart,
but a changed existing `.env` value does.

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
Measurements behind these choices: [docs/llm-models.md](llm-models.md).
Restart running workers and Research Desk after changing these environment
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

## Code layout

All Python code is one package, `research_desk/`:

| Part | Role |
|---|---|
| `__main__.py`, `cli.py` | Command entry (`python -m research_desk`) and the one command list |
| `core/` | Shared infrastructure: `.env` settings, Supabase / Postgres connections, LLM providers and embeddings, the KIS Open API client, MongoDB reads, PDF files; knows no business concept |
| `domain/` | Shared rules and reference data: report vocabulary, the in-scope rule, the stock list and its version |
| `collector/` | Telegram channel → PDF files + `pending` rows in `reports` (`collect`) |
| `tagger/` | `pending` rows → `auto` / `review_needed` with the LangGraph row graph (`tag`) |
| `features/` | Research Desk features, one folder each: `companies` (company list, favorites), `reports` (company reports, PDF, analyze), `analysis` (one report's financial analysis), `compare` (two reports side by side), `coverage` (research coverage counts, broker report counts), `review` (manual review queue), `prices` (daily price snapshot, `prices update`), `peers` (yearly peers build, peers tab and theme search), `freshness` (the status line) |
| `web/` | Web server assembly: security checks, error answers, screen files, the feature list (`web`) |
| `tests/` | The architecture check |

A feature exposes only the names its `__init__.py` binds: other parts import
`research_desk.features.<name>` or those names, never its inner modules. A new feature is a new
folder under `features/` plus one line in the feature list in `web/app.py` (and one in `cli.py`
if it adds a command). A feature with a command binds no FastAPI name in its `__init__.py` (every
command imports it) and hands its web router out through `web_router()`. The architecture check (`research_desk/tests/test_architecture.py`)
enforces these boundaries, so the tests and the commit checks fail when code crosses them.

## Development tests

Run tests:

```bash
pip install -r requirements-dev.txt
python -m pytest
```

This collects the tests under `research_desk/` only (each part keeps its tests in its own
`tests/` folder) and includes the architecture check. The tests mock every DB, AI, Telegram,
MongoDB and KIS call and do not read your `.env`. The commit checks run the same suite.

## Troubleshooting

- **"Missing required env var: X"** (`collect`) or **"X is required"** (`tag`, exit `4`) — Add the key to `.env`.
- **"종목표 파일 내용이 버전 정보와 다릅니다…"** (`tag run` / `tag escalate`, exit `4`) — The stock list changed without a new version; see [Stock list update](#stock-list-update).
- **SMS code prompt every run** — The `sessions/samstudy.session` file is missing or got deleted. Telethon re-authenticates each time.
- **Persistent failures in `failed_attempts`** — Check the `error_message` and `attempt_count` columns. If `attempt_count > 10` for a row, the message is likely permanently broken; manually inspect or DELETE the row to stop retrying.

## Security

- `.env` and `*.session` files contain credentials and account access. They are in `.gitignore` — keep it that way.
- `SUPABASE_SERVICE_KEY` bypasses Row-Level Security. Treat it as a master password.
