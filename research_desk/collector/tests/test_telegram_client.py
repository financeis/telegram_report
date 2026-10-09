"""The Telethon wrapper, against a fake Telethon client (no network, no real session)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import telethon

from research_desk.collector.telegram import TelegramClient


class FakeTelethonClient:
    def __init__(self, session, api_id, api_hash) -> None:
        self.init = dict(session=session, api_id=api_id, api_hash=api_hash)
        self.flood_sleep_threshold = None
        self.calls: list[tuple] = []
        self.messages: list = []
        self.by_id: dict = {}
        self.media = b'%PDF-1.7'

    async def start(self):
        self.calls.append(('start',))

    async def disconnect(self):
        self.calls.append(('disconnect',))

    async def iter_messages(self, channel, **kwargs):
        self.calls.append(('iter_messages', channel, kwargs))
        for m in self.messages:
            yield m

    async def get_messages(self, channel, ids):
        self.calls.append(('get_messages', channel, ids))
        return self.by_id.get(ids)

    async def download_media(self, msg, file):
        self.calls.append(('download_media', msg, file))
        return self.media


@pytest.fixture
def made(monkeypatch) -> list[FakeTelethonClient]:
    """Telethon clients created by the wrapper (telethon.TelegramClient is swapped)."""
    created: list[FakeTelethonClient] = []

    def factory(**kwargs):
        client = FakeTelethonClient(**kwargs)
        created.append(client)
        return client

    monkeypatch.setattr(telethon, 'TelegramClient', factory)
    return created


@pytest.fixture
def wrapper(made, tmp_path):
    return TelegramClient(api_id=12345, api_hash='hash', session_path=tmp_path / 'sessions' / 'samstudy')


def test_creates_the_session_folder_and_the_telethon_client(made, tmp_path):
    session_path = tmp_path / 'sessions' / 'samstudy'

    TelegramClient(api_id=12345, api_hash='hash', session_path=session_path)

    assert (tmp_path / 'sessions').is_dir()
    (client,) = made
    assert client.init == dict(session=str(session_path), api_id=12345, api_hash='hash')
    # Auto-sleep on FloodWaitError under 60s; raise above
    assert client.flood_sleep_threshold == 60


async def test_context_manager_starts_and_disconnects(wrapper, made):
    async with wrapper as entered:
        assert entered is wrapper
        assert made[0].calls == [('start',)]
    assert made[0].calls == [('start',), ('disconnect',)]


async def test_iter_messages_after_id_is_oldest_first(wrapper, made):
    made[0].messages = ['m1', 'm2']

    got = [m async for m in wrapper.iter_messages_after_id('chan', 100)]

    assert got == ['m1', 'm2']
    assert made[0].calls == [('iter_messages', 'chan', {'min_id': 100, 'reverse': True})]


async def test_iter_messages_since_date_starts_days_ago(wrapper, made):
    made[0].messages = ['m1']
    before = datetime.now(timezone.utc)

    got = [m async for m in wrapper.iter_messages_since_date(1378197756, 30)]

    after = datetime.now(timezone.utc)
    assert got == ['m1']
    ((name, channel, kwargs),) = made[0].calls
    assert (name, channel, kwargs['reverse']) == ('iter_messages', 1378197756, True)
    assert before - timedelta(days=30) <= kwargs['offset_date'] <= after - timedelta(days=30)


async def test_get_message_by_id_returns_none_when_missing(wrapper, made):
    made[0].by_id = {5: 'msg5'}

    assert await wrapper.get_message_by_id('chan', 5) == 'msg5'
    assert await wrapper.get_message_by_id('chan', 6) is None
    assert made[0].calls == [('get_messages', 'chan', 5), ('get_messages', 'chan', 6)]


async def test_download_pdf_bytes_downloads_into_memory(wrapper, made):
    assert await wrapper.download_pdf_bytes('msg') == b'%PDF-1.7'
    assert made[0].calls == [('download_media', 'msg', bytes)]

    made[0].media = bytearray(b'abc')
    result = await wrapper.download_pdf_bytes('msg')
    assert result == b'abc' and type(result) is bytes


async def test_download_pdf_bytes_rejects_other_results(wrapper, made):
    made[0].media = None

    with pytest.raises(RuntimeError, match="download_media returned unexpected type: <class 'NoneType'>"):
        await wrapper.download_pdf_bytes('msg')
