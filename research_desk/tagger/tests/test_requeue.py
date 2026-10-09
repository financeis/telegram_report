"""``tag requeue`` (spec §3.6): send classified rows that match chosen criteria back to ``pending``.

What is checked:
- arguments: the five criteria, ``--apply``, no criterion → usage error 2 before anything runs;
- not ready (exit 4, nothing read or written): no SUPABASE_DB_URL, a publisher dictionary that
  cannot be read, ``--unreadable`` with a tagging model that takes no images, and (``--apply``
  only) a running backfill / escalate / collect / web app or a process list that cannot be read;
- selection per criterion and every exclusion (verified / processing / pending never; suspect-noted,
  unreadable and refused rows never for the filename mismatch; rows whose first page cannot be
  rendered are counted in ``skipped``), several criteria on one row counted per criterion but
  reverted once, the unknown filename tags of every classified row;
- preview writes nothing; apply writes the backup CSV first, then locks, re-checks, reverts with
  exactly ``domain.reports.pending_reset_shape()`` and checks the count in one transaction; a backup
  that cannot be written or a count that does not match changes nothing (exit 1, empty stdout);
- the process check and the first-page check behind their one function each.

No test reaches a DB, PowerShell or the real backup folder: ``SupabaseSQL.from_env`` returns the
in-memory ``FakeReports`` below, ``requeue.list_processes`` and ``requeue.first_page_renders`` are
replaced (their own tests replace ``subprocess.run`` / use temp PDFs), ``requeue.BACKUP_DIR`` is a
temp folder, and the publisher dictionary is a small temp file.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import subprocess
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest

import research_desk
from research_desk.core import db
from research_desk.domain.reports import pending_reset_shape
from research_desk.tagger import requeue, sql, vocabulary
from research_desk.tagger.cli import register

OWN_PID = os.getpid()
ALL_CRITERIA = ["--unreadable", "--publisher-not-in-dictionary", "--publisher-filename-mismatch",
                "--publisher-type-mismatch", "--krx-unmatched"]

DICTIONARY = textwrap.dedent("""\
    broker:
      - canonical: 메리츠증권
        aliases: ["메리츠", "MERITZ"]
      - canonical: 하나증권
        aliases: ["Hana"]
      - canonical: DB증권
        aliases: ["DB금융투자", "DB"]
    data_provider:
      - canonical: 한국IR협의회
        aliases: ["KIRS"]
    other:
      - canonical: 해당기업
