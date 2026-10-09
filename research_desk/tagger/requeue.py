"""``tag requeue``: send classified rows that meet chosen criteria back to ``pending`` (docs/contracts.md, ``tag requeue``).

The usual backfill (``scripts/run-batches.ps1``: 2 AI calls at once, batches of 10) then
classifies them again under today's rules. Nothing here calls an AI.

Criteria (one or more; each is an option of the command and a key of its report):

- ``unreadable``: ``review_needed``, note starting ``first_page_unreadable``, and page 1 of the PDF
  renders to an image now (rows whose page does not render are counted in ``skipped``);
- ``publisher_not_in_dictionary``: publisher set but not a canonical name of the dictionary;
- ``publisher_filename_mismatch``: the filename tag points to a publisher and the stored publisher
  differs (null included); not when the note has ``publisher_suspect`` (already checked under
  today's rules) or starts with ``first_page_unreadable`` / ``llm_refusal`` (no AI result);
- ``publisher_type_mismatch``: canonical publisher whose stored type is not the dictionary's;
- ``krx_unmatched``: ``review_needed``, note starting ``krx_unmatched_in_scope``.

Only ``auto`` / ``review_needed`` rows are picked; ``verified``, ``processing`` and ``pending``
never are. A row meeting several criteria counts under each and is reverted once.

Preview (default) reads and reports; it writes nothing. ``--apply``:

1. nothing that writes reports runs on this PC (backfill, escalate, collect, web app), checked
   from the process list (``list_processes``); a running job stops it with exit 1
   (``RequeueJobsRunning``), a list that cannot be read is "not ready" (4);
2. the targets are picked again and their current values written to a new CSV in ``BACKUP_DIR``
   before anything changes;
3. one transaction locks the rows, re-checks each (still ``auto`` / ``review_needed``, still meets
   a chosen criterion — the first-page result from before the transaction — and unchanged since
   the backup, so the backup holds what is overwritten), reverts those to
   ``domain.reports.pending_reset_shape()`` (the review's retag shape) and compares the reverted
   ids with the re-checked ids; any difference rolls everything back.

The rows are read and written through ``core.db`` (asyncpg), never Supabase REST: all classified
rows are read in one query, which REST would cut at 1000 rows. Comparison data stays out: the
saved comparisons belong to the analysis feature, and the tagger does not read its table.
"""
from __future__ import annotations

import itertools
import json
import os
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from research_desk.core import llm, pdf, settings
from research_desk.domain.reports import pending_reset_shape
from research_desk.tagger import sql, vocabulary
from research_desk.tagger.vocabulary import PublisherDictionary, PublisherDictionaryError, filename_tag

# The criteria in report order; each is also the argparse dest of its option.
CRITERIA = ("unreadable", "publisher_not_in_dictionary", "publisher_filename_mismatch",
            "publisher_type_mismatch", "krx_unmatched")

REQUEUE_STATUSES = ("auto", "review_needed")
CLASSIFIED_STATUSES = ("auto", "review_needed", "verified")
NULL_PUBLISHER = "(null)"

# Note names the criteria look at (notes are ``<name>[:<detail>]`` joined by ``;``).
UNREADABLE_NOTE = "first_page_unreadable"
REFUSAL_NOTE = "llm_refusal"
KRX_UNMATCHED_NOTE = "krx_unmatched_in_scope"
SUSPECT_NOTE = "publisher_suspect"

# Backups go to <repository>/backups/requeue/ (ignored by git: /backups/ in .gitignore), wherever
# the command is started from.
BACKUP_DIR = Path(__file__).resolve().parents[2] / "backups" / "requeue"

PROCESS_LIST_TIMEOUT_S = 60

# stderr texts (one line each).
NO_CRITERION = ("대상 조건을 하나 이상 고르세요: --unreadable, --publisher-not-in-dictionary, "
                "--publisher-filename-mismatch, --publisher-type-mismatch, --krx-unmatched")
DICTIONARY_UNREADABLE = "발행처 사전을 읽을 수 없습니다: {reason}"
MODEL_TAKES_NO_IMAGES = ("지금 분류 모델 {model}은(는) 그림을 받지 못해 --unreadable 행을 되돌려도 다시 "
                         "못 읽음이 됩니다. LLM_MODEL_DEFAULT를 그림을 받는 모델로 바꾼 뒤 다시 실행하세요.")
