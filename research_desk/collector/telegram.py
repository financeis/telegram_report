"""Telethon wrapper + pure PDF predicates.

``has_pdf`` and ``_get_original_filename`` work on any Telethon-shaped message
object. Telethon itself is imported only when a ``TelegramClient`` is created,
so importing this module (and registering the ``collect`` command, which the
entry point does for every command) does not load it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

if TYPE_CHECKING:
    from telethon.tl.custom.message import Message  # type: ignore


def has_pdf(msg: Any) -> bool:
    """Return True if the message has a PDF attachment.

    Strategy:
      1. If no document at all → False
      2. If document.mime_type == 'application/pdf' → True (most reliable)
      3. Else, scan attributes for any file_name ending in .pdf (case-insensitive)
      4. Else → False
    """
    if not getattr(msg, 'document', None):
        return False
    if msg.document.mime_type == 'application/pdf':
        return True
    for attr in getattr(msg.document, 'attributes', []):
        file_name = getattr(attr, 'file_name', None)
        if file_name and file_name.lower().endswith('.pdf'):
            return True
    return False


def _get_original_filename(msg: Any) -> str | None:
    """Extract the Telegram-original file_name attribute, or None."""
    if not getattr(msg, 'document', None):
        return None
    for attr in getattr(msg.document, 'attributes', []):
        file_name = getattr(attr, 'file_name', None)
        if file_name:
            return file_name
    return None


class TelegramClient:
    """Thin async wrapper around Telethon for our specific use case.

    Use as an async context manager:
        async with TelegramClient(api_id, api_hash, session_path) as client:
            async for msg in client.iter_messages_after_id(...):
                ...
    """

    def __init__(self, api_id: int, api_hash: str, session_path: Path) -> None:
        from telethon import TelegramClient as _TelethonClient

        # Ensure parent dir exists; Telethon won't create it
        session_path.parent.mkdir(parents=True, exist_ok=True)
        self._client = _TelethonClient(
            session=str(session_path),
            api_id=api_id,
            api_hash=api_hash,
        )
        # Auto-sleep on FloodWaitError under 60s; raise above
        self._client.flood_sleep_threshold = 60

    async def __aenter__(self) -> 'TelegramClient':
        await self._client.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._client.disconnect()

    async def iter_messages_after_id(
        self, channel: int | str, min_id: int
    ) -> AsyncIterator[Message]:
        """Yield messages with id > min_id, oldest first."""
        async for msg in self._client.iter_messages(channel, min_id=min_id, reverse=True):
            yield msg

    async def iter_messages_since_date(
        self, channel: int | str, days_ago: int
    ) -> AsyncIterator[Message]:
        """First-run path: yield all messages since `days_ago` days ago, oldest first."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_ago)
        async for msg in self._client.iter_messages(channel, offset_date=cutoff, reverse=True):
            yield msg

    async def get_message_by_id(self, channel: int | str, message_id: int) -> Message | None:
        """Fetch a single message by id. Returns None if deleted/not found."""
        return await self._client.get_messages(channel, ids=message_id)

    async def download_pdf_bytes(self, msg: Message) -> bytes:
        """Download the PDF attached to `msg` and return its bytes.

        We download into memory so storage.save_pdf_atomically can handle the
        atomic write. Reports are typically ~1–10 MB, well within RAM.
        """
        result = await self._client.download_media(msg, file=bytes)
        if not isinstance(result, (bytes, bytearray)):
            raise RuntimeError(f'download_media returned unexpected type: {type(result)}')
        return bytes(result)
