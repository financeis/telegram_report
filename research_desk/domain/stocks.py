"""The stock list (종목표, KRX CSV): loading, lookups, search, catalog, and its version file.

One loader for every user: the tagger's KRX mapping and the web features' company list,
report pages and coverage names. File paths come in as arguments; nothing here reads settings.

Version (spec §8). Next to the CSV sits ``<csv stem>.version.json`` =
``{"version": "KRX@YYYY-MM-DD", "content_hash": "<64 lowercase hex>"}``; the date is the stock
data's as-of date. ``content_hash`` fingerprints the parsed cells, so LF/CRLF line breaks,
a BOM or different quoting give the same hash, while any changed cell or row order does not.
The repo checks the CSV out with CRLF but stores it with LF, so file bytes cannot be used.
"""
from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Optional, Union

PathLike = Union[str, os.PathLike]

HEADER: tuple[str, ...] = ("종목코드", "종목명", "시장", "산업명(대)", "산업명(중)", "주요제품")

# verify() reasons
VERSION_MISSING = "missing"    # no version file next to the CSV
VERSION_INVALID = "invalid"    # version file unreadable or not {"version": "KRX@…", "content_hash": "<hex>"}
VERSION_MISMATCH = "mismatch"  # the CSV content no longer matches the recorded hash

_VERSION_PREFIX = "KRX@"
_CODE_RE = re.compile(r"^[0-9A-Z]{6}$")
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_CELL_SEPARATOR = "\x1f"


class StockListError(ValueError):
    """The stock list or its version file cannot be used.

    Raised for a missing or unreadable CSV, an unexpected header, a bad as-of date, or a
    failed version-file write. ``str(error)`` is a Korean reason that may contain the file
    path: fine for command output on this PC, not for web responses.
    """


@dataclass(frozen=True)
class StockEntry:
    code: str
    name: str
    market: str           # KOSPI / KOSDAQ / KOSDAQ GLOBAL
    sector_major: str
    sector_minor: str
    products_text: str    # free text, e.g. "DRAM, NAND 등"


@dataclass(frozen=True)
class VersionCheck:
    """Result of comparing a loaded stock list with its version file."""
    ok: bool
    version: Optional[str]   # "version" from the version file; None when missing or invalid
    reason: Optional[str]    # None when ok, else VERSION_MISSING / VERSION_INVALID / VERSION_MISMATCH


def version_file(csv_path: PathLike) -> Path:
    """``<csv stem>.version.json`` in the CSV's folder."""
    path = Path(csv_path)
    return path.with_name(f"{path.stem}.version.json")


def content_hash(csv_path: PathLike) -> str:
    """Content hash of a CSV file (spec §8). Raises StockListError if it cannot be read."""
    return _hash_rows(_read_rows(Path(csv_path)))


