"""Storage layer: Supabase metadata + local PDF filesystem.

This module also exports two pure helpers:
- sanitize_filename: make a Telegram-supplied name safe for the filesystem.
- compute_sha256: chunked file hash for integrity tracking.

The Supabase client is created through ``research_desk.core.db``
(``build_storage``); the collector owns the ``reports`` rows it creates and the
``failed_attempts`` table.
"""
from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Windows-forbidden filename chars + ASCII control chars (\x00–\x1f)
_FORBIDDEN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str, max_len: int = 100) -> str:
    """Convert an arbitrary string into a safe filename.

    Rules (in order):
      1. Replace OS-forbidden chars with '_'
      2. Strip leading/trailing dots and spaces (Windows quirk)
      3. Ensure name is non-empty (fallback to 'unnamed')
      4. Ensure '.pdf' extension
      5. Truncate to max_len, preserving '.pdf' suffix
    """
    # 1. Remove forbidden chars
    name = _FORBIDDEN_CHARS.sub('_', name)
    # 2. Strip dots/spaces from edges
    name = name.strip('. ')
    # 3. Empty fallback
    if not name:
        name = 'unnamed'
    # 4. Ensure .pdf
    if not name.lower().endswith('.pdf'):
        name = name + '.pdf'
    # 5. Truncate (keep .pdf suffix)
    if len(name) > max_len:
        name = name[: max_len - 4] + '.pdf'
    return name


def compute_sha256(file_path: Path, chunk_size: int = 64 * 1024) -> str:
    """Compute SHA-256 of a file by streaming in chunks.

    Returns lowercase hex digest. Avoids loading the whole file into memory.
    """
    h = hashlib.sha256()
    with open(file_path, 'rb') as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


