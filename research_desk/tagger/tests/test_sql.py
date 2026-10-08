"""Smoke tests for SQL constants — no live DB.

Ported from langgraph_tagger/tests/test_supabase_io.py (the constants moved to
research_desk/tagger/sql.py; the asyncpg adapter is research_desk/core/db.py).
"""
from pathlib import Path

from research_desk.tagger import sql
from research_desk.tagger.sql import (
    ATOMIC_CLAIM_SQL, DRY_RUN_SELECT_SQL, ESCALATION_PICK_SQL,
    INSPECT_SUMMARY_SQL, REVERT_TO_PENDING_SQL, ROW_IDS_FETCH_SQL,
    STALE_LOCK_RECLAIM_SQL, UPDATE_SQL,
)

# The UPDATE text as langgraph_tagger/supabase_io.py had it, character for character.
PREVIOUS_UPDATE_SQL = """
UPDATE reports
   SET published_at=$2,
       report_type=$3,
       publisher=$4,
       publisher_type=$5,
       analysts=$6,
       title=$7,
       stock_codes=$8,
       company_names=$9,
       stock_codes_raw=$10,
       company_names_raw=$11,
       sectors_major=$12,
       sectors_minor=$13,
       products=$14,
       out_of_scope_reason=$15,
       tagging_status=$16,
       tagging_confidence=$17,
       tagging_notes=$18,
       tagging_locked_at=NULL,
       tagging_worker_id=NULL,
       tagged_at=now(),
       tagger_version='langgraph-tagger@2.0',
       taxonomy_version=$19
 WHERE id=$1
"""


def test_atomic_claim_uses_skip_locked():
    assert "FOR UPDATE SKIP LOCKED" in ATOMIC_CLAIM_SQL
    assert "tagging_status='processing'" in ATOMIC_CLAIM_SQL


def test_stale_reclaim_uses_param_threshold():
    # Threshold is parameterized via $1::int (LOCK_TTL_MINUTES from env), not hardcoded.
    assert "$1::int * interval '1 minute'" in STALE_LOCK_RECLAIM_SQL


def test_dry_run_select_does_not_mutate():
    assert "UPDATE" not in DRY_RUN_SELECT_SQL


def test_update_sql_has_19_placeholders():
    import re
    placeholders = set(re.findall(r"\$\d+", UPDATE_SQL))
    expected = {f"${i}" for i in range(1, 20)}
    assert placeholders == expected


def test_update_sql_has_raw_audit_columns():
    assert "stock_codes_raw=$10" in UPDATE_SQL
    assert "company_names_raw=$11" in UPDATE_SQL
    assert "topics" not in UPDATE_SQL


def test_update_sql_tagger_version_is_2_0():
    assert "tagger_version='langgraph-tagger@2.0'" in UPDATE_SQL


def test_escalation_pick_filters_by_status_and_date():
    assert "tagging_status='review_needed'" in ESCALATION_PICK_SQL
    assert "$1" in ESCALATION_PICK_SQL


def test_revert_only_acts_on_processing():
    assert "tagging_status='processing'" in REVERT_TO_PENDING_SQL


def test_row_ids_fetch_and_inspect_are_reads():
    assert "ANY($1::bigint[])" in ROW_IDS_FETCH_SQL
    assert "UPDATE" not in ROW_IDS_FETCH_SQL
    assert "UPDATE" not in INSPECT_SUMMARY_SQL


# ── tagger version (spec §9.2) ───────────────────────────────────────────────

def test_tagger_version_constant_is_what_the_update_writes():
    assert sql.TAGGER_VERSION == "langgraph-tagger@2.0"
    assert f"tagger_version='{sql.TAGGER_VERSION}'" in UPDATE_SQL


def test_update_sql_text_is_unchanged():
    """Same statement as before, so the same 19 arguments in the same order."""
    assert UPDATE_SQL == PREVIOUS_UPDATE_SQL


def test_tagger_version_is_spelled_out_only_once():
    """Defined once (sql.py); the stale ``langgraph-tagger@1.0`` constant is gone."""
    package = Path(sql.__file__).resolve().parent
    hits = [
        (path.relative_to(package).as_posix(), line.strip())
        for path in sorted(package.rglob("*.py"))
        if "tests" not in path.relative_to(package).parts
        for line in path.read_text(encoding="utf-8").splitlines()
        if "langgraph-tagger@" in line
    ]
    assert hits == [("sql.py", 'TAGGER_VERSION = "langgraph-tagger@2.0"')]