JOBS_RUNNING = "실행 중인 작업이 있어 아무것도 바꾸지 않았습니다. 다음을 끈 뒤 다시 실행하세요: {jobs}"
PROCESS_LIST_UNREADABLE = ("프로세스 목록을 읽을 수 없어 실행 중인 작업을 확인하지 못했습니다. "
                           "아무것도 바꾸지 않았습니다: {reason}")
BACKUP_FAILED = "백업 파일을 쓰지 못해 아무것도 바꾸지 않았습니다: {reason}"
COUNT_MISMATCH = ("되돌린 행 수({reverted})가 다시 확인한 행 수({expected})와 달라 모두 취소했습니다. "
                  "아무것도 바뀌지 않았습니다.")
FAILED = "tag requeue를 마치지 못했습니다: {reason}"

# Jobs that must not run during --apply, in the order the guidance names them.
JOB_LABELS = {
    "backfill": "백필(run-batches.ps1 또는 research_desk tag run)",
    "escalate": "재처리(research_desk tag escalate)",
    "collect": "수집(research_desk collect)",
    "web": "웹앱(research_desk web)",
}


class RequeueNotReady(Exception):
    """A check before any DB access failed; ``str()`` is the stderr line (exit code 4)."""


class RequeueFailed(Exception):
    """Stopped with nothing changed; ``str()`` is the stderr line (exit code 1)."""


class RequeueJobsRunning(RequeueFailed):
    """``--apply`` while a backfill / escalate / collect / web app runs: nothing changed, exit 1.

    Like ``peers build`` while tagging, this clears once the job ends or is stopped, so it is
    1 and not 4 (4 is for problems that stay until a setting or file is fixed).
    """


class ProcessListError(RuntimeError):
    """The list of running processes could not be read."""


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def failure_line(exc: BaseException) -> str:
    """The one stderr line for an error that ends the command with exit code 1."""
    if isinstance(exc, RequeueFailed):
        return _one_line(str(exc))
    return _one_line(FAILED.format(reason=f"{type(exc).__name__}: {exc}"))


# ── checks before the DB ─────────────────────────────────────────────────────

def load_publisher_dictionary() -> PublisherDictionary:
    """The publisher dictionary file, read now (``RequeueNotReady`` when it cannot be used)."""
    try:
        return vocabulary.load_dictionary(vocabulary.PUBLISHERS_PATH)
    except PublisherDictionaryError as exc:
        raise RequeueNotReady(DICTIONARY_UNREADABLE.format(reason=_one_line(str(exc)))) from exc


_PROCESS_QUERY = (
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
    "Get-CimInstance -ClassName Win32_Process | "
    "Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress"
)