def write_version(csv_path: PathLike, as_of_date: str) -> str:
    """Record the CSV's content hash with version ``KRX@<as_of_date>``; returns that version.

    Steps (spec §8): check the date (YYYY-MM-DD), check the CSV header, hash the content,
    write the version file atomically (temp file, then replace). Any failure raises
    StockListError and leaves the version file as it was. Rewriting the same date is allowed.
    """
    if not isinstance(as_of_date, str) or not _is_date(as_of_date):
        raise StockListError(f"기준일은 YYYY-MM-DD 형식의 실제 날짜여야 합니다: {as_of_date!r}")
    stock_list = StockList.load(csv_path)
    version = f"{_VERSION_PREFIX}{as_of_date}"
    payload = {"version": version, "content_hash": stock_list.content_hash}
    _replace_atomically(version_file(csv_path), json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return version


class StockList:
    """Loaded stock list. Lookups are exact: callers pad web input codes themselves."""

    def __init__(self, entries: Iterable[StockEntry], *, path: Optional[PathLike] = None,
                 content_hash: Optional[str] = None) -> None:
        self.entries: tuple[StockEntry, ...] = tuple(entries)
        self.path: Optional[Path] = Path(path) if path is not None else None
        # Hash of the loaded file content; None for a list built in memory.
        self.content_hash: Optional[str] = content_hash
        # A repeated code keeps its last row; a repeated name keeps its first (as before).
        self.by_code: dict[str, StockEntry] = {e.code: e for e in self.entries}
        self._by_name: dict[str, StockEntry] = {}
        for entry in self.entries:
            self._by_name.setdefault(_name_key(entry.name), entry)

    @classmethod
    def load(cls, csv_path: PathLike) -> "StockList":
        """Read the CSV once: header check, entries, content hash. Raises StockListError.

        Header cells lose their line breaks before the check. Rows with fewer than 6 cells
        are skipped, cells are stripped, cells after the 6th are ignored.
        """
        path = Path(csv_path)
        rows = _read_rows(path)
        header = [_header_cell(cell) for cell in rows[0]] if rows else []
        if header != list(HEADER):
            raise StockListError(f"머리줄이 다릅니다: {path} (읽은 값 {header}, 기대값 {list(HEADER)})")
        entries = [StockEntry(*(cell.strip() for cell in row[:6])) for row in rows[1:] if len(row) >= 6]
        return cls(entries, path=path, content_hash=_hash_rows(rows))

    def lookup(self, code: str) -> Optional[StockEntry]:
        return self.by_code.get(code)

    def lookup_by_name(self, name: Optional[str]) -> Optional[StockEntry]:
        """Company-name lookup ignoring whitespace and case. None on a miss."""
        if not name:
            return None
        return self._by_name.get(_name_key(name))

    def validate_code(self, code: str) -> bool:
        """A 6-character uppercase/digit code that is in the list."""
        return bool(_CODE_RE.fullmatch(code)) and code in self.by_code

    @staticmethod
    def split_products(products_text: str) -> list[str]:
        """ "MLCC, 기판, 카메라 모듈 등" → ['MLCC', '기판', '카메라 모듈']

        Splits on commas and drops a trailing " 등" (common on the last token).
        """
        out: list[str] = []
        for token in products_text.split(","):
            item = token.strip()
            if item.endswith(" 등"):
                item = item[:-2].strip()
            if not item or item == "등":
                continue
            out.append(item)
        return out

    def search(self, query: Optional[str]) -> list[StockEntry]:
        """Entries whose code starts with the query or whose name contains it (any case).

        An empty query returns every entry. File order is kept.
        """
        q = (query or "").strip()
        if not q:
            return list(self.entries)
        upper = q.upper()
        return [e for e in self.entries if e.code.startswith(q) or upper in e.name.upper()]

    def catalog(self) -> list[dict[str, str]]:
        """``{code, name, sector_major, sector_minor}`` per entry, in file order, "" when empty."""
        return [
            {"code": e.code or "", "name": e.name or "",
             "sector_major": e.sector_major or "", "sector_minor": e.sector_minor or ""}
            for e in self.entries
        ]

    def verify(self) -> VersionCheck:
        """Compare this list's content hash with the version file (read now, every call)."""
        if self.path is None or self.content_hash is None:
            return VersionCheck(ok=False, version=None, reason=VERSION_MISSING)
        try:
            data = json.loads(version_file(self.path).read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return VersionCheck(ok=False, version=None, reason=VERSION_MISSING)
        except (OSError, ValueError):   # unreadable, not UTF-8, or not JSON
            return VersionCheck(ok=False, version=None, reason=VERSION_INVALID)
        version = data.get("version") if isinstance(data, dict) else None
        recorded = data.get("content_hash") if isinstance(data, dict) else None
        if not _is_version(version) or not (isinstance(recorded, str) and _HASH_RE.fullmatch(recorded)):
            return VersionCheck(ok=False, version=None, reason=VERSION_INVALID)
        if recorded != self.content_hash:
            return VersionCheck(ok=False, version=version, reason=VERSION_MISMATCH)
        return VersionCheck(ok=True, version=version, reason=None)


def _name_key(name: str) -> str:
    return "".join(name.split()).lower()


def _read_rows(path: Path) -> list[list[str]]:
    """UTF-8 text without a leading BOM, parsed as CSV (line breaks inside quotes kept)."""
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except FileNotFoundError as exc:
        raise StockListError(f"파일이 없습니다: {path}") from exc
    except UnicodeDecodeError as exc:
        raise StockListError(f"UTF-8로 읽을 수 없습니다: {path}") from exc
    except OSError as exc:
        raise StockListError(f"파일을 열 수 없습니다: {path} ({exc})") from exc
    try:
        return list(csv.reader(io.StringIO(text, newline="")))
    except csv.Error as exc:
        raise StockListError(f"CSV로 읽을 수 없습니다: {path} ({exc})") from exc


def _header_cell(cell: str) -> str:
    return cell.replace("\r", "").replace("\n", "").strip()


def _data_cell(cell: str) -> str:
    return cell.replace("\r\n", "\n").replace("\r", "\n").strip()


def _hash_rows(rows: list[list[str]]) -> str:
    """spec §8: clean cells, join cells with \\x1f and rows with \\n, SHA-256 of the UTF-8 bytes."""
    lines = [
        _CELL_SEPARATOR.join((_header_cell if i == 0 else _data_cell)(cell) for cell in row)
        for i, row in enumerate(rows)
    ]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _is_date(value: str) -> bool:
    if not _DATE_RE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _is_version(value: object) -> bool:
    return (isinstance(value, str) and value.startswith(_VERSION_PREFIX)
            and _is_date(value[len(_VERSION_PREFIX):]))


def _replace_atomically(target: Path, text: str) -> None:
    """Write ``text`` to a temp file next to ``target``, then replace ``target`` with it."""
    try:
        fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    except OSError as exc:
        raise StockListError(f"버전 정보 파일을 쓸 수 없습니다: {target} ({exc})") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException as exc:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        if isinstance(exc, OSError):
            raise StockListError(f"버전 정보 파일을 쓸 수 없습니다: {target} ({exc})") from exc
        raise
