"""Stock favorites persisted in a JSON file.

Atomic writes via tempfile + os.replace. Corrupted JSON is moved to a
.bak sibling and the file is treated as empty (graceful recovery).
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def _read(path: Path) -> list[str]:
    """Read favorites list. Returns [] if file missing.

    On JSON corruption or unexpected shape: move file to .bak and return [].
    """
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError(f"unexpected JSON shape: {type(data).__name__}")
        stocks = data.get('stocks', [])
        if not isinstance(stocks, list):
            raise ValueError(f"'stocks' must be a list, got {type(stocks).__name__}")
        return list(stocks)
    except (json.JSONDecodeError, ValueError):
        bak = path.with_suffix('.json.bak')
        path.rename(bak)
        return []


def _write_atomic(path: Path, stocks: list[str]) -> None:
    """Atomic JSON write — tempfile in same dir, then os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump({'stocks': stocks}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def load(path: Path) -> list[str]:
    """Public: return list of stock codes from the favorites file."""
    return _read(path)


def add(path: Path, code: str) -> None:
    """Idempotent — duplicate adds are no-ops; insertion order preserved."""
    stocks = _read(path)
    if code in stocks:
        return
    stocks.append(code)
    _write_atomic(path, stocks)


def remove(path: Path, code: str) -> None:
    """No-op if code absent."""
    stocks = _read(path)
    if code not in stocks:
        return
    stocks.remove(code)
    _write_atomic(path, stocks)