def list_processes() -> list[tuple[int, Optional[str]]]:
    """(process id, command line or None) of every process on this PC.

    Asks Windows PowerShell (``Get-CimInstance Win32_Process``); no extra package. Raises
    ``ProcessListError`` when PowerShell cannot run, fails, times out or prints no usable list.
    """
    try:
        done = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _PROCESS_QUERY],
            capture_output=True, timeout=PROCESS_LIST_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProcessListError(f"PowerShell을 실행하지 못했습니다 ({type(exc).__name__}: {exc})") from exc
    if done.returncode != 0:
        detail = _one_line((done.stderr or b"").decode("utf-8", errors="replace"))
        raise ProcessListError(f"PowerShell 종료 코드 {done.returncode}"
                               + (f" ({detail})" if detail else ""))
    try:
        listed = json.loads((done.stdout or b"").decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise ProcessListError(f"PowerShell 출력을 읽지 못했습니다 ({exc})") from exc
    if isinstance(listed, dict):   # ConvertTo-Json prints a lone object for a single process
        listed = [listed]
    if not isinstance(listed, list):
        raise ProcessListError("PowerShell 출력이 프로세스 목록이 아닙니다")
    processes: list[tuple[int, Optional[str]]] = []
    for item in listed:
        pid = item.get("ProcessId") if isinstance(item, dict) else None
        if not isinstance(pid, int) or isinstance(pid, bool):
            raise ProcessListError("프로세스 번호가 없는 항목이 있습니다")
        command_line = item.get("CommandLine")
        processes.append((pid, command_line if isinstance(command_line, str) else None))
    return processes


def _job_of(command_line: Optional[str]) -> Optional[str]:
    """Which JOB_LABELS job a command line runs, if any."""
    if not command_line:
        return None
    text = command_line.lower()
    if "run-batches.ps1" in text:
        return "backfill"
    words = text.replace('"', " ").replace("'", " ").split()
    for index, word in enumerate(words):
        if word not in ("research_desk", "-mresearch_desk"):
            continue
        command = words[index + 1:index + 3]
        if command == ["tag", "run"]:
            return "backfill"
        if command == ["tag", "escalate"]:
            return "escalate"
        if command[:1] == ["collect"]:
            return "collect"
        if command[:1] == ["web"]:
            return "web"
    return None


def running_jobs(processes: Iterable[tuple[int, Optional[str]]], own_pid: int) -> dict[str, list[int]]:
    """Job → process ids of the listed processes running one, this process left out."""
    found: dict[str, list[int]] = {}
    for pid, command_line in processes:
        if pid == own_pid:
            continue
        job = _job_of(command_line)
        if job is not None:
            found.setdefault(job, []).append(pid)
    return {job: sorted(found[job]) for job in JOB_LABELS if job in found}


def check_nothing_running(processes: Sequence[tuple[int, Optional[str]]], own_pid: int) -> None:
    """``RequeueJobsRunning`` naming every running job; ``RequeueNotReady`` when the list cannot be trusted."""
    if not any(pid == own_pid for pid, _ in processes):
        # This process is running, so a list without it is not the full list.
        raise RequeueNotReady(PROCESS_LIST_UNREADABLE.format(reason="이 프로세스가 목록에 없습니다"))
    jobs = running_jobs(processes, own_pid)
    if jobs:
        named = " / ".join(f"{JOB_LABELS[job]} PID {', '.join(str(pid) for pid in pids)}"
                           for job, pids in jobs.items())
        raise RequeueJobsRunning(JOBS_RUNNING.format(jobs=named))


def prepare(criteria: Sequence[str], *, apply: bool, model: str) -> PublisherDictionary:
    """The checks after SUPABASE_DB_URL and before the DB; the publisher dictionary to use.

    The dictionary must load; with ``unreadable`` the tagging model ``model`` must take images
    (otherwise the rows would come back unreadable); with ``apply`` no job may be running.
    """
    dictionary = load_publisher_dictionary()
    if "unreadable" in criteria and not llm.supports_images(model):
        raise RequeueNotReady(MODEL_TAKES_NO_IMAGES.format(model=model))
    if apply:
        try:
            processes = list_processes()
        except ProcessListError as exc:
            raise RequeueNotReady(PROCESS_LIST_UNREADABLE.format(reason=_one_line(str(exc)))) from exc
        check_nothing_running(processes, os.getpid())
    return dictionary


# ── the criteria ─────────────────────────────────────────────────────────────

def first_page_renders(file_path: Optional[str]) -> bool:
    """Whether the tagger could show page 1 of the PDF at ``file_path`` (under STORAGE_BASE_DIR) to the AI now.

    Uses the tagger's own picture rule (``extract_pdf.render_page_images``: drawn at the
    tagger's dpi, smaller when over the AI limits), so a row picked here is one the
    tagger will really read from a picture. Any failure — no path, outside the storage
    folder, missing, broken, no page, too big at every tried dpi — is False.
    """
    if not file_path:
        return False
    from research_desk.tagger.nodes import extract_pdf
    try:
        path = pdf.resolve_in_storage(settings.storage_base_dir(), file_path)
    except Exception:
        return False
    return bool(extract_pdf.render_page_images(path))


def _notes(row: Mapping[str, Any]) -> str:
    return row.get("tagging_notes") or ""


def _unreadable_note(row: Mapping[str, Any]) -> bool:
    return row["tagging_status"] == "review_needed" and _notes(row).startswith(UNREADABLE_NOTE)


def _unreadable(row, dictionary: PublisherDictionary, renders: Mapping[int, bool]) -> bool:
    return _unreadable_note(row) and bool(renders.get(row["id"], False))


def _publisher_not_in_dictionary(row, dictionary: PublisherDictionary, renders) -> bool:
    return row["publisher"] is not None and row["publisher"] not in dictionary.types


def _publisher_filename_mismatch(row, dictionary: PublisherDictionary, renders) -> bool:
    notes = _notes(row)
    if SUSPECT_NOTE in notes or notes.startswith((UNREADABLE_NOTE, REFUSAL_NOTE)):
        return False
    pointed = dictionary.filename_publisher(row["file_name"])
    return pointed is not None and row["publisher"] != pointed


def _publisher_type_mismatch(row, dictionary: PublisherDictionary, renders) -> bool:
    publisher = row["publisher"]
    return publisher in dictionary.types and row["publisher_type"] != dictionary.types[publisher]


def _krx_unmatched(row, dictionary: PublisherDictionary, renders) -> bool:
    return row["tagging_status"] == "review_needed" and _notes(row).startswith(KRX_UNMATCHED_NOTE)


_RULES = {
    "unreadable": _unreadable,
    "publisher_not_in_dictionary": _publisher_not_in_dictionary,
    "publisher_filename_mismatch": _publisher_filename_mismatch,
    "publisher_type_mismatch": _publisher_type_mismatch,
    "krx_unmatched": _krx_unmatched,
}


def met_criteria(row: Mapping[str, Any], criteria: Sequence[str], dictionary: PublisherDictionary,
                 renders: Mapping[int, bool]) -> tuple[str, ...]:
    """The chosen criteria ``row`` meets, in CRITERIA order; () unless auto / review_needed.

    ``renders``: row id → whether its first page renders (rows not in it do not).
    """
    if row["tagging_status"] not in REQUEUE_STATUSES:
        return ()
    return tuple(name for name in CRITERIA
                 if name in criteria and _RULES[name](row, dictionary, renders))


@dataclass(frozen=True)
class Selection:
    counts: dict[str, int]                  # chosen criterion → rows meeting it
    rows: list[Mapping[str, Any]]           # rows meeting one or more, each once, by id
    no_page: int                            # unreadable-note rows whose first page does not render
    renders: Mapping[int, bool] = field(default_factory=dict)   # first-page results by row id

    @property
    def ids(self) -> list[int]:
        return [row["id"] for row in self.rows]


def select(rows: Iterable[Mapping[str, Any]], criteria: Sequence[str],
           dictionary: PublisherDictionary) -> Selection:
    """Pick the rows meeting the chosen criteria; first pages are rendered only for unreadable notes."""
    rows = list(rows)
    renders: dict[int, bool] = {}
    if "unreadable" in criteria:
        for row in rows:
            if _unreadable_note(row):
                renders[row["id"]] = first_page_renders(row["file_path"])
    counts = {name: 0 for name in CRITERIA if name in criteria}
    picked = []
    for row in rows:
        met = met_criteria(row, criteria, dictionary, renders)
        for name in met:
            counts[name] += 1
        if met:
            picked.append(row)
    picked.sort(key=lambda row: row["id"])
    no_page = sum(1 for renders_now in renders.values() if not renders_now)
    return Selection(counts=counts, rows=picked, no_page=no_page, renders=renders)


def _by_count(counter: Counter) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def publisher_values(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Rows per stored publisher value (``(null)`` for none), most first."""
    return _by_count(Counter(NULL_PUBLISHER if row["publisher"] is None else row["publisher"]
                             for row in rows))


def unknown_filename_tags(rows: Iterable[Mapping[str, Any]],
                          dictionary: PublisherDictionary) -> dict[str, int]:
    """Classified rows (auto / review_needed / verified) per filename tag the dictionary lacks."""
    tags: Counter = Counter()
    for row in rows:
        if row["tagging_status"] not in CLASSIFIED_STATUSES:
            continue
        tag = filename_tag(row["file_name"])
        if tag is not None and dictionary.canonical_for(tag) is None:
            tags[tag] += 1
    return _by_count(tags)


# ── backup ───────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now()


def _csv_field(value: Any) -> str:
    """NULL → an empty unquoted field; any value → quoted (so an empty string stays ``""``)."""
    if value is None:
        return ""
    return '"' + str(value).replace('"', '""') + '"'


def _csv_line(values: Iterable[Any]) -> str:
    return ",".join(_csv_field(value) for value in values) + "\r\n"


def write_backup(rows: Sequence[Mapping[str, Any]], folder: Path) -> Path:
    """Write ``rows`` (``sql.REQUEUE_BACKUP_COLUMNS``) to a new CSV in ``folder``; its path.

    UTF-8 without BOM, a header line, Postgres text values as read (array columns as Postgres
    array literals). Postgres ``COPY ... CSV HEADER`` reads it back with NULLs intact. The name
    carries the local time (``requeue-YYYYMMDD-HHMMSS.csv``, then ``-1``, ``-2`` … in the same
    second); an existing file is never overwritten. A file that fails half-way is removed.
    """
    folder.mkdir(parents=True, exist_ok=True)
    stamp = _now().strftime("%Y%m%d-%H%M%S")
    for attempt in itertools.count():
        path = folder / (f"requeue-{stamp}.csv" if attempt == 0 else f"requeue-{stamp}-{attempt}.csv")
        try:
            handle = path.open("x", encoding="utf-8", newline="")
        except FileExistsError:
            continue
        try:
            with handle:
                handle.write(_csv_line(sql.REQUEUE_BACKUP_COLUMNS))
                for row in rows:
                    handle.write(_csv_line(row[column] for column in sql.REQUEUE_BACKUP_COLUMNS))
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path
    raise AssertionError("unreachable")


# ── the command ──────────────────────────────────────────────────────────────

async def run(sb, criteria: Sequence[str], *, dictionary: PublisherDictionary, apply: bool) -> dict:
    """Preview or apply on ``sb`` (``core.db.SupabaseSQL``); the report to print.

    Raises ``RequeueFailed`` (nothing changed) when the backup cannot be written or the reverted
    rows differ from the re-checked rows; any other error propagates.
    """
    rows = await sb.fetch(sql.REQUEUE_CANDIDATES_SQL)
    selection = select(rows, criteria, dictionary)
    report: dict[str, Any] = {
        "mode": "apply" if apply else "preview",
        "selected": dict(selection.counts),
        "skipped": {"unreadable_no_page": selection.no_page} if "unreadable" in criteria else {},
        "total": len(selection.rows),
    }
    if not apply:
        report["publisher_values"] = publisher_values(selection.rows)
        report["unknown_filename_tags"] = unknown_filename_tags(rows, dictionary)
        return report
    if not selection.rows:
        report.update(requeued=0, backup_file=None)
        return report

    backup_rows = await sb.fetch(sql.REQUEUE_BACKUP_SQL, [selection.ids])
    try:
        backup_file = write_backup(backup_rows, BACKUP_DIR)
    except (OSError, UnicodeError) as exc:
        raise RequeueFailed(BACKUP_FAILED.format(reason=_one_line(str(exc)))) from exc
    requeued = await _revert(sb, backup_rows, criteria, dictionary, selection.renders)
    report.update(requeued=requeued, backup_file=str(backup_file))
    return report


async def _revert(sb, backup_rows: Sequence[Mapping[str, Any]], criteria: Sequence[str],
                  dictionary: PublisherDictionary, renders: Mapping[int, bool]) -> int:
    """Lock → re-check → reset → compare, in one transaction; the number of rows reverted."""
    backed_up = {row["id"]: row for row in backup_rows}
    shape = pending_reset_shape()
    values = [shape[column] for column in sql.REQUEUE_RESET_COLUMNS]
    async with sb.transaction() as tx:
        locked = await tx.fetch(sql.REQUEUE_LOCK_SQL, [list(backed_up)])
        # Unchanged since the backup (so the backup holds what is overwritten) and still a match.
        keep = [row["id"] for row in locked
                if row == backed_up.get(row["id"])
                and met_criteria(row, criteria, dictionary, renders)]
        if not keep:
            return 0
        reverted = await tx.fetch(sql.REQUEUE_RESET_SQL, [keep, *values])
        reverted_ids = sorted(row["id"] for row in reverted)
        if reverted_ids != sorted(keep):   # raising here rolls the whole transaction back
            raise RequeueFailed(COUNT_MISMATCH.format(reverted=len(reverted_ids), expected=len(keep)))
    return len(keep)
