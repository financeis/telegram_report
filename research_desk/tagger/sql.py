"""The tagger's SQL on the ``reports`` table (spec §4: the tagger's part of that table).

Run through ``research_desk.core.db.SupabaseSQL`` (direct asyncpg pool).
supabase-py wraps PostgREST, which has no ``FOR UPDATE SKIP LOCKED`` and no raw
SQL without RPC functions; the direct connection keeps the SQL transparent.
"""
from __future__ import annotations

# Recorded in reports.tagger_version by every tagged row. The one definition.
TAGGER_VERSION = "langgraph-tagger@2.0"

STALE_LOCK_RECLAIM_SQL = """
UPDATE reports
   SET tagging_status='pending', tagging_locked_at=NULL, tagging_worker_id=NULL
 WHERE tagging_status='processing'
   AND tagging_locked_at < now() - ($1::int * interval '1 minute')
"""

ATOMIC_CLAIM_SQL = """
UPDATE reports
   SET tagging_status='processing',
       tagging_locked_at=now(),
       tagging_worker_id=$1
 WHERE id IN (
       SELECT id FROM reports
        WHERE tagging_status='pending'
        ORDER BY downloaded_at ASC
        LIMIT $2
        FOR UPDATE SKIP LOCKED
       )
RETURNING id, file_path, file_name, sent_at, caption, chat_username
"""

DRY_RUN_SELECT_SQL = """
SELECT id, file_path, file_name, sent_at, caption, chat_username
  FROM reports
 WHERE tagging_status='pending'
 ORDER BY downloaded_at ASC
 LIMIT $1
"""

ROW_IDS_FETCH_SQL = """
SELECT id, file_path, file_name, sent_at, caption, chat_username
  FROM reports
 WHERE id = ANY($1::bigint[])
"""

REVERT_TO_PENDING_SQL = """
UPDATE reports
   SET tagging_status='pending', tagging_locked_at=NULL, tagging_worker_id=NULL
 WHERE id=$1 AND tagging_status='processing'
"""

# Operational cleanup: when a wrapper detects nonzero exit from a CLI run,
# revert ONLY rows claimed by that specific worker_id. Scoped narrower than
# STALE_LOCK_RECLAIM_SQL (which uses time TTL) so it cannot race a still-
# running worker on the same machine. Returns affected ids for audit.
RESET_WORKER_SQL = """
UPDATE reports
   SET tagging_status='pending', tagging_locked_at=NULL, tagging_worker_id=NULL
 WHERE tagging_status='processing'
   AND tagging_worker_id=$1
RETURNING id
"""

# Note: in-scope, OOS, unreadable rows all share this UPDATE; payload semantics differ.
# 19 bind args ($1..$19), built by nodes/write.py.
UPDATE_SQL = f"""
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
       tagger_version='{TAGGER_VERSION}',
       taxonomy_version=$19
 WHERE id=$1
"""

ESCALATION_PICK_SQL = """
SELECT id FROM reports
 WHERE tagging_status='review_needed'
   AND tagged_at >= $1
"""

INSPECT_SUMMARY_SQL = """
SELECT
  (SELECT count(*) FROM reports WHERE tagging_status='pending')          AS pending,
  (SELECT count(*) FROM reports WHERE tagging_status='processing')       AS processing,
  (SELECT count(*) FROM reports WHERE tagging_status='auto')             AS auto,
  (SELECT count(*) FROM reports WHERE tagging_status='review_needed')    AS review_needed,
  (SELECT count(*) FROM reports WHERE tagging_status='verified')         AS verified,
  (SELECT count(*) FROM reports WHERE out_of_scope_reason IS NOT NULL)   AS oos_total,
  (SELECT count(*) FROM reports WHERE tagged_at >= now() - interval '24 hours') AS last_24h
"""
