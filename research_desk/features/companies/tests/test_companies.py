"""features.companies: GET /api/workspace and PUT /api/favorites/{code} (spec §6, §9.4, §9.9).

Ported from langgraph_tagger/workspace/tests/test_workspace.py: the bootstrap part of
test_routes_and_explicit_analysis and the favorites response of test_cross_origin_writes_are_rejected
(the Origin check itself belongs to the web app). The old tests used a stub service; these run the
real service on a temp stock list and a temp favorites file.

New: the zero-padded existence check, the known issue for codes that left the stock list, NotReady
and the retry on the next request, the .env re-read, version warnings, no DB or AI settings needed,
the default favorites location, the real favorites file never touched, changes one at a time.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from research_desk.core.settings import NotReady
from research_desk.domain.stocks import version_file, write_version
from research_desk.features.companies import favorites, router
from research_desk.features.companies import service as service_module
from research_desk.features.companies.service import CompaniesService, default_favorites_path, get_service

# The first header cell has a line break inside quotes, like the real file.
HEADER_LINE = '"종목\n코드",종목명,시장,산업명(대),산업명(중),주요제품\n'
ROWS = (
    "005930,삼성전자,KOSPI,반도체,메모리반도체,DRAM/NAND\n"
    "000660,SK하이닉스,KOSPI,반도체,메모리반도체,DRAM/NAND\n"
    "016360,삼성증권,KOSPI,금융,증권,증권\n"
    "000020,동화약품,KOSPI,,,의약품\n"
)
CATALOG = [
    {"code": "005930", "name": "삼성전자", "sector_major": "반도체", "sector_minor": "메모리반도체"},
    {"code": "000660", "name": "SK하이닉스", "sector_major": "반도체", "sector_minor": "메모리반도체"},
    {"code": "016360", "name": "삼성증권", "sector_major": "금융", "sector_minor": "증권"},
    {"code": "000020", "name": "동화약품", "sector_major": "", "sector_minor": ""},
]
NOT_READY = {"detail": "기업 목록 기능을 지금 쓸 수 없습니다: 종목표 파일을 읽을 수 없습니다"}
NOT_FOUND = {"detail": "종목을 찾을 수 없습니다."}
LOGGER = "research_desk.features.companies.service"


def write_stock_csv(path: Path, text: str = HEADER_LINE + ROWS) -> Path:
    path.write_bytes(text.encode("utf-8"))
    return path


def saved(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def build_app(service: CompaniesService) -> FastAPI:
    """This feature's router plus the NotReady → 503 answer the web app adds (spec §6)."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_service] = lambda: service

    @app.exception_handler(NotReady)
    async def not_ready(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=503)

    return app


@pytest.fixture
def stock_csv(tmp_path, monkeypatch) -> Path:
    """A small stock list with a matching version file, at KRX_CSV_PATH."""
    path = write_stock_csv(tmp_path / "krx.csv")
    write_version(path, "2026-05-08")
    monkeypatch.setenv("KRX_CSV_PATH", str(path))
    return path


@pytest.fixture
def favorites_path(tmp_path) -> Path:
    return tmp_path / "favorites" / "favorites.json"


@pytest.fixture
def service(favorites_path) -> CompaniesService:
    return CompaniesService(favorites_path=favorites_path)


@pytest.fixture
def client(service):
    with TestClient(build_app(service)) as test_client:
        yield test_client


def put(client: TestClient, code: str, enabled: bool):
    return client.put(f"/api/favorites/{code}", json={"enabled": enabled})


def write_favorites(path: Path, codes: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"stocks": codes}), encoding="utf-8")


# --- ported from the old web tests -----------------------------------------------------------

def test_workspace_with_an_empty_stock_list_and_no_favorites(client, tmp_path, monkeypatch, favorites_path):
    # test_routes_and_explicit_analysis (bootstrap part)
    monkeypatch.setenv("KRX_CSV_PATH", str(write_stock_csv(tmp_path / "header_only.csv", HEADER_LINE)))
    assert client.get("/api/workspace").json() == {"stocks": [], "favorites": []}
    assert not favorites_path.exists()


def test_turning_a_favorite_on_returns_the_favorites(client, stock_csv, favorites_path):
    # test_cross_origin_writes_are_rejected (favorites response; the Origin check is the web app's)
    response = put(client, "016360", True)
    assert response.status_code == 200
    assert response.json() == {"favorites": ["016360"]}
    assert saved(favorites_path) == {"stocks": ["016360"]}


# --- addresses ---------------------------------------------------------------------------------

def test_the_router_serves_exactly_the_two_addresses():
    assert sorted((sorted(r.methods), r.path) for r in router.routes) == [
        (["GET"], "/api/workspace"),
        (["PUT"], "/api/favorites/{code}"),
    ]


def test_workspace_lists_the_stock_list_in_file_order_and_the_favorites(client, stock_csv, favorites_path):
    write_favorites(favorites_path, ["016360", "005930"])
    assert client.get("/api/workspace").json() == {"stocks": CATALOG, "favorites": ["016360", "005930"]}


