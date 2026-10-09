"""In-memory fakes for the collector tests (ported from the root tests/conftest.py)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any


def make_msg(msg_id: int, *, has_pdf: bool = True, file_name: str = 'r.pdf',
             caption: str | None = None,
             sent_at: datetime | None = None) -> SimpleNamespace:
    """Build a fake Telethon-Message-like object for collector tests."""
    if has_pdf:
        attrs = [SimpleNamespace(file_name=file_name)]
        doc = SimpleNamespace(mime_type='application/pdf', attributes=attrs)
    else:
        doc = None
    return SimpleNamespace(
        id=msg_id,
        document=doc,
        message=caption,
        date=sent_at or datetime(2026, 5, 5, 12, 0, tzinfo=timezone.utc),
    )


class FakeTelegramClient:
    """In-memory fake matching the TelegramClient interface used by collector."""

    def __init__(self) -> None:
        self.new_messages: list[Any] = []          # for iter_messages_after_id / since_date
        self.failed_lookups: dict[int, Any] = {}   # for get_message_by_id
        self.download_results: dict[int, bytes] = {}  # msg_id -> bytes
        self.download_errors: dict[int, Exception] = {}  # msg_id -> exception
        self.calls: list[tuple] = []               # records (method_name, args)

    async def iter_messages_after_id(self, channel: str, min_id: int):
        self.calls.append(('iter_after_id', channel, min_id))
        for m in self.new_messages:
            yield m

    async def iter_messages_since_date(self, channel: str, days_ago: int):
        self.calls.append(('iter_since_date', channel, days_ago))
        for m in self.new_messages:
            yield m

    async def get_message_by_id(self, channel: str, message_id: int):
        self.calls.append(('get_by_id', channel, message_id))
        return self.failed_lookups.get(message_id)

    async def download_pdf_bytes(self, msg) -> bytes:
        self.calls.append(('download', msg.id))
        if msg.id in self.download_errors:
            raise self.download_errors[msg.id]
        return self.download_results.get(msg.id, b'fake pdf bytes')


class TrackingFakeClient(FakeTelegramClient):
    """FakeTelegramClient that records max concurrent download_pdf_bytes calls.

    Used by concurrency tests to observe whether the collector's Semaphore
    bound is honored.
    """

    def __init__(self) -> None:
        super().__init__()
        self.current_concurrent = 0
        self.max_concurrent_observed = 0

    async def download_pdf_bytes(self, msg) -> bytes:
        self.calls.append(('download', msg.id))
        self.current_concurrent += 1
        self.max_concurrent_observed = max(
            self.max_concurrent_observed, self.current_concurrent
        )
        # Yield to other tasks so concurrency can actually be observed
        await asyncio.sleep(0.01)
        self.current_concurrent -= 1
        if msg.id in self.download_errors:
            raise self.download_errors[msg.id]
        return self.download_results.get(msg.id, b'fake pdf bytes')


class FakeStorage:
    """In-memory fake matching the Storage interface used by collector."""

    def __init__(self, base_dir: Path, max_seen: int = 0,
                 failed_ids: list[int] | None = None,
                 existing_ids: set[int] | None = None) -> None:
        self.base_dir = base_dir
        self._max_seen = max_seen
        self._failed_ids = list(failed_ids or [])
        self._existing_ids: set[int] = set(existing_ids or [])
        self.inserted: list[dict] = []
        self.failed_upserts: list[tuple[str, int, str]] = []
        self.failed_removes: list[tuple[str, int]] = []
        self.saved_files: list[tuple[str, bytes]] = []

    def get_max_seen_message_id(self, chat_username: str) -> int:
        return self._max_seen

    def get_failed_message_ids(self, chat_username: str) -> list[int]:
        return list(self._failed_ids)

    def get_all_message_ids(self, chat_username: str) -> set[int]:
        return set(self._existing_ids)

    def insert_report_metadata(self, meta: dict) -> None:
        self.inserted.append(meta)

    def upsert_failed_attempt(self, chat_username: str, message_id: int,
                              error_message: str) -> int:
        self.failed_upserts.append((chat_username, message_id, error_message))
        # Count occurrences of this (chat, msg_id) to mimic attempt_count
        return sum(1 for c, m, _ in self.failed_upserts if c == chat_username and m == message_id)

    def remove_failed_attempt(self, chat_username: str, message_id: int) -> None:
        self.failed_removes.append((chat_username, message_id))

    def save_pdf_atomically(self, content: bytes, filename: str) -> Path:
        self.saved_files.append((filename, content))
        path = self.base_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path