""")

ARRAY_COLUMNS = tuple(column for column, value in pending_reset_shape().items() if value == [])
CANDIDATE_KEYS = ("id", "file_name", "file_path", "publisher", "publisher_type", "tagging_status",
                  "tagging_notes")


def make_row(row_id: int, *, tag: str = "MERITZ", **values) -> dict:
    """A classified row as the requeue SQL reads it (every column but ``id`` as Postgres text)."""
    row = {column: None for column in sql.REQUEUE_BACKUP_COLUMNS}
    row.update({column: "{}" for column in ARRAY_COLUMNS})
    row.update(
        id=row_id,
        file_name=f"기업분석_20260511_{tag}_{1000 + row_id}.pdf",
        file_path=f"2026/05/{row_id}.pdf",
        tagging_status="auto",
        tagged_at="2026-05-08 09:00:00.123+00",
        tagging_confidence="high",
        tagger_version=sql.TAGGER_VERSION,
        taxonomy_version="KRX@2026-05-08",
        published_at="2026-05-08",
        report_type="단일종목",
        publisher="메리츠증권",
        publisher_type="broker",
        analysts='{홍길동,"김 철수"}',
        title='삼성전자 "실적" 리뷰',
        stock_codes="{005930}",
        company_names="{삼성전자}",
    )
    row.update(values)
    return row


# ── fakes ────────────────────────────────────────────────────────────────────

class FakeReports:
    """The ``reports`` table as the requeue SQL sees it. Every statement lands in ``events``.

    Writes inside a transaction are kept aside and applied on commit only, like Postgres.
    ``before_lock(rows)`` runs just before the lock (another session changing rows);
    ``reset_result(ids)`` replaces the ids the reset reports back (a count mismatch).
    """

    def __init__(self, rows=()):
        self.rows = {row["id"]: dict(row) for row in rows}
        self.events = []
        self.before_lock = None
        self.reset_result = None
        self.fail_fetch = None

    def _backup_row(self, row_id):
        return {column: self.rows[row_id][column] for column in sql.REQUEUE_BACKUP_COLUMNS}

    async def fetch(self, sql_text, args=()):
        self.events.append(("fetch", sql_text, list(args)))
        if self.fail_fetch is not None:
            raise self.fail_fetch
        if sql_text == sql.REQUEUE_CANDIDATES_SQL:
            return [{key: row[key] for key in CANDIDATE_KEYS} for row in self.rows.values()
                    if row["tagging_status"] in ("auto", "review_needed", "verified")]
        if sql_text == sql.REQUEUE_BACKUP_SQL:
            [ids] = args
            return [self._backup_row(row_id) for row_id in sorted(ids) if row_id in self.rows]
        raise AssertionError(f"unexpected fetch outside the transaction: {sql_text}")

    async def execute(self, sql_text, args=()):
        self.events.append(("execute", sql_text, list(args)))
        raise AssertionError("tag requeue never writes outside its transaction")

    def transaction(self):
        return FakeTransaction(self)

    async def close(self):
        self.events.append(("close",))

    # what happened
    def statements(self):
        return [event[0] if event[0] != "fetch" else NAMES.get(event[1], event[1])
                for event in self.events]

    def wrote(self):
        return any(event[0] in ("begin", "execute", "commit") for event in self.events)


class FakeTransaction:
    def __init__(self, table: FakeReports):
        self.table = table
        self.changes = {}

    async def __aenter__(self):
        self.table.events.append(("begin",))
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.table.events.append(("commit",))
            for row_id, values in self.changes.items():
                self.table.rows[row_id].update(values)
        else:
            self.table.events.append(("rollback",))
        return False

    async def fetch(self, sql_text, args=()):
        self.table.events.append(("fetch", sql_text, list(args)))
        if sql_text == sql.REQUEUE_LOCK_SQL:
            if self.table.before_lock is not None:
                self.table.before_lock(self.table.rows)
            [ids] = args
            return [self.table._backup_row(row_id) for row_id in sorted(ids)
                    if row_id in self.table.rows]
        if sql_text == sql.REQUEUE_RESET_SQL:
            ids, *values = args
            shape = dict(zip(sql.REQUEUE_RESET_COLUMNS, values))
            hit = [row_id for row_id in ids if row_id in self.table.rows
                   and self.table.rows[row_id]["tagging_status"] in ("auto", "review_needed")]
            for row_id in hit:
                self.changes[row_id] = dict(shape)
            if self.table.reset_result is not None:
                hit = self.table.reset_result(hit)
            return [{"id": row_id} for row_id in hit]
        raise AssertionError(f"unexpected fetch in the transaction: {sql_text}")

    async def execute(self, sql_text, args=()):
        self.table.events.append(("execute", sql_text, list(args)))
        raise AssertionError("tag requeue reverts with RETURNING (fetch), not execute")


NAMES = {
    sql.REQUEUE_CANDIDATES_SQL: "candidates",
    sql.REQUEUE_BACKUP_SQL: "backup-select",
    sql.REQUEUE_LOCK_SQL: "lock",
    sql.REQUEUE_RESET_SQL: "reset",
}


@pytest.fixture
def table(monkeypatch):
    """The fake ``reports`` table behind ``SupabaseSQL.from_env``; no real pool can open."""
    fake = FakeReports()

    async def from_env(*, max_size):
        fake.events.append(("from_env", max_size))
        return fake

    async def no_pool(*args, **kwargs):
        raise AssertionError("tests never open a real DB pool")

    monkeypatch.setattr(db.SupabaseSQL, "from_env", staticmethod(from_env))
    monkeypatch.setattr(db, "create_pool", no_pool)
    monkeypatch.setattr(db.asyncpg, "create_pool", no_pool)
    return fake


@pytest.fixture
def dictionary_file(tmp_path, monkeypatch):
    path = tmp_path / "publishers.yaml"
    path.write_text(DICTIONARY, encoding="utf-8")
    monkeypatch.setattr(vocabulary, "PUBLISHERS_PATH", path)
    return path


@pytest.fixture
def processes(monkeypatch):
    """What ``list_processes`` returns: this process only, unless a test adds or replaces."""
    state = SimpleNamespace(listed=[(OWN_PID, "python -m research_desk tag requeue --apply")],
                            error=None, calls=0)

    def fake_list():
        state.calls += 1
        if state.error is not None:
            raise state.error
        return list(state.listed)

    monkeypatch.setattr(requeue, "list_processes", fake_list)
    return state


@pytest.fixture
def pages(monkeypatch):
    """First pages that render: every path except those in ``broken``; ``checked`` records calls."""
    state = SimpleNamespace(broken=set(), checked=[])

    def fake_renders(file_path):
        state.checked.append(file_path)
        return file_path not in state.broken

    monkeypatch.setattr(requeue, "first_page_renders", fake_renders)
    return state


@pytest.fixture
def backups(tmp_path, monkeypatch):
    folder = tmp_path / "backups" / "requeue"
    monkeypatch.setattr(requeue, "BACKUP_DIR", folder)
    return folder


@pytest.fixture
def env(tagger_env, table, dictionary_file, processes, pages, backups):
    """Ready to requeue: DB URL set, default tagging model (takes images), fakes in place."""
    tagger_env.setenv("SUPABASE_DB_URL", "postgresql://tester@127.0.0.1:1/never")
    return tagger_env


def main(argv: list[str]) -> int:
    """What ``python -m research_desk`` does with the ``tag`` command."""
    parser = argparse.ArgumentParser(prog="research_desk")
    register(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(argv)
    return args.func(args)


def requeue_cmd(capsys, *options):
    """Run ``tag requeue <options>``; (exit code, stdout, stderr)."""
    code = main(["tag", "requeue", *options])
    out, err = capsys.readouterr()
    return code, out, err


def report(out: str) -> dict:
    data = json.loads(out)
    assert out == json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    return data


def one_line(err: str) -> str:
    assert err.endswith("\n") and err.count("\n") == 1, err
    return err.strip()


# ── arguments ────────────────────────────────────────────────────────────────

def test_requeue_is_a_tag_subcommand_with_five_criteria_and_apply(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["tag", "--help"])
    assert excinfo.value.code == 0
    assert "requeue" in capsys.readouterr().out
    with pytest.raises(SystemExit) as excinfo:
        main(["tag", "requeue", "--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("usage: research_desk tag requeue")
    for option in [*ALL_CRITERIA, "--apply"]:
        assert option in out


@pytest.mark.parametrize("argv", [[], ["--apply"]], ids=["preview", "apply"])
def test_no_criterion_is_a_usage_error_before_anything_runs(env, table, processes, capsys, argv):
    with pytest.raises(SystemExit) as excinfo:
        main(["tag", "requeue", *argv])
    assert excinfo.value.code == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert err.startswith("usage: research_desk tag requeue")
    assert requeue.NO_CRITERION in err
    assert table.events == []
    assert processes.calls == 0


def test_an_unknown_option_is_a_usage_error(env, table, capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["tag", "requeue", "--unreadable", "--everything"])
    assert excinfo.value.code == 2
    assert table.events == []


# ── not ready: exit 4, nothing read or written ───────────────────────────────

@pytest.mark.parametrize("apply", [False, True], ids=["preview", "apply"])
@pytest.mark.parametrize("value", [None, ""])
def test_without_a_db_url_it_exits_4(env, table, processes, backups, capsys, apply, value):
    if value is None:
        env.delenv("SUPABASE_DB_URL")
    else:
        env.setenv("SUPABASE_DB_URL", value)
    code, out, err = requeue_cmd(capsys, "--krx-unmatched", *(["--apply"] if apply else []))
    assert (code, out) == (4, "")
    assert one_line(err) == "SUPABASE_DB_URL is required"
    assert table.events == []
    assert processes.calls == 0
    assert not backups.exists()


@pytest.mark.parametrize("apply", [False, True], ids=["preview", "apply"])
@pytest.mark.parametrize("broken", ["missing", "not yaml", "unknown section"])
def test_an_unreadable_publisher_dictionary_exits_4(env, table, dictionary_file, capsys, apply, broken):
    if broken == "missing":
        dictionary_file.unlink()
    elif broken == "not yaml":
        dictionary_file.write_text("broker: [\n", encoding="utf-8")
    else:
        dictionary_file.write_text("brokers:\n  - canonical: 메리츠증권\n", encoding="utf-8")
    code, out, err = requeue_cmd(capsys, "--publisher-not-in-dictionary",
                                 *(["--apply"] if apply else []))
    assert (code, out) == (4, "")
    assert one_line(err).startswith("발행처 사전을 읽을 수 없습니다: ")
    assert table.events == []


@pytest.mark.parametrize("apply", [False, True], ids=["preview", "apply"])
@pytest.mark.parametrize("name", ["LLM_MODEL_DEFAULT", "OPENAI_MODEL_DEFAULT"])
def test_unreadable_with_a_tagging_model_that_takes_no_images_exits_4(env, table, processes, capsys,
                                                                      apply, name):
    env.setenv(name, "gpt-4")
    code, out, err = requeue_cmd(capsys, "--unreadable", "--krx-unmatched",
                                 *(["--apply"] if apply else []))
    assert (code, out) == (4, "")
    line = one_line(err)
    assert "gpt-4" in line and "--unreadable" in line
    assert table.events == []
    assert processes.calls == 0


def test_the_image_check_is_only_for_unreadable(env, table, capsys):
    env.setenv("LLM_MODEL_DEFAULT", "gpt-4")
    code, out, err = requeue_cmd(capsys, "--krx-unmatched")
    assert (code, err) == (0, "")
    assert report(out)["mode"] == "preview"


@pytest.mark.parametrize("command_line,label", [
    ('"C:\\rd\\.venv\\Scripts\\python.exe" -u -m research_desk tag run --worker-id h-1-ab12',
     "백필(run-batches.ps1 또는 research_desk tag run)"),
    ("powershell -File scripts\\run-batches.ps1 -Iterations 5 -BatchSize 10",
     "백필(run-batches.ps1 또는 research_desk tag run)"),
    ("python -m research_desk tag escalate --since 2026-05-08T09:00",
     "재처리(research_desk tag escalate)"),
    ("python -m research_desk collect", "수집(research_desk collect)"),
    ("python -m research_desk web --view review", "웹앱(research_desk web)"),
])
def test_apply_while_a_job_runs_exits_4_and_changes_nothing(env, table, processes, backups, capsys,
                                                            command_line, label):
    table.rows = {1: make_row(1, tagging_status="review_needed",
                              tagging_notes="krx_unmatched_in_scope:ipo_pending_or_unknown")}
    processes.listed.append((4242, command_line))
    code, out, err = requeue_cmd(capsys, "--krx-unmatched", "--apply")
    assert (code, out) == (4, "")
    line = one_line(err)
    assert label in line and "4242" in line
    assert table.events == []
    assert not backups.exists()
    assert table.rows[1]["tagging_status"] == "review_needed"


def test_apply_when_the_process_list_cannot_be_read_exits_4(env, table, processes, backups, capsys):
    processes.error = requeue.ProcessListError("powershell exit 1")
    code, out, err = requeue_cmd(capsys, "--krx-unmatched", "--apply")
    assert (code, out) == (4, "")
    assert "powershell exit 1" in one_line(err)
    assert table.events == []
    assert not backups.exists()


def test_preview_does_not_look_at_running_processes(env, table, processes, capsys):
    processes.error = requeue.ProcessListError("never called")
    processes.listed.append((4242, "python -m research_desk web"))
    code, out, err = requeue_cmd(capsys, "--krx-unmatched")
    assert (code, err) == (0, "")
    assert processes.calls == 0


# ── which rows each criterion picks (the pure rule) ──────────────────────────

UNREADABLE = "first_page_unreadable"
KRX = "krx_unmatched_in_scope:ipo_pending_or_unknown"

# name → (row values, criteria the row meets when every first page renders)
CASES = {
    # --unreadable
    "unreadable": (dict(tagging_status="review_needed", tagging_notes=UNREADABLE, publisher=None,
                        publisher_type=None), {"unreadable"}),
    "unreadable with page_image note": (dict(tagging_status="review_needed",
                                             tagging_notes=UNREADABLE + ";page_image",
                                             publisher=None, publisher_type=None),
                                        {"unreadable"}),
    "unreadable note on an auto row": (dict(tagging_notes=UNREADABLE), set()),
    "refused": (dict(tagging_status="review_needed", tagging_notes="llm_refusal:policy",
                     publisher=None, publisher_type=None), set()),
    "refused after an image": (dict(tagging_status="review_needed",
                                    tagging_notes="llm_refusal:;page_image",
                                    publisher=None, publisher_type=None), set()),
    # --publisher-not-in-dictionary
    "alias stored": (dict(publisher="MERITZ"), {"publisher_not_in_dictionary",
                                                "publisher_filename_mismatch"}),
    "old name stored, no tag": (dict(publisher="DB금융투자", publisher_type="broker", tag="NEWCO"),
                                {"publisher_not_in_dictionary"}),
    "free text stored": (dict(publisher="리서치센터", tag="Hana"),
                         {"publisher_not_in_dictionary", "publisher_filename_mismatch"}),
    # --publisher-filename-mismatch
    "matches the tag": (dict(), set()),
    "other canonical than the tag": (dict(publisher="하나증권"), {"publisher_filename_mismatch"}),
    "null publisher, known tag": (dict(publisher=None, publisher_type=None),
                                  {"publisher_filename_mismatch"}),
    "IR material on a broker file": (dict(report_type="IR자료", publisher="해당기업",
                                          publisher_type="other"),
                                     {"publisher_filename_mismatch"}),
    "unknown tag, null publisher": (dict(publisher=None, publisher_type=None, tag="NEWCO"), set()),
    "no tag": (dict(publisher=None, publisher_type=None, file_name="리포트.pdf"), set()),
    "already suspect": (dict(publisher="하나증권",
                             tagging_notes="publisher_suspect:filename_mismatch"), set()),
    "already suspect after another note": (dict(publisher=None, publisher_type=None,
                                                tagging_status="review_needed",
                                                tagging_notes=KRX + ";publisher_suspect:filename_mismatch"),
                                           {"krx_unmatched"}),
    "mismatch but unreadable": (dict(publisher=None, publisher_type=None,
                                     tagging_status="review_needed", tagging_notes=UNREADABLE),
                                {"unreadable"}),
    "mismatch but refused": (dict(publisher=None, publisher_type=None,
                                  tagging_status="review_needed", tagging_notes="llm_refusal:x"),
                             set()),
    # --publisher-type-mismatch
    "wrong type": (dict(publisher="한국IR협의회", publisher_type="ir_agency", tag="KIRS"),
                   {"publisher_type_mismatch"}),
    "missing type": (dict(publisher_type=None), {"publisher_type_mismatch"}),
    "type of an alias is not checked": (dict(publisher="메리츠", publisher_type="other", tag="NEWCO"),
                                        {"publisher_not_in_dictionary"}),
    # --krx-unmatched
    "krx unmatched": (dict(tagging_status="review_needed", tagging_notes=KRX), {"krx_unmatched"}),
    "krx note on an auto row": (dict(tagging_notes=KRX), set()),
    "other review reason": (dict(tagging_status="review_needed", tagging_notes="type_indeterminate"),
                            set()),
}


@pytest.fixture
def loaded_dictionary(dictionary_file):
    return vocabulary.load_dictionary(dictionary_file)


@pytest.mark.parametrize("case", list(CASES))
def test_each_criterion_picks_its_rows(loaded_dictionary, case):
    values, expected = CASES[case]
    row = make_row(1, **values)
    met = requeue.met_criteria(row, requeue.CRITERIA, loaded_dictionary, {1: True})
    assert set(met) == expected
    assert list(met) == [name for name in requeue.CRITERIA if name in expected]   # fixed order


@pytest.mark.parametrize("status", ["verified", "processing", "pending"])
@pytest.mark.parametrize("case", [case for case, (_, expected) in CASES.items() if expected])
def test_verified_processing_and_pending_rows_are_never_picked(loaded_dictionary, case, status):
    values, _ = CASES[case]
    row = make_row(1, **{**values, "tagging_status": status})
    assert requeue.met_criteria(row, requeue.CRITERIA, loaded_dictionary, {1: True}) == ()


def test_an_unreadable_row_whose_first_page_does_not_render_is_not_picked(loaded_dictionary):
    values, _ = CASES["unreadable"]
    row = make_row(1, **values)
    assert requeue.met_criteria(row, requeue.CRITERIA, loaded_dictionary, {1: False}) == ()
    assert requeue.met_criteria(row, requeue.CRITERIA, loaded_dictionary, {}) == ()


def test_only_the_chosen_criteria_are_checked(loaded_dictionary):
    row = make_row(1, publisher="MERITZ")
    assert requeue.met_criteria(row, ("publisher_filename_mismatch",), loaded_dictionary, {}) == (
        "publisher_filename_mismatch",)
    assert requeue.met_criteria(row, ("krx_unmatched",), loaded_dictionary, {}) == ()


# ── preview ──────────────────────────────────────────────────────────────────

def preview_rows():
    return [
        make_row(1, tagging_status="review_needed", tagging_notes=UNREADABLE, publisher=None,
                 publisher_type=None),                                           # unreadable
        make_row(2, tagging_status="review_needed", tagging_notes=UNREADABLE, publisher=None,
                 publisher_type=None, file_path="2026/05/broken.pdf"),           # no page
        make_row(3, publisher="DB금융투자", tag="MERITZ"),                       # 2 criteria
        make_row(4, publisher="하나증권"),                                        # mismatch
        make_row(5, publisher_type=None),                                         # type
        make_row(6, tagging_status="review_needed", tagging_notes=KRX, tag="NEWCO"),  # krx
        make_row(7),                                                              # nothing
        make_row(8, tagging_status="verified", publisher="DB금융투자", tag="NEWCO"),  # never
        make_row(9, tagging_status="pending", publisher=None, tag="OTHERCO"),
        make_row(10, tagging_status="processing", publisher=None, tag="OTHERCO"),
        make_row(11, tag="NEWCO", publisher=None, publisher_type=None),          # unknown tag
        make_row(12, file_name="리포트_20260511_+_31780.pdf", publisher=None,
                 publisher_type=None),                                            # not a tag
    ]


def test_preview_prints_the_counts_and_writes_nothing(env, table, pages, backups, capsys):
    table.rows = {row["id"]: row for row in preview_rows()}
    pages.broken = {"2026/05/broken.pdf"}
    before = json.dumps(table.rows, ensure_ascii=False, sort_keys=True)

    code, out, err = requeue_cmd(capsys, *ALL_CRITERIA)

    assert (code, err) == (0, "")
    data = report(out)
    assert data == {
        "mode": "preview",
        "selected": {"unreadable": 1, "publisher_not_in_dictionary": 1,
                     "publisher_filename_mismatch": 2, "publisher_type_mismatch": 1,
                     "krx_unmatched": 1},
        "skipped": {"unreadable_no_page": 1},
        "total": 5,
        "publisher_values": {"(null)": 1, "DB금융투자": 1, "메리츠증권": 2, "하나증권": 1},
        "unknown_filename_tags": {"NEWCO": 3},
    }
    assert list(data) == ["mode", "selected", "skipped", "total", "publisher_values",
                          "unknown_filename_tags"]
    assert list(data["selected"]) == list(requeue.CRITERIA)
    # One read, no transaction, no write, no backup.
    assert table.statements() == ["from_env", "candidates", "close"]
    assert not table.wrote()
    assert json.dumps(table.rows, ensure_ascii=False, sort_keys=True) == before
    assert not backups.exists()
    # The first page is rendered only for rows the --unreadable note picks.
    assert sorted(pages.checked) == ["2026/05/1.pdf", "2026/05/broken.pdf"]


def test_selected_and_skipped_hold_only_the_chosen_criteria(env, table, pages, capsys):
    table.rows = {row["id"]: row for row in preview_rows()}
    code, out, _ = requeue_cmd(capsys, "--publisher-filename-mismatch", "--krx-unmatched")
    data = report(out)
    assert data["selected"] == {"publisher_filename_mismatch": 2, "krx_unmatched": 1}
    assert data["skipped"] == {}
    assert data["total"] == 3
    assert data["publisher_values"] == {"DB금융투자": 1, "메리츠증권": 1, "하나증권": 1}
    # The unknown tags count every classified row, whatever was chosen.
    assert data["unknown_filename_tags"] == {"NEWCO": 3}
    assert pages.checked == []   # no first page rendered without --unreadable


def test_unknown_filename_tags_count_auto_review_needed_and_verified_rows(env, table, capsys):
    table.rows = {row["id"]: row for row in [
        make_row(1, tag="ZETA"), make_row(2, tag="ZETA", tagging_status="verified"),
        make_row(3, tag="ALPHA", tagging_status="review_needed", tagging_notes="type_indeterminate"),
        make_row(4, tag="Hana", publisher="하나증권"),                 # known tag
        make_row(5, tag="ALPHA", tagging_status="pending"),            # not classified
        make_row(6, tag="ALPHA", tagging_status="processing"),
        make_row(7, tag="Mirae+Asset"),                                # unknown in this dictionary
    ]}
    code, out, _ = requeue_cmd(capsys, "--krx-unmatched")
    data = report(out)
    assert data["unknown_filename_tags"] == {"ZETA": 2, "ALPHA": 1, "Mirae+Asset": 1}
    assert data["selected"] == {"krx_unmatched": 0}
    assert (data["total"], data["publisher_values"]) == (0, {})


# ── apply ────────────────────────────────────────────────────────────────────

@pytest.fixture
def backup_order(monkeypatch, table):
    """Records the backup being written in ``table.events`` (to check it comes before any write)."""
    write = requeue.write_backup

    def recording(rows, folder):
        path = write(rows, folder)
        table.events.append(("backup", path))
        return path

    monkeypatch.setattr(requeue, "write_backup", recording)


def test_apply_backs_up_then_reverts_in_one_transaction(env, table, pages, backups, backup_order,
                                                       capsys):
    table.rows = {row["id"]: row for row in preview_rows()}
    pages.broken = {"2026/05/broken.pdf"}
    untouched = {row_id: dict(table.rows[row_id]) for row_id in (2, 7, 8, 9, 10, 11, 12)}

    code, out, err = requeue_cmd(capsys, *ALL_CRITERIA, "--apply")

    assert (code, err) == (0, "")
    data = report(out)
    backup_file = data.pop("backup_file")
    assert data == {
        "mode": "apply",
        "selected": {"unreadable": 1, "publisher_not_in_dictionary": 1,
                     "publisher_filename_mismatch": 2, "publisher_type_mismatch": 1,
                     "krx_unmatched": 1},
        "skipped": {"unreadable_no_page": 1},
        "total": 5,
        "requeued": 5,
    }
    assert list(report(out)) == ["mode", "selected", "skipped", "total", "requeued", "backup_file"]
    # Backup first, then lock → re-check → reset → commit, all in one transaction.
    assert table.statements() == ["from_env", "candidates", "backup-select", "backup", "begin",
                                  "lock", "reset", "commit", "close"]
    [lock] = [event for event in table.events if event[0] == "fetch" and event[1] == sql.REQUEUE_LOCK_SQL]
    assert lock[2] == [[1, 3, 4, 5, 6]]
    [reset] = [event for event in table.events if event[0] == "fetch" and event[1] == sql.REQUEUE_RESET_SQL]
    assert reset[2][0] == [1, 3, 4, 5, 6]   # row 3 meets two criteria: reverted once
    # The reverted rows now have the pending-reset shape; the others are as they were.
    for row_id in (1, 3, 4, 5, 6):
        row = table.rows[row_id]
        assert {column: row[column] for column in sql.REQUEUE_RESET_COLUMNS} == pending_reset_shape()
    for row_id, row in untouched.items():
        assert table.rows[row_id] == row
    # The backup file holds the rows as they were.
    path = backups / os.path.basename(backup_file)
    assert os.path.samefile(backup_file, path)
    assert re.fullmatch(r"requeue-\d{8}-\d{6}\.csv", path.name)
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [int(row["id"]) for row in rows] == [1, 3, 4, 5, 6]
    assert rows[1]["publisher"] == "DB금융투자"


def test_the_reset_is_exactly_the_pending_reset_shape(env, table, capsys):
    table.rows = {1: make_row(1, tagging_status="review_needed", tagging_notes=KRX)}
    code, _, _ = requeue_cmd(capsys, "--krx-unmatched", "--apply")
    assert code == 0
    [reset] = [event for event in table.events if event[0] == "fetch" and event[1] == sql.REQUEUE_RESET_SQL]
    ids, *values = reset[2]
    assert ids == [1]
    assert sql.REQUEUE_RESET_COLUMNS == tuple(pending_reset_shape())
    assert dict(zip(sql.REQUEUE_RESET_COLUMNS, values)) == pending_reset_shape()


def test_the_reset_sql_sets_every_shape_column_and_never_touches_other_statuses():
    text = sql.REQUEUE_RESET_SQL
    for number, column in enumerate(sql.REQUEUE_RESET_COLUMNS, start=2):
        assert re.search(rf"\b{column}=\${number}\b", text), column
    assert set(re.findall(r"\$\d+", text)) == {f"${n}" for n in range(1, len(sql.REQUEUE_RESET_COLUMNS) + 2)}
    assert "id = ANY($1::bigint[])" in text
    assert "tagging_status IN ('auto', 'review_needed')" in text
    assert "RETURNING id" in text
    assert "verified" not in text
    assert "FOR UPDATE" in sql.REQUEUE_LOCK_SQL
    assert "UPDATE" not in sql.REQUEUE_CANDIDATES_SQL.replace("FOR UPDATE", "")
    assert "UPDATE" not in sql.REQUEUE_BACKUP_SQL


def test_the_backup_has_the_reset_columns_in_postgres_text(env, table, backups, capsys):
    table.rows = {1: make_row(1, tagging_status="review_needed", tagging_notes=KRX, title=None,
                              publisher='메리츠"증권', products="{}")}
    code, out, _ = requeue_cmd(capsys, "--krx-unmatched", "--apply")
    assert code == 0
    path = report(out)["backup_file"]
    raw = open(path, "rb").read()
    text = raw.decode("utf-8")
    assert not raw.startswith(b"\xef\xbb\xbf")
    lines = text.splitlines()
    assert lines[0] == ",".join(f'"{column}"' for column in sql.REQUEUE_BACKUP_COLUMNS)
    assert list(sql.REQUEUE_BACKUP_COLUMNS[:4]) == ["id", "file_name", "tagging_status", "tagged_at"]
    assert set(sql.REQUEUE_BACKUP_COLUMNS) == {"id", "file_name", *sql.REQUEUE_RESET_COLUMNS}
    [row] = list(csv.DictReader(io.StringIO(text)))
    assert row["analysts"] == '{홍길동,"김 철수"}'                 # the Postgres array text as is
    assert row["products"] == "{}"
    assert row["tagged_at"] == "2026-05-08 09:00:00.123+00"
    assert row["publisher"] == '메리츠"증권'
    # NULL is an empty unquoted field; a value (even empty) is quoted.
    assert ',"메리츠""증권",' in lines[1]
    columns = list(sql.REQUEUE_BACKUP_COLUMNS)
    assert lines[1].split(",")[columns.index("tagging_locked_at")] == ""


REAL_BACKUP_DIR = requeue.BACKUP_DIR


def test_the_backup_folder_is_in_the_repository_and_ignored_by_git():
    repository = Path(research_desk.__file__).resolve().parent.parent
    assert REAL_BACKUP_DIR == repository / "backups" / "requeue"
    ignored = (repository / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/backups/" in ignored


def test_two_backups_in_the_same_second_do_not_overwrite_each_other(tmp_path, monkeypatch):
    from datetime import datetime

    monkeypatch.setattr(requeue, "_now", lambda: datetime(2026, 10, 10, 9, 30, 15))
    rows = [{column: None for column in sql.REQUEUE_BACKUP_COLUMNS} | {"id": 1}]
    first = requeue.write_backup(rows, tmp_path / "b")
    second = requeue.write_backup(rows, tmp_path / "b")
    assert first.name == "requeue-20261010-093015.csv"
    assert second != first and second.exists() and first.exists()


def test_apply_without_targets_writes_no_backup_and_nothing_else(env, table, backups, capsys):
    table.rows = {1: make_row(1), 2: make_row(2, tagging_status="verified", tagging_notes=KRX)}
    code, out, err = requeue_cmd(capsys, "--krx-unmatched", "--apply")
    assert (code, err) == (0, "")
    assert report(out) == {"mode": "apply", "selected": {"krx_unmatched": 0}, "skipped": {},
                           "total": 0, "requeued": 0, "backup_file": None}
    assert table.statements() == ["from_env", "candidates", "close"]
    assert not backups.exists()


def test_a_backup_that_cannot_be_written_changes_nothing(env, table, monkeypatch, tmp_path, capsys):
    blocker = tmp_path / "a-file"
    blocker.write_text("not a folder", encoding="utf-8")
    monkeypatch.setattr(requeue, "BACKUP_DIR", blocker / "requeue")
    table.rows = {1: make_row(1, tagging_status="review_needed", tagging_notes=KRX)}
    before = dict(table.rows[1])

    code, out, err = requeue_cmd(capsys, "--krx-unmatched", "--apply")

    assert (code, out) == (1, "")
    assert one_line(err).startswith("백업 파일을 쓰지 못해 아무것도 바꾸지 않았습니다: ")
    assert table.statements() == ["from_env", "candidates", "backup-select", "close"]
    assert not table.wrote()
    assert table.rows[1] == before


def test_the_recheck_in_the_transaction_drops_rows_that_changed(env, table, backups, capsys):
    table.rows = {row_id: make_row(row_id, tagging_status="review_needed", tagging_notes=KRX)
                  for row_id in (1, 2, 3, 4, 5)}

    def another_session(rows):   # between the backup and the lock
        rows[1]["tagging_status"] = "verified"                  # a person approved it
        rows[2]["tagging_notes"] = "type_indeterminate"         # re-tagged, no longer a match
        rows[3]["tagged_at"] = "2026-10-10 01:00:00+00"         # re-tagged: the backup is stale
        rows[4]["tagging_status"] = "pending"

    table.before_lock = another_session
    code, out, err = requeue_cmd(capsys, "--krx-unmatched", "--apply")

    assert (code, err) == (0, "")
    data = report(out)
    assert (data["total"], data["requeued"]) == (5, 1)
    [reset] = [event for event in table.events if event[0] == "fetch" and event[1] == sql.REQUEUE_RESET_SQL]
    assert reset[2][0] == [5]
    assert table.rows[5]["tagging_status"] == "pending"
    assert table.rows[1]["tagging_status"] == "verified"
    assert table.rows[3]["tagging_status"] == "review_needed"
    assert table.statements()[-2:] == ["commit", "close"]


def test_when_every_row_changed_nothing_is_reset(env, table, capsys):
    table.rows = {1: make_row(1, tagging_status="review_needed", tagging_notes=KRX)}
    table.before_lock = lambda rows: rows[1].update(tagging_status="verified")
    code, out, _ = requeue_cmd(capsys, "--krx-unmatched", "--apply")
    assert code == 0
    data = report(out)
    assert (data["requeued"], data["total"]) == (0, 1)
    assert data["backup_file"] is not None
    assert "reset" not in table.statements()
    assert table.rows[1]["tagging_status"] == "verified"


def test_the_recheck_uses_the_first_page_result_from_before_the_transaction(env, table, pages,
                                                                           capsys):
    table.rows = {1: make_row(1, tagging_status="review_needed", tagging_notes=UNREADABLE,
                              publisher=None, publisher_type=None)}
    code, out, _ = requeue_cmd(capsys, "--unreadable", "--apply")
    assert code == 0
    assert report(out)["requeued"] == 1
    assert pages.checked == ["2026/05/1.pdf"]   # rendered once, before the transaction


def test_a_count_mismatch_rolls_everything_back(env, table, backups, capsys):
    table.rows = {row_id: make_row(row_id, tagging_status="review_needed", tagging_notes=KRX)
                  for row_id in (1, 2, 3)}
    before = {row_id: dict(row) for row_id, row in table.rows.items()}
    table.reset_result = lambda ids: ids[:-1]

    code, out, err = requeue_cmd(capsys, "--krx-unmatched", "--apply")

    assert (code, out) == (1, "")
    line = one_line(err)
    assert "취소" in line and "2" in line and "3" in line
    assert table.statements() == ["from_env", "candidates", "backup-select", "begin", "lock",
                                  "reset", "rollback", "close"]
    assert table.rows == before
    assert len(list(backups.iterdir())) == 1   # the backup stays as the record


def test_another_error_exits_1_with_one_line_and_empty_stdout(env, table, capsys):
    table.fail_fetch = ConnectionError("server closed the connection\nunexpectedly")
    code, out, err = requeue_cmd(capsys, "--krx-unmatched", "--apply")
    assert (code, out) == (1, "")
    line = one_line(err)
    assert "server closed the connection unexpectedly" in line
    assert table.statements()[-1] == "close"


def test_a_stdout_that_cannot_show_a_value_gets_the_same_json_escaped(env, table, monkeypatch):
    """A pipe in the console's code page (cp949) must not turn a committed apply into a traceback."""
    publisher = "Nomura ☃"   # the snowman is not in cp949
    table.rows = {1: make_row(1, tagging_status="review_needed", tagging_notes=KRX,
                              publisher=publisher)}
    raw = io.BytesIO()
    stdout = io.TextIOWrapper(raw, encoding="cp949", errors="strict", newline="\n")
    monkeypatch.setattr("sys.stdout", stdout)
    assert main(["tag", "requeue", "--krx-unmatched"]) == 0
    stdout.flush()
    out = raw.getvalue().decode("ascii")
    data = json.loads(out)
    assert out == json.dumps(data, indent=2, ensure_ascii=True) + "\n"
    assert data["publisher_values"] == {publisher: 1}