class Storage:
    """Storage facade over Supabase (metadata) + local filesystem (PDF blobs).

    The supabase_client is `Any` to keep this module decoupled from supabase-py
    types; pass a real `supabase.Client` in production, or `None` in
    filesystem-only tests.
    """

    def __init__(self, supabase_client: Any, base_dir: Path) -> None:
        self._sb = supabase_client
        self.base_dir = Path(base_dir)

    # === Filesystem ===

    def save_pdf_atomically(self, content: bytes, filename: str) -> Path:
        """Write `content` to `base_dir/filename` atomically.

        Strategy: write to `<filename>.partial`, then `os.replace()` to
        the final name (atomic on the same filesystem).

        Returns the final Path on success. Raises on filesystem errors.
        """
        self.base_dir.mkdir(parents=True, exist_ok=True)
        target = self.base_dir / filename
        partial = target.with_suffix(target.suffix + '.partial')
        partial.write_bytes(content)
        os.replace(partial, target)
        return target

    # === Supabase ===

    def get_max_seen_message_id(self, chat_username: str) -> int:
        """Return max(message_id) from reports ∪ failed_attempts for this chat.

        Returns 0 if the channel has no rows yet (= first run).
        """
        # Two queries → max in Python. Cleaner than UNION via the supabase-py builder.
        max_reports = self._max_in_table('reports', chat_username)
        max_failed = self._max_in_table('failed_attempts', chat_username)
        return max(max_reports, max_failed)

    def _max_in_table(self, table: str, chat_username: str) -> int:
        result = (
            self._sb.table(table)
            .select('message_id')
            .eq('chat_username', chat_username)
            .order('message_id', desc=True)
            .limit(1)
            .execute()
        )
        if not result.data:
            return 0
        return int(result.data[0]['message_id'])

    def get_all_message_ids(self, chat_username: str) -> set[int]:
        """Return ALL message_ids already in reports for this chat.

        Pages through results explicitly because PostgREST/Supabase enforces a
        default max of 1000 rows per request — without pagination, this returns
        an incomplete set once the table exceeds that, breaking backfill dedupe
        (backfill design §3.1).
        """
        PAGE_SIZE = 1000
        ids: set[int] = set()
        offset = 0
        while True:
            result = (
                self._sb.table('reports')
                .select('message_id')
                .eq('chat_username', chat_username)
                .order('message_id')  # stable order for predictable paging
                .range(offset, offset + PAGE_SIZE - 1)
                .execute()
            )
            batch = result.data or []
            ids.update(int(row['message_id']) for row in batch)
            if len(batch) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
        return ids

    def get_failed_message_ids(self, chat_username: str) -> list[int]:
        """Return all message_ids currently in failed_attempts for this chat (oldest first).

        Paginates to defeat Supabase's default 1000-row response limit
        (backfill design §3.2). Same pattern as get_all_message_ids.
        """
        PAGE_SIZE = 1000
        ids: list[int] = []
        offset = 0
        while True:
            result = (
                self._sb.table('failed_attempts')
                .select('message_id')
                .eq('chat_username', chat_username)
                .order('message_id', desc=False)
                .range(offset, offset + PAGE_SIZE - 1)
                .execute()
            )
            batch = result.data or []
            ids.extend(int(row['message_id']) for row in batch)
            if len(batch) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
        return ids

    def insert_report_metadata(self, meta: dict) -> None:
        """Upsert a row into reports keyed on (chat_username, message_id).

        Idempotent at the DB layer: re-running with the same key updates the row
        with identical data (harmless), avoiding stuck-retry loops when an INSERT
        appears to fail but actually committed (transient timeouts).

        Required keys (see collector design §4.6):
          message_id, chat_username, sent_at, file_name, file_path,
          file_size_bytes, file_hash_sha256
        Optional: caption.
        """
        # sent_at must be ISO-format string for supabase-py over REST
        payload = dict(meta)
        sent_at = payload.get('sent_at')
        if sent_at is not None and not isinstance(sent_at, str):
            payload['sent_at'] = sent_at.isoformat()
        self._sb.table('reports').upsert(
            payload,
            on_conflict='chat_username,message_id',
        ).execute()

    def upsert_failed_attempt(
        self, chat_username: str, message_id: int, error_message: str
    ) -> int:
        """Insert a new failed_attempts row, or increment attempt_count if it exists.

        Returns the NEW attempt_count value (1 on first failure, prev+1 on retry).
        Caller can use this for threshold-based warnings (collector design §5.3).
        """
        # supabase-py `upsert` with on_conflict requires us to manage attempt_count
        # manually, since we want += 1 on conflict. So: SELECT first, then INSERT or UPDATE.
        existing = (
            self._sb.table('failed_attempts')
            .select('id, attempt_count')
            .eq('chat_username', chat_username)
            .eq('message_id', message_id)
            .limit(1)
            .execute()
        )
        if existing.data:
            row = existing.data[0]
            new_count = int(row['attempt_count']) + 1
            self._sb.table('failed_attempts').update({
                'attempt_count': new_count,
                'last_failed_at': datetime.now(timezone.utc).isoformat(),
                'error_message': error_message,
            }).eq('id', row['id']).execute()
            return new_count
        else:
            self._sb.table('failed_attempts').insert({
                'message_id': message_id,
                'chat_username': chat_username,
                'attempt_count': 1,
                'error_message': error_message,
            }).execute()
            return 1

    def remove_failed_attempt(self, chat_username: str, message_id: int) -> None:
        """Delete the failed_attempts row for this message (no-op if absent)."""
        (
            self._sb.table('failed_attempts')
            .delete()
            .eq('chat_username', chat_username)
            .eq('message_id', message_id)
            .execute()
        )


def build_storage(supabase_url: str, supabase_service_key: str, base_dir: Path) -> Storage:
    """Construct a Storage with a real Supabase client (core.db). Used by the collect command."""
    from research_desk.core.db import supabase_client  # imported lazily to keep tests fast

    client = supabase_client(supabase_url, supabase_service_key)
    return Storage(supabase_client=client, base_dir=base_dir)
