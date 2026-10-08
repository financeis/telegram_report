"""Test setup shared by every research_desk test.

- No test reads the real ``.env``: ``load_env()`` is switched off for each
  test. The switch lives in ``research_desk.core.settings``, so modules that
  did ``from research_desk.core.settings import load_env`` obey it too.
- ``env_file``: opt in to real ``.env`` reading. Gives the path of a temp
  ``.env`` (empty at first) that ``load_env()`` reads; write lines to it, then
  call the code under test. Variables loaded during the test are removed
  afterwards.
"""
from __future__ import annotations

import os

import pytest

from research_desk.core import settings


@pytest.fixture(autouse=True)
def _env_file_off(monkeypatch):
    monkeypatch.setattr(settings, "ENV_FILE_ENABLED", False)
    monkeypatch.setattr(settings, "ENV_FILE_PATH", None)


@pytest.fixture
def env_file(_env_file_off, tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(settings, "ENV_FILE_ENABLED", True)
    monkeypatch.setattr(settings, "ENV_FILE_PATH", path)
    before = dict(os.environ)
    yield path
    # load_env() writes os.environ directly; put the process environment back.
    for key in set(os.environ) - set(before):
        del os.environ[key]
    for key, value in before.items():
        if os.environ.get(key) != value:
            os.environ[key] = value
