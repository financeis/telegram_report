"""core.mongo: MongoDB read handle and reachability check (pymongo's client is a fake, no server)."""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pymongo
import pytest
from pymongo.errors import ConfigurationError, OperationFailure, ServerSelectionTimeoutError

import research_desk
from research_desk.core import mongo

REPOSITORY = Path(research_desk.__file__).resolve().parent.parent


class FakeDatabase:
    def __init__(self, client, name):
        self.client, self.name = client, name

    def __getitem__(self, name):
        return SimpleNamespace(database=self, name=name)  # pymongo's Collection has these two


class FakeMongoClient:
    """Stands in for pymongo.MongoClient: records how it was made; ``fail`` makes ping raise."""

    made: list = []
    fail = None

    def __init__(self, url, **options):
        self.url, self.options = url, options
        self.commands: list[str] = []
        self.closed = False
        FakeMongoClient.made.append(self)

    @property
    def admin(self):
        return SimpleNamespace(command=self._command)

    def _command(self, name):
        self.commands.append(name)
        if FakeMongoClient.fail is not None:
            raise FakeMongoClient.fail
        return {"ok": 1.0}

    def __getitem__(self, name):
        return FakeDatabase(self, name)

    def close(self):
        self.closed = True


@pytest.fixture
def clients(monkeypatch):
    monkeypatch.setattr(FakeMongoClient, "made", [])
    monkeypatch.setattr(FakeMongoClient, "fail", None)
    monkeypatch.setattr(pymongo, "MongoClient", FakeMongoClient)
    return FakeMongoClient


# ── collection handle ────────────────────────────────────────────────────────

def test_collection_handle_names_the_database_and_collection(clients):
    handle = mongo.mongo_collection("mongodb://localhost:27017/", "FS", "A001_v2")

    (client,) = clients.made
    assert (handle.name, handle.database.name, handle.database.client) == ("A001_v2", "FS", client)
    assert client.url == "mongodb://localhost:27017/"
    # An unreachable server fails the first query after 5 s instead of hanging.
    assert client.options == {"serverSelectionTimeoutMS": 5000, "connectTimeoutMS": 5000}
    assert client.commands == []  # nothing is sent until the caller reads


def test_collection_handle_takes_a_timeout(clients):
    mongo.mongo_collection("mongodb://db.example.invalid:27017/", "FS", "A001_v2", timeout_ms=1500)
    (client,) = clients.made
    assert client.options == {"serverSelectionTimeoutMS": 1500, "connectTimeoutMS": 1500}


# ── ping ─────────────────────────────────────────────────────────────────────

def test_ping_is_true_when_the_server_answers(clients):
    assert mongo.ping("mongodb://localhost:27017/") is True
    (client,) = clients.made
    assert client.commands == ["ping"]
    assert client.options == {"serverSelectionTimeoutMS": 3000, "connectTimeoutMS": 3000}
    assert client.closed


def test_ping_takes_a_timeout(clients):
    assert mongo.ping("mongodb://localhost:27017/", timeout_ms=250) is True
    (client,) = clients.made
    assert client.options == {"serverSelectionTimeoutMS": 250, "connectTimeoutMS": 250}


@pytest.mark.parametrize("error", [
    ServerSelectionTimeoutError("localhost:27017: [WinError 10061] No connection could be made"),
    OperationFailure("Authentication failed."),
])
def test_ping_is_false_when_the_server_is_unreachable_or_refuses(clients, caplog, error):
    clients.fail = error
    url = "mongodb://reader:s3cret-pass@localhost:27017/"
    with caplog.at_level(logging.DEBUG, logger=mongo.__name__):
        assert mongo.ping(url) is False
    (client,) = clients.made
    assert client.commands == ["ping"]
    assert client.closed
    assert "s3cret-pass" not in caplog.text  # a URL can hold a password: never logged


def test_ping_is_false_when_the_url_is_unusable(monkeypatch):
    def refuse(url, **options):
        raise ConfigurationError("bad URI")

    monkeypatch.setattr(pymongo, "MongoClient", refuse)
    assert mongo.ping("mongodb+srv://no-such-cluster.example.invalid/") is False


# ── import cost ──────────────────────────────────────────────────────────────

def test_importing_core_mongo_loads_neither_pymongo_nor_bson():
    """Every command imports core when it starts; the driver loads only when a function here runs."""
    code = ("import sys, research_desk.core.mongo\n"
            "print(sorted(m for m in ('pymongo', 'bson') if m in sys.modules))\n")
    result = subprocess.run([sys.executable, "-c", code], cwd=REPOSITORY, capture_output=True,
                            text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"