def test_the_pool_is_the_taggers_and_is_closed(env, table, capsys):
    requeue_cmd(capsys, "--krx-unmatched")
    assert table.events[0] == ("from_env", 10)
    assert table.events[-1] == ("close",)


def test_commands_read_dotenv_when_they_start(tagger_env, table, dictionary_file, processes, pages,
                                              backups, env_file, capsys):
    env_file.write_text("SUPABASE_DB_URL=postgresql://from-dotenv@127.0.0.1:1/never\n",
                        encoding="utf-8")
    code, _, _ = requeue_cmd(capsys, "--krx-unmatched")
    assert code == 0
    assert os.environ["SUPABASE_DB_URL"] == "postgresql://from-dotenv@127.0.0.1:1/never"


def test_the_bundled_dictionary_is_the_default():
    loaded = requeue.load_publisher_dictionary()
    assert loaded.canonical_names == vocabulary.canonical_names()


# ── the running-process check ────────────────────────────────────────────────

@pytest.mark.parametrize("command_line,job", [
    ('"C:\\rd\\.venv\\Scripts\\python.exe" -u -m research_desk tag run --worker-id h-1-ab12', "backfill"),
    ("python -m research_desk tag run --dry-run", "backfill"),
    ('powershell.exe -ExecutionPolicy Bypass -File "C:\\rd\\scripts\\Run-Batches.ps1" -Iterations 3',
     "backfill"),
    ("python -m research_desk tag escalate --since 2026-05-08", "escalate"),
    ("python -m research_desk collect --backfill-days 2", "collect"),
    ('"C:\\rd\\.venv\\Scripts\\python.exe" -m research_desk web --view review', "web"),
    ('bash -c "cd /c/rd && python -m research_desk web"', "web"),
    ("python -m research_desk tag requeue --apply", None),
    ("python -m research_desk tag inspect", None),
    ("python -m research_desk tag reset-worker --worker-id w", None),
    ("python -m research_desk stocks set-version --as-of 2026-05-08", None),
    ("python -m pytest -q", None),
    ("C:\\Windows\\System32\\svchost.exe -k netsvcs", None),
    (None, None),
])
def test_running_jobs_are_found_by_command_line(command_line, job):
    found = requeue.running_jobs([(OWN_PID, "python -m research_desk tag requeue"),
                                  (77, command_line)], OWN_PID)
    assert found == ({job: [77]} if job else {})


