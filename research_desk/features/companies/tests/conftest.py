"""Safety net for every companies test: the real favorites file is out of reach.

``fake_home`` (autouse) points ``Path.home()`` and ``~`` at a new empty temp folder (outside the
test's ``tmp_path``), so even a service built without a favorites path cannot reach
``~/.review_viewer/favorites.json``.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def fake_home(tmp_path_factory, monkeypatch) -> Path:
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("USERPROFILE", str(home))   # Windows
    monkeypatch.setenv("HOME", str(home))          # elsewhere
    assert Path.home() == home
    return home
