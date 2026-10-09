"""research_desk.domain.stocks: stock list loader, lookups, search, catalog, version file.

Ported from langgraph_tagger/tests/test_krx.py (KRXIndex on the bundled CSV) and
langgraph_tagger/analytics/tests/test_krx.py (pandas loader on a small fixture CSV).
New: the content hash (spec §8), bundled CSV vs its version file, verify(), write_version().
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date
from pathlib import Path

import pytest

from research_desk.domain import stocks
from research_desk.domain.stocks import (
    HEADER,
    StockEntry,
    StockList,
    StockListError,
    VersionCheck,
    content_hash,
    version_file,
    write_version,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
BUNDLED_CSV = REPO_ROOT / "docs" / "stock_data" / "KRX_stocks_data.csv"

# The first header cell has a line break inside quotes, like the real file.
HEADER_LINE = '"종목\n코드",종목명,시장,산업명(대),산업명(중),주요제품\n'
# Same rows as the old analytics test fixture.
SMALL_CSV = HEADER_LINE + (
    '005930,삼성전자,KOSPI,반도체,메모리반도체,DRAM/NAND\n'
    '000660,SK하이닉스,KOSPI,반도체,메모리반도체,DRAM/NAND\n'
    '373220,LG에너지솔루션,KOSPI,2차전지,셀,리튬이온배터리\n'
    '035720,카카오,KOSPI,IT,플랫폼,메신저\n'
    '042660,한화오션,KOSPI,조선,상선,LNG선\n'
)


def write_csv(path: Path, text: str, *, newline: str = "\n", bom: bool = False) -> Path:
    """Write ``text`` as UTF-8 bytes with the given line breaks (also inside quoted cells)."""
    data = text.replace("\n", newline).encode("utf-8")
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + data)
    return path


def names_in(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


@pytest.fixture(scope="module")
def krx() -> StockList:
    """The bundled stock list, loaded once for this module."""
    return StockList.load(BUNDLED_CSV)


@pytest.fixture
def small_csv(tmp_path) -> Path:
    return write_csv(tmp_path / "krx_test.csv", SMALL_CSV)


# --- bundled CSV (ported from langgraph_tagger/tests/test_krx.py) -----------------------------

class TestBundledLoad:
    def test_csv_loads_with_normalized_header(self, krx):
        # Sanity: the KRX listed pool is ~2,559 rows.
        assert len(krx.by_code) >= 2000
        assert len(krx.entries) == len(krx.catalog())

    def test_known_code_present(self, krx):
        # 005930 is Samsung Electronics, always listed.
        assert krx.validate_code("005930")
        entry = krx.lookup("005930")
        assert entry is not None
        assert "삼성전자" in entry.name

    def test_catalog_starts_in_file_order(self, krx):
        first = krx.catalog()[0]
        assert first["code"] == "005930"
        assert list(first) == ["code", "name", "sector_major", "sector_minor"]


class TestValidate:
    def test_alphanumeric_six(self, tmp_path):
        # SPAC/listing-pending codes use letters, e.g. 0008Z0.
        assert re.fullmatch(r"[0-9A-Z]{6}", "0008Z0")
        csv_path = write_csv(tmp_path / "spac.csv", HEADER_LINE + "0008Z0,스팩,KOSDAQ,금융,스팩,합병\n")
        listed = StockList.load(csv_path)
        assert listed.validate_code("0008Z0")
        assert not listed.validate_code("0008z0")

    def test_too_short_rejected(self, krx):
        assert not krx.validate_code("12345")

    def test_lowercase_rejected(self, krx):
        # ^[0-9A-Z]{6}$ — uppercase only
        assert not krx.validate_code("a12345")

    def test_unknown_six_digit_rejected(self, krx):
        assert not krx.validate_code("999999")

    def test_trailing_newline_rejected(self, krx):
        assert not krx.validate_code("005930\n")


class TestSplitProducts:
    def test_simple_split(self, krx):
        assert krx.split_products("DRAM, NAND 등") == ["DRAM", "NAND"]

    def test_drops_deung(self, krx):
        assert krx.split_products("MLCC, 기판, 카메라 모듈 등") == ["MLCC", "기판", "카메라 모듈"]

    def test_no_deung(self, krx):
        assert krx.split_products("정유") == ["정유"]

    def test_empty(self, krx):
        assert krx.split_products("") == []

    def test_lone_deung_and_empty_tokens_dropped(self):
        assert StockList.split_products("DRAM, , 등") == ["DRAM"]


def test_lookup_by_name_exact_match(krx):
    entry = krx.lookup_by_name("삼성전자")
    assert entry is not None
    assert entry.code == "005930"


def test_lookup_by_name_whitespace_insensitive(krx):
    entry = krx.lookup_by_name("삼성 전자")
    assert entry is not None
    assert entry.code == "005930"


def test_lookup_by_name_case_insensitive(krx, tmp_path):
    # The bundled CSV uses Korean names, so an English name misses.
    assert krx.lookup_by_name("Samsung Electronics") is None
    listed = StockList.load(write_csv(tmp_path / "en.csv", HEADER_LINE + "123450,NAVER Corp,KOSPI,IT,포털,검색\n"))
    assert listed.lookup_by_name("naver corp").code == "123450"
    assert listed.lookup_by_name("NAVERCORP").code == "123450"


def test_lookup_by_name_unknown_returns_none(krx):
    assert krx.lookup_by_name("존재하지않는회사") is None
    assert krx.lookup_by_name("") is None
    assert krx.lookup_by_name(None) is None


def test_v1_helpers_and_mtime_version_are_gone(krx):
    assert not hasattr(krx, "has_product")
    assert not hasattr(krx, "rows_with_product")
    assert not hasattr(krx, "rows_with_sector_minor")
    assert not hasattr(krx, "fuzzy_sector_match")
    # The version now comes from the version file, not the CSV modification time.
    assert not hasattr(krx, "taxonomy_version")


# --- small fixture CSV (ported from langgraph_tagger/analytics/tests/test_krx.py) ---------------

def test_load_returns_entries_with_known_fields(small_csv):
    listed = StockList.load(small_csv)
    assert len(listed.entries) == 5
    assert listed.entries[0] == StockEntry(
        code="005930", name="삼성전자", market="KOSPI",
        sector_major="반도체", sector_minor="메모리반도체", products_text="DRAM/NAND",
    )


def test_search_by_code_prefix(small_csv):
    codes = [e.code for e in StockList.load(small_csv).search("005")]
    assert "005930" in codes
    assert "000660" not in codes


def test_search_by_name_substring(small_csv):
    codes = [e.code for e in StockList.load(small_csv).search("삼성")]
    assert "005930" in codes


def test_search_name_is_case_insensitive(small_csv):
    listed = StockList.load(small_csv)
    assert [e.code for e in listed.search("sk")] == ["000660"]
    assert [e.code for e in listed.search("lg에너지")] == ["373220"]


@pytest.mark.parametrize("query", ["", None, "   "])
def test_search_empty_query_returns_all(small_csv, query):
    listed = StockList.load(small_csv)
    assert listed.search(query) == list(listed.entries)
    assert len(listed.search(query)) == 5


def test_search_strips_the_query_and_keeps_file_order(small_csv):
    listed = StockList.load(small_csv)
    assert [e.code for e in listed.search(" 005930 ")] == ["005930"]
    assert [e.code for e in listed.search("0")] == ["005930", "000660", "035720", "042660"]


def test_lookup_returns_entry(small_csv):
    entry = StockList.load(small_csv).lookup("005930")
    assert (entry.code, entry.name, entry.sector_major, entry.sector_minor) == (
        "005930", "삼성전자", "반도체", "메모리반도체",
    )


def test_lookup_missing_returns_none(small_csv):
    assert StockList.load(small_csv).lookup("999999") is None


def test_lookup_is_exact_without_zero_padding(small_csv):
    listed = StockList.load(small_csv)
    assert listed.lookup("5930") is None
    assert not listed.validate_code("5930")


def test_load_missing_file(tmp_path):
    with pytest.raises(StockListError, match="파일이 없습니다"):
        StockList.load(tmp_path / "nope.csv")


# --- loader rules --------------------------------------------------------------------------------

def test_header_constant():
    assert HEADER == ("종목코드", "종목명", "시장", "산업명(대)", "산업명(중)", "주요제품")


@pytest.mark.parametrize("first_cell", ['"종목\n코드"', '"종목\r\n코드"', '"종목\r코드"', "종목코드", '" 종목코드 "'])
def test_header_line_breaks_and_spaces_are_normalized(tmp_path, first_cell):
    text = first_cell + ",종목명,시장,산업명(대),산업명(중),주요제품\n005930,삼성전자,KOSPI,반도체,메모리반도체,DRAM\n"
    path = tmp_path / "h.csv"
    path.write_bytes(text.encode("utf-8"))
    assert [e.code for e in StockList.load(path).entries] == ["005930"]


@pytest.mark.parametrize("header", [
    "종목코드,회사명,시장,산업명(대),산업명(중),주요제품",
    "종목코드,종목명,시장,산업명(대),산업명(중)",
    "종목코드,종목명,시장,산업명(대),산업명(중),주요제품,비고",
    "종목명,종목코드,시장,산업명(대),산업명(중),주요제품",
    "",
])
def test_wrong_header_is_rejected(tmp_path, header):
    path = write_csv(tmp_path / "bad.csv", header + "\n005930,삼성전자,KOSPI,반도체,메모리반도체,DRAM\n")
    with pytest.raises(StockListError, match="머리줄"):
        StockList.load(path)


def test_empty_file_is_rejected(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_bytes(b"")
    with pytest.raises(StockListError, match="머리줄"):
        StockList.load(path)


def test_short_rows_skipped_cells_stripped_extra_cells_ignored(tmp_path):
    text = HEADER_LINE + (
        ' 005930 , 삼성전자 ,KOSPI, 반도체 ,메모리반도체,"DRAM, NAND 등 "\n'
        '000660,SK하이닉스,KOSPI\n'
        '\n'
        '035720,카카오,KOSPI,IT,플랫폼,메신저,extra\n'
    )
    listed = StockList.load(write_csv(tmp_path / "rows.csv", text))
    assert listed.entries == (
        StockEntry("005930", "삼성전자", "KOSPI", "반도체", "메모리반도체", "DRAM, NAND 등"),
        StockEntry("035720", "카카오", "KOSPI", "IT", "플랫폼", "메신저"),
    )


def test_non_utf8_file_is_rejected(tmp_path):
    path = tmp_path / "cp949.csv"
    path.write_bytes(SMALL_CSV.encode("cp949"))
    with pytest.raises(StockListError, match="UTF-8"):
        StockList.load(path)


def test_directory_is_rejected(tmp_path):
    with pytest.raises(StockListError):
        StockList.load(tmp_path)


def test_lookup_by_name_first_match_wins(tmp_path):
    text = HEADER_LINE + "111110,삼성 전자,KOSPI,a,b,c\n005930,삼성전자,KOSPI,반도체,메모리반도체,DRAM\n"
    listed = StockList.load(write_csv(tmp_path / "dup.csv", text))
    assert listed.lookup_by_name("삼성전자").code == "111110"


def test_duplicate_code_lookup_keeps_the_last_row_like_the_old_index(tmp_path):
    text = HEADER_LINE + "005930,첫째,KOSPI,a,b,c\n005930,둘째,KOSPI,d,e,f\n"
    listed = StockList.load(write_csv(tmp_path / "dupcode.csv", text))
    assert listed.lookup("005930").name == "둘째"
    assert [row["name"] for row in listed.catalog()] == ["첫째", "둘째"]


# --- catalog -----------------------------------------------------------------------------------

def test_catalog_lists_every_row_in_file_order(small_csv):
    assert StockList.load(small_csv).catalog() == [
        {"code": "005930", "name": "삼성전자", "sector_major": "반도체", "sector_minor": "메모리반도체"},
        {"code": "000660", "name": "SK하이닉스", "sector_major": "반도체", "sector_minor": "메모리반도체"},
        {"code": "373220", "name": "LG에너지솔루션", "sector_major": "2차전지", "sector_minor": "셀"},
        {"code": "035720", "name": "카카오", "sector_major": "IT", "sector_minor": "플랫폼"},
        {"code": "042660", "name": "한화오션", "sector_major": "조선", "sector_minor": "상선"},
    ]


def test_catalog_uses_empty_string_for_missing_values(tmp_path):
    listed = StockList.load(write_csv(tmp_path / "empty_cells.csv", HEADER_LINE + "005930,삼성전자,KOSPI,,,DRAM\n"))
    assert listed.catalog() == [{"code": "005930", "name": "삼성전자", "sector_major": "", "sector_minor": ""}]
    in_memory = StockList([StockEntry("005930", "삼성전자", "KOSPI", None, None, "")])
    assert in_memory.catalog() == [{"code": "005930", "name": "삼성전자", "sector_major": "", "sector_minor": ""}]


def test_catalog_returns_fresh_dicts(small_csv):
    listed = StockList.load(small_csv)
    listed.catalog()[0]["name"] = "바뀜"
    assert listed.catalog()[0]["name"] == "삼성전자"


# --- content hash (spec §8) ------------------------------------------------------------------------

def test_hash_is_lowercase_sha256_hex(small_csv):
    assert re.fullmatch(r"[0-9a-f]{64}", content_hash(small_csv))


def test_hash_follows_the_spec_algorithm_exactly(tmp_path):
    path = tmp_path / "algo.csv"
    path.write_bytes('﻿"A\r\nB", b \r\n"x\r\ny", z \r\n"p\rq",r\r\n'.encode("utf-8"))
    # header cells lose every line break; other cells get \n line breaks; all cells are stripped;
    # cells joined with \x1f, rows with \n; SHA-256 of the UTF-8 bytes.
    expected = "AB\x1fb\nx\ny\x1fz\np\nq\x1fr"
    assert content_hash(path) == hashlib.sha256(expected.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("newline, bom", [
    ("\n", False), ("\r\n", False), ("\n", True), ("\r\n", True), ("\r", False),
])
def test_hash_ignores_line_endings_and_bom(tmp_path, newline, bom):
    reference = content_hash(write_csv(tmp_path / "lf.csv", SMALL_CSV))
    variant = write_csv(tmp_path / "variant.csv", SMALL_CSV, newline=newline, bom=bom)
    assert content_hash(variant) == reference


def test_hash_ignores_quoting_and_surrounding_spaces(tmp_path):
    plain = write_csv(tmp_path / "plain.csv", HEADER_LINE + "005930,삼성전자,KOSPI,반도체,메모리반도체,DRAM\n")
    quoted = write_csv(tmp_path / "quoted.csv",
                       HEADER_LINE + '"005930","삼성전자", KOSPI ,"반도체 ",메모리반도체,"DRAM"\n')
    assert content_hash(plain) == content_hash(quoted)


def test_hash_ignores_the_header_line_break_style(tmp_path):
    body = ",종목명,시장,산업명(대),산업명(중),주요제품\n005930,삼성전자,KOSPI,반도체,메모리반도체,DRAM\n"
    hashes = set()
    for i, first_cell in enumerate(['"종목\n코드"', '"종목\r\n코드"', "종목코드"]):
        path = tmp_path / f"h{i}.csv"
        path.write_bytes((first_cell + body).encode("utf-8"))
        hashes.add(content_hash(path))
    assert len(hashes) == 1


def test_hash_normalizes_line_breaks_inside_data_cells(tmp_path):
    def digest(cell: str, name: str) -> str:
        path = tmp_path / name
        path.write_bytes((HEADER_LINE + f'005930,삼성전자,KOSPI,반도체,메모리반도체,"{cell}"\n').encode("utf-8"))
        return content_hash(path)

    assert digest("DRAM\r\nNAND", "crlf.csv") == digest("DRAM\nNAND", "lf.csv") == digest("DRAM\rNAND", "cr.csv")
    assert digest("DRAM NAND", "space.csv") != digest("DRAM\nNAND", "lf2.csv")


def test_one_changed_cell_changes_the_hash(tmp_path):
    base = content_hash(write_csv(tmp_path / "a.csv", SMALL_CSV))
    changed = content_hash(write_csv(tmp_path / "b.csv", SMALL_CSV.replace("LNG선", "LNG운반선")))
    assert changed != base


def test_row_order_changes_the_hash(tmp_path):
    lines = SMALL_CSV.splitlines(keepends=True)
    # lines[0] and lines[1] together hold the quoted two-line header cell; swap rows 2 and 3.
    swapped = "".join(lines[:3] + [lines[4], lines[3]] + lines[5:])
    assert sorted(swapped.splitlines()) == sorted(SMALL_CSV.splitlines())
    assert content_hash(write_csv(tmp_path / "a.csv", SMALL_CSV)) != content_hash(
        write_csv(tmp_path / "b.csv", swapped))


def test_hash_of_a_missing_file_raises(tmp_path):
    with pytest.raises(StockListError, match="파일이 없습니다"):
        content_hash(tmp_path / "nope.csv")


def test_loaded_list_carries_the_same_hash(small_csv):
    assert StockList.load(small_csv).content_hash == content_hash(small_csv)


# --- version file ------------------------------------------------------------------------------------

def test_version_file_sits_next_to_the_csv():
    assert version_file(Path("docs/stock_data/KRX_stocks_data.csv")) == Path(
        "docs/stock_data/KRX_stocks_data.version.json")
    assert version_file("a/b.c.csv") == Path("a/b.c.version.json")


def test_bundled_csv_matches_its_version_file():
    # The version file decides the bundled version, so replacing the CSV and running
    # `stocks set-version` needs no change here; the content hash still binds the two files.
    data = json.loads(version_file(BUNDLED_CSV).read_text(encoding="utf-8"))
    assert data == {"version": data["version"], "content_hash": content_hash(BUNDLED_CSV)}
    assert re.fullmatch(r"[0-9a-f]{64}", data["content_hash"])
    check = StockList.load(BUNDLED_CSV).verify()
    assert check == VersionCheck(ok=True, version=data["version"], reason=None)
    # KRX@YYYY-MM-DD (ported from test_taxonomy_version_format)
    assert check.version.startswith("KRX@")
    date.fromisoformat(check.version.removeprefix("KRX@"))


def test_bundled_hash_is_the_same_for_lf_and_crlf_checkouts(tmp_path):
    recorded = json.loads(version_file(BUNDLED_CSV).read_text(encoding="utf-8"))["content_hash"]
    raw = BUNDLED_CSV.read_bytes()
    lf = raw.replace(b"\r\n", b"\n")
    variants = {"lf.csv": lf, "crlf.csv": lf.replace(b"\n", b"\r\n"), "nobom.csv": lf.removeprefix(b"\xef\xbb\xbf")}
    for name, data in variants.items():
        path = tmp_path / name
        path.write_bytes(data)
        assert content_hash(path) == recorded, name


def test_verify_reports_a_missing_version_file(small_csv):
    assert StockList.load(small_csv).verify() == VersionCheck(ok=False, version=None, reason="missing")


def test_verify_reports_a_mismatch_after_the_csv_changes(small_csv):
    write_version(small_csv, "2026-05-08")
    write_csv(small_csv, SMALL_CSV.replace("LNG선", "LNG운반선"))
    assert StockList.load(small_csv).verify() == VersionCheck(
        ok=False, version="KRX@2026-05-08", reason="mismatch")


@pytest.mark.parametrize("content", [
    "not json",
    "[]",
    '{"version": "KRX@2026-05-08"}',
    '{"content_hash": "<HASH>"}',
    '{"version": "2026-05-08", "content_hash": "<HASH>"}',
    '{"version": "KRX@2026-02-30", "content_hash": "<HASH>"}',
    '{"version": "KRX@2026-5-8", "content_hash": "<HASH>"}',
    '{"version": null, "content_hash": "<HASH>"}',
    '{"version": "KRX@2026-05-08", "content_hash": "<HASH_UPPER>"}',
    '{"version": "KRX@2026-05-08", "content_hash": "abc"}',
])
def test_verify_reports_an_invalid_version_file(small_csv, content):
    digest = content_hash(small_csv)
    text = content.replace("<HASH_UPPER>", digest.upper()).replace("<HASH>", digest)
    version_file(small_csv).write_text(text, encoding="utf-8")
    assert StockList.load(small_csv).verify() == VersionCheck(ok=False, version=None, reason="invalid")


def test_verify_accepts_a_version_file_saved_with_bom(small_csv):
    payload = {"version": "KRX@2026-05-08", "content_hash": content_hash(small_csv)}
    version_file(small_csv).write_bytes(b"\xef\xbb\xbf" + json.dumps(payload).encode("utf-8"))
    assert StockList.load(small_csv).verify() == VersionCheck(ok=True, version="KRX@2026-05-08", reason=None)


def test_verify_rereads_the_version_file(small_csv):
    listed = StockList.load(small_csv)
    assert listed.verify().reason == "missing"
    write_version(small_csv, "2026-05-08")
    assert listed.verify() == VersionCheck(ok=True, version="KRX@2026-05-08", reason=None)


def test_version_ignores_the_csv_modification_time(small_csv):
    write_version(small_csv, "2026-05-08")
    os.utime(small_csv, (0, 0))
    assert StockList.load(small_csv).verify().version == "KRX@2026-05-08"


def test_in_memory_list_has_no_version():
    listed = StockList([StockEntry("005930", "삼성전자", "KOSPI", "반도체", "메모리반도체", "DRAM")])
    assert listed.path is None
    assert listed.lookup("005930").name == "삼성전자"
    assert listed.verify() == VersionCheck(ok=False, version=None, reason="missing")


# --- write_version -----------------------------------------------------------------------------

def test_write_version_writes_version_and_hash(small_csv):
    assert write_version(small_csv, "2026-05-08") == "KRX@2026-05-08"
    data = json.loads(version_file(small_csv).read_text(encoding="utf-8"))
    assert data == {"version": "KRX@2026-05-08", "content_hash": content_hash(small_csv)}
    assert StockList.load(small_csv).verify() == VersionCheck(ok=True, version="KRX@2026-05-08", reason=None)
    assert names_in(small_csv.parent) == ["krx_test.csv", "krx_test.version.json"]


def test_write_version_accepts_a_string_path(small_csv):
    assert write_version(str(small_csv), "2026-05-08") == "KRX@2026-05-08"
    assert version_file(small_csv).exists()


def test_write_version_can_rewrite_the_same_or_a_new_date(small_csv):
    assert write_version(small_csv, "2026-05-08") == "KRX@2026-05-08"
    assert write_version(small_csv, "2026-05-08") == "KRX@2026-05-08"
    write_csv(small_csv, SMALL_CSV.replace("LNG선", "LNG운반선"))
    assert write_version(small_csv, "2026-06-01") == "KRX@2026-06-01"
    assert StockList.load(small_csv).verify() == VersionCheck(ok=True, version="KRX@2026-06-01", reason=None)
    assert names_in(small_csv.parent) == ["krx_test.csv", "krx_test.version.json"]


BAD_DATES = [
    "2026/05/08", "20260508", "2026-5-8", "2026-02-30", "2026-13-01", "", " 2026-05-08",
    "2026-05-08\n", "２０２６-05-08", "KRX@2026-05-08", "2026-05-08T00:00", None, 20260508,
]


@pytest.mark.parametrize("bad", BAD_DATES)
def test_write_version_rejects_a_bad_date_and_keeps_the_file(small_csv, bad):
    write_version(small_csv, "2026-05-08")
    before = version_file(small_csv).read_bytes()
    with pytest.raises(StockListError, match="기준일"):
        write_version(small_csv, bad)
    assert version_file(small_csv).read_bytes() == before
    assert names_in(small_csv.parent) == ["krx_test.csv", "krx_test.version.json"]


def test_write_version_checks_the_date_before_the_csv(tmp_path):
    with pytest.raises(StockListError, match="기준일"):
        write_version(tmp_path / "missing.csv", "2026/05/08")


def test_write_version_rejects_a_bad_header_and_keeps_the_file(small_csv):
    write_version(small_csv, "2026-05-08")
    before = version_file(small_csv).read_bytes()
    write_csv(small_csv, SMALL_CSV.replace("종목명", "회사명"))
    with pytest.raises(StockListError, match="머리줄"):
        write_version(small_csv, "2026-06-01")
    assert version_file(small_csv).read_bytes() == before
    assert names_in(small_csv.parent) == ["krx_test.csv", "krx_test.version.json"]


def test_write_version_without_a_csv_creates_nothing(tmp_path):
    missing = tmp_path / "nope.csv"
    with pytest.raises(StockListError, match="파일이 없습니다"):
        write_version(missing, "2026-05-08")
    assert names_in(tmp_path) == []


def test_write_version_failed_replace_keeps_the_old_file_and_cleans_up(small_csv, monkeypatch):
    write_version(small_csv, "2026-05-08")
    before = version_file(small_csv).read_bytes()
    write_csv(small_csv, SMALL_CSV.replace("LNG선", "LNG운반선"))

    def refuse(src, dst):
        raise PermissionError("locked by another program")

    monkeypatch.setattr(stocks.os, "replace", refuse)
    with pytest.raises(StockListError, match="버전 정보 파일"):
        write_version(small_csv, "2026-06-01")
    assert version_file(small_csv).read_bytes() == before
    assert names_in(small_csv.parent) == ["krx_test.csv", "krx_test.version.json"]