def test_the_current_process_is_not_a_running_job():
    assert requeue.running_jobs([(OWN_PID, "python -m research_desk tag run")], OWN_PID) == {}


def test_the_guidance_names_every_running_job_on_one_line():
    listed = [(OWN_PID, "python -m research_desk tag requeue --apply"),
              (31, "python -m research_desk web"), (12, "python -m research_desk tag run"),
              (13, "powershell -File scripts\\run-batches.ps1")]
    with pytest.raises(requeue.RequeueNotReady) as excinfo:
        requeue.check_nothing_running(listed, OWN_PID)
    message = str(excinfo.value)
    assert "\n" not in message
    assert message == (
        "실행 중인 작업이 있어 아무것도 바꾸지 않았습니다. 다음을 끈 뒤 다시 실행하세요: "
        "백필(run-batches.ps1 또는 research_desk tag run) PID 12, 13 / 웹앱(research_desk web) PID 31")


def test_a_list_without_this_process_cannot_be_trusted():
    with pytest.raises(requeue.RequeueNotReady, match="프로세스 목록"):
        requeue.check_nothing_running([(5, "explorer.exe")], OWN_PID)
    with pytest.raises(requeue.RequeueNotReady, match="프로세스 목록"):
        requeue.check_nothing_running([], OWN_PID)