def test_favorites_on_and_off_keep_order_and_ignore_repeats(client, stock_csv, favorites_path):
    assert put(client, "005930", True).json() == {"favorites": ["005930"]}
    assert put(client, "000660", True).json() == {"favorites": ["005930", "000660"]}
    assert put(client, "005930", True).json() == {"favorites": ["005930", "000660"]}
    assert put(client, "005930", False).json() == {"favorites": ["000660"]}
    assert put(client, "016360", False).json() == {"favorites": ["000660"]}   # not a favorite: no change
    assert saved(favorites_path) == {"stocks": ["000660"]}
    assert client.get("/api/workspace").json()["favorites"] == ["000660"]


def test_a_five_digit_code_is_zero_padded_for_the_existence_check(client, stock_csv, favorites_path):
    # Spec §6: only the stock list check pads; the code is stored as received (as before).
    response = put(client, "16360", True)
    assert response.status_code == 200
    assert response.json() == {"favorites": ["16360"]}
    assert put(client, "16360", False).json() == {"favorites": []}


@pytest.mark.parametrize("code", ["999999", "16361", "0016360", "abc"])
def test_a_code_not_in_the_stock_list_is_404_and_changes_nothing(client, stock_csv, favorites_path, code):
    for enabled in (True, False):
        response = put(client, code, enabled)
        assert response.status_code == 404
        assert response.json() == NOT_FOUND
    assert not favorites_path.exists()


def test_a_code_that_left_the_stock_list_cannot_be_removed(client, stock_csv, favorites_path):
    # Spec §9.4 known issue, kept: turning off also checks the stock list first.
    write_favorites(favorites_path, ["123450", "005930"])
    response = put(client, "123450", False)
    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    assert saved(favorites_path) == {"stocks": ["123450", "005930"]}
    assert client.get("/api/workspace").json()["favorites"] == ["123450", "005930"]


@pytest.mark.parametrize("body", [{}, {"enabled": "maybe"}, {"enabled": None}])
def test_a_bad_body_is_422_and_changes_nothing(client, stock_csv, favorites_path, body):
    assert client.put("/api/favorites/005930", json=body).status_code == 422
    assert not favorites_path.exists()


def test_a_corrupt_favorites_file_is_backed_up_and_read_as_empty(client, stock_csv, favorites_path):
    favorites_path.parent.mkdir(parents=True)
    favorites_path.write_text("not valid json {", encoding="utf-8")
    assert client.get("/api/workspace").json()["favorites"] == []
    assert favorites_path.with_suffix(".json.bak").read_text(encoding="utf-8") == "not valid json {"
    assert put(client, "005930", True).json() == {"favorites": ["005930"]}


def test_needs_no_db_or_ai_settings(client, stock_csv, monkeypatch):
    for name in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "SUPABASE_DB_URL",
                 "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert client.get("/api/workspace").json()["stocks"] == CATALOG
    assert put(client, "005930", True).status_code == 200


# --- readiness (spec §9.9) ---------------------------------------------------------------------

UNREADABLE = {
    "missing": lambda path: None,
    "wrong header": lambda path: write_stock_csv(path, HEADER_LINE.replace("종목명", "회사명") + ROWS),
    "not utf-8": lambda path: path.write_bytes((HEADER_LINE + ROWS).encode("cp949")),
    "empty": lambda path: path.write_bytes(b""),
    "a folder": lambda path: path.mkdir(),
}


@pytest.mark.parametrize("make", list(UNREADABLE.values()), ids=list(UNREADABLE))
def test_an_unreadable_stock_list_makes_the_feature_not_ready(client, tmp_path, monkeypatch, favorites_path,
                                                              make):
    path = tmp_path / "krx.csv"
    make(path)
    monkeypatch.setenv("KRX_CSV_PATH", str(path))
    for response in (client.get("/api/workspace"), put(client, "005930", True)):
        assert response.status_code == 503
        assert response.json() == NOT_READY   # a fixed sentence: no path, no cause
    assert not favorites_path.exists()


def test_not_ready_names_the_feature_and_a_fixed_reason(service, tmp_path, monkeypatch):
    monkeypatch.setenv("KRX_CSV_PATH", str(tmp_path / "missing.csv"))
    with pytest.raises(NotReady) as caught:
        service.bootstrap()
    assert (caught.value.area, caught.value.reason) == ("기업 목록", "종목표 파일을 읽을 수 없습니다")
    assert str(caught.value) == NOT_READY["detail"]


def test_the_cause_of_not_ready_goes_to_the_local_log(client, tmp_path, monkeypatch, caplog):
    missing = tmp_path / "missing.csv"
    monkeypatch.setenv("KRX_CSV_PATH", str(missing))
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert client.get("/api/workspace").json() == NOT_READY
    assert any(str(missing) in r.getMessage() for r in caplog.records if r.name == LOGGER)


