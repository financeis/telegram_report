"""Companies work: the stock list for the web app and the favorite companies.

Readiness (spec §9.9). The stock list is prepared on first use: ``core.settings.load_env()``,
then ``domain.stocks.StockList.load(core.settings.krx_csv_path())``. A file that cannot be read
raises ``NotReady("기업 목록", "종목표 파일을 읽을 수 없습니다")`` and the next request prepares
again, so filling in the file or adding ``KRX_CSV_PATH`` to ``.env`` recovers without a restart.
Once prepared, the list is kept for the life of the process. A content hash that differs from
the version file, or a missing version file, only logs a warning (spec §8): the web app shows
names and never stores the version.

Favorites (spec §9.4). One JSON file, by default ``~/.review_viewer/favorites.json``; tests pass
their own path. Turning a favorite on or off first looks the received code up in the stock list,
zero-padded to 6 digits (spec §6), then stores the code as received. So a code that has left the
stock list cannot be removed either (known issue, kept). Changes run one at a time under a lock.
No DB or AI settings are needed.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from threading import Lock
from typing import Optional, Union

from fastapi import HTTPException

from research_desk.core import settings
from research_desk.domain.stocks import (
    VERSION_INVALID,
    VERSION_MISMATCH,
    VERSION_MISSING,
    StockList,
    StockListError,
)

from . import favorites

logger = logging.getLogger(__name__)

PathLike = Union[str, os.PathLike]

AREA = "기업 목록"
STOCK_LIST_UNREADABLE = "종목표 파일을 읽을 수 없습니다"
STOCK_NOT_FOUND = "종목을 찾을 수 없습니다."
CODE_WIDTH = 6

_VERSION_PROBLEMS = {
    VERSION_MISSING: "종목표 버전 정보 파일이 없습니다",
    VERSION_MISMATCH: "종목표 파일 내용이 버전 정보와 다릅니다",
    VERSION_INVALID: "종목표 버전 정보 파일을 읽을 수 없습니다",
}


def default_favorites_path() -> Path:
    """``~/.review_viewer/favorites.json``: the web app's favorites file (spec §9.4, §11)."""
    return Path.home() / ".review_viewer" / "favorites.json"


class CompaniesService:
    """The company list and the favorites for one web app process.

    ``favorites_path`` defaults to ``default_favorites_path()``; tests pass a temp path so they
    never use the real file. Creating the service reads nothing; the first request prepares.
    """

    def __init__(self, favorites_path: Optional[PathLike] = None) -> None:
        self.favorites_path = Path(favorites_path) if favorites_path is not None else default_favorites_path()
        self.favorite_lock = Lock()
        self._stocks: Optional[StockList] = None
        self._prepare_lock = Lock()

    def stock_list(self) -> StockList:
        """The prepared stock list. Prepares it now if needed; NotReady when that fails."""
        stocks = self._stocks
        if stocks is None:
            with self._prepare_lock:
                stocks = self._stocks
                if stocks is None:
                    stocks = self._stocks = load_stock_list()
        return stocks

    def bootstrap(self) -> dict:
        """``GET /api/workspace``: the whole stock list in file order, and the favorites."""
        return {"stocks": self.stock_list().catalog(), "favorites": favorites.load(self.favorites_path)}

    def set_favorite(self, code: str, enabled: bool) -> dict:
        """``PUT /api/favorites/{code}``: turn one favorite on or off; returns the favorites."""
        if self.stock_list().lookup(code.zfill(CODE_WIDTH)) is None:
            raise HTTPException(404, STOCK_NOT_FOUND)
        with self.favorite_lock:
            (favorites.add if enabled else favorites.remove)(self.favorites_path, code)
            return {"favorites": favorites.load(self.favorites_path)}


def load_stock_list() -> StockList:
    """Re-read ``.env``, then load the stock list from ``KRX_CSV_PATH``.

    Raises NotReady when the file cannot be read; a version problem only logs a warning.
    """
    settings.load_env()
    path = settings.krx_csv_path()
    try:
        stocks = StockList.load(path)
    except StockListError as exc:
        # The response carries a fixed sentence; the path and the cause go to the local log only.
        logger.warning("기업 목록을 준비하지 못했습니다. 다음 요청 때 다시 시도합니다: %s", exc)
        raise settings.NotReady(AREA, STOCK_LIST_UNREADABLE) from exc
    check = stocks.verify()
    if not check.ok:
        logger.warning(
            "%s: %s. 기업 목록은 경고만 남기고 그대로 동작합니다. 종목표를 바꿨다면 "
            "python -m research_desk stocks set-version --as-of <자료 기준일, YYYY-MM-DD>를 실행하세요.",
            _VERSION_PROBLEMS.get(check.reason, check.reason), path,
        )
    return stocks


_service: Optional[CompaniesService] = None
_service_lock = Lock()


def get_service() -> CompaniesService:
    """The process-wide service, created on first use (creating it reads nothing).

    The router depends on this. Tests override it with their own ``CompaniesService``
    (``app.dependency_overrides[get_service]``) so they never use the real favorites file.
    """
    global _service
    with _service_lock:
        if _service is None:
            _service = CompaniesService()
        return _service