REAL_LIST_PROCESSES = requeue.list_processes


@pytest.fixture
def powershell(monkeypatch):
    state = SimpleNamespace(stdout=b"", returncode=0, error=None, argv=None, kwargs=None)

    def fake_run(argv, **kwargs):
        state.argv, state.kwargs = argv, kwargs
        if state.error is not None:
            raise state.error
        return subprocess.CompletedProcess(argv, state.returncode, state.stdout, b"Access denied\n")

    monkeypatch.setattr(requeue.subprocess, "run", fake_run)
    return state


def test_the_process_list_comes_from_powershell(powershell):
    powershell.stdout = json.dumps([
        {"ProcessId": 4, "CommandLine": None},
        {"ProcessId": 77, "CommandLine": "python -m research_desk web"},
        {"ProcessId": 78, "CommandLine": "C:\\보고서\\python.exe -m research_desk collect"},
    ], ensure_ascii=False).encode("utf-8")
    assert REAL_LIST_PROCESSES() == [(4, None), (77, "python -m research_desk web"),
                                     (78, "C:\\보고서\\python.exe -m research_desk collect")]
    assert powershell.argv[0].lower().startswith("powershell")
    assert "Get-CimInstance" in powershell.argv[-1] and "Win32_Process" in powershell.argv[-1]
    assert powershell.kwargs.get("timeout")