def test_preparation_is_tried_again_on_the_next_request(client, tmp_path, monkeypatch):
    path = tmp_path / "krx.csv"
    monkeypatch.setenv("KRX_CSV_PATH", str(path))
    assert client.get("/api/workspace").status_code == 503
    assert client.get("/api/workspace").status_code == 503
    write_stock_csv(path)   # fill in the missing file: no restart needed
    assert client.get("/api/workspace").json() == {"stocks": CATALOG, "favorites": []}


def test_a_stock_list_path_added_to_env_is_used_on_the_next_request(env_file, client, tmp_path, monkeypatch):
    monkeypatch.delenv("KRX_CSV_PATH", raising=False)
    monkeypatch.chdir(tmp_path)   # the default docs/stock_data/... path does not exist here
    assert client.get("/api/workspace").status_code == 503
    listed = write_stock_csv(tmp_path / "listed.csv")
    env_file.write_text(f"KRX_CSV_PATH={listed.as_posix()}\n", encoding="utf-8")
    assert client.get("/api/workspace").json()["stocks"] == CATALOG


def test_the_stock_list_is_read_once_and_kept(client, stock_csv):
    first = client.get("/api/workspace").json()
    stock_csv.unlink()
    version_file(stock_csv).unlink()
    assert client.get("/api/workspace").json() == first
    assert put(client, "005930", True).status_code == 200


def _mismatch(csv_path: Path) -> None:
    write_stock_csv(csv_path, HEADER_LINE + ROWS.replace("DRAM/NAND", "DRAM"))


VERSION_PROBLEMS = {
    "missing": (lambda csv_path: version_file(csv_path).unlink(), "버전 정보 파일이 없습니다"),
    "mismatch": (_mismatch, "내용이 버전 정보와 다릅니다"),
    "invalid": (lambda csv_path: version_file(csv_path).write_text("not json", encoding="utf-8"),
                "버전 정보 파일을 읽을 수 없습니다"),
}


@pytest.mark.parametrize("break_version, text", list(VERSION_PROBLEMS.values()), ids=list(VERSION_PROBLEMS))
def test_a_version_problem_only_logs_a_warning(client, stock_csv, caplog, break_version, text):
    break_version(stock_csv)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        response = client.get("/api/workspace")
    assert response.status_code == 200
    assert [r["code"] for r in response.json()["stocks"]] == [r["code"] for r in CATALOG]
    (record,) = [r for r in caplog.records if r.name == LOGGER]
    assert record.levelno == logging.WARNING
    assert text in record.getMessage()


def test_a_matching_version_logs_nothing(client, stock_csv, caplog):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert client.get("/api/workspace").status_code == 200
    assert [r for r in caplog.records if r.name == LOGGER] == []


# --- the favorites file ------------------------------------------------------------------------

def test_the_default_favorites_file_is_in_the_home_folder(fake_home):
    expected = fake_home / ".review_viewer" / "favorites.json"
    assert default_favorites_path() == expected
    assert CompaniesService().favorites_path == expected
    assert list(fake_home.iterdir()) == []   # creating the service reads and writes nothing


def test_a_default_service_writes_only_under_the_home_folder(fake_home, stock_csv):
    with TestClient(build_app(CompaniesService())) as client:
        assert put(client, "005930", True).json() == {"favorites": ["005930"]}
    assert saved(fake_home / ".review_viewer" / "favorites.json") == {"stocks": ["005930"]}


def test_an_injected_favorites_path_never_touches_the_home_file(fake_home, stock_csv, favorites_path,
                                                                monkeypatch):
    def forbidden():
        raise AssertionError("the home favorites file must not be used")

    monkeypatch.setattr(service_module, "default_favorites_path", forbidden)
    with TestClient(build_app(CompaniesService(favorites_path=favorites_path))) as client:
        assert client.get("/api/workspace").status_code == 200
        put(client, "005930", True)
        put(client, "000660", True)
        assert put(client, "005930", False).json() == {"favorites": ["000660"]}
    assert saved(favorites_path) == {"stocks": ["000660"]}
    assert list(fake_home.iterdir()) == []


def test_get_service_makes_one_service_and_reads_nothing(fake_home, tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "_service", None)   # restored after the test
    monkeypatch.setenv("KRX_CSV_PATH", str(tmp_path / "missing.csv"))
    first = get_service()
    assert get_service() is first
    assert first.favorites_path == fake_home / ".review_viewer" / "favorites.json"
    assert list(fake_home.iterdir()) == []


def test_favorite_changes_run_one_at_a_time(service, stock_csv, favorites_path, monkeypatch):
    active = peak = 0
    counter = threading.Lock()
    real_add = favorites.add

    def slow_add(path, code):
        nonlocal active, peak
        with counter:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.02)   # widen the read-modify-write window
            real_add(path, code)
        finally:
            with counter:
                active -= 1

    monkeypatch.setattr(favorites, "add", slow_add)
    codes = ["005930", "000660", "016360", "000020"]
    with ThreadPoolExecutor(max_workers=len(codes)) as pool:
        list(pool.map(lambda code: service.set_favorite(code, True), codes))
    assert peak == 1
    assert sorted(saved(favorites_path)["stocks"]) == sorted(codes)