def test_a_single_process_comes_back_as_one_object(powershell):
    powershell.stdout = b'{"ProcessId": 9, "CommandLine": "x"}'
    assert REAL_LIST_PROCESSES() == [(9, "x")]


@pytest.mark.parametrize("case", ["exit code", "not json", "empty", "no pid", "not found", "timeout"])
def test_a_process_list_that_cannot_be_read_is_an_error(powershell, case):
    if case == "exit code":
        powershell.returncode = 1
        powershell.stdout = b"[]"
    elif case == "not json":
        powershell.stdout = b"Get-CimInstance : Access denied"
    elif case == "empty":
        powershell.stdout = b""
    elif case == "no pid":
        powershell.stdout = b'[{"CommandLine": "x"}]'
    elif case == "not found":
        powershell.error = FileNotFoundError("powershell.exe")
    else:
        powershell.error = subprocess.TimeoutExpired("powershell.exe", 60)
    with pytest.raises(requeue.ProcessListError):
        REAL_LIST_PROCESSES()


# ── the first-page check ─────────────────────────────────────────────────────

def make_pdf(path, pages=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page()
    doc.save(path)
    doc.close()


REAL_FIRST_PAGE_RENDERS = requeue.first_page_renders


def test_the_first_page_check_renders_page_1_under_the_storage_folder(tagger_env, tmp_path):
    storage = tmp_path / "reports"
    tagger_env.setenv("STORAGE_BASE_DIR", str(storage))
    make_pdf(storage / "2026" / "05" / "ok.pdf")   # a page with no text still renders
    (storage / "2026" / "05" / "broken.pdf").write_bytes(b"%PDF-1.4 not really")
    make_pdf(tmp_path / "outside.pdf")

    assert REAL_FIRST_PAGE_RENDERS("2026/05/ok.pdf") is True
    assert REAL_FIRST_PAGE_RENDERS("2026/05/missing.pdf") is False
    assert REAL_FIRST_PAGE_RENDERS("2026/05/broken.pdf") is False
    assert REAL_FIRST_PAGE_RENDERS("../outside.pdf") is False
    assert REAL_FIRST_PAGE_RENDERS(None) is False
    assert REAL_FIRST_PAGE_RENDERS("") is False
