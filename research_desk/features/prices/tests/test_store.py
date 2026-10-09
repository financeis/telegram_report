"""features.prices store: the two price tables over Supabase REST (the FakeSupabase stand-in).

Checked: snapshot upserts in batches on stock_code, reads by code in chunks (each answer under
the 1000-row limit), the full read of stored returns paged 1000 rows at a time with a new query
per page, run records (insert as running, finish, the latest run, the latest successful run).
"""
from __future__ import annotations

from research_desk.features.prices import store

from .fakes import FakeSupabase


def code(i: int) -> str:
    return f"{i:06d}"


def snap(i: int, **values) -> dict:
    return {"stock_code": code(i), "market": "KOSPI", "as_of": "2026-10-08", "close": 1000 + i,
            "ret_1w": float(i), "flags": [], **values}


# ── snapshots ────────────────────────────────────────────────────────────────

def test_snapshot_rows_are_upserted_on_the_stock_code_in_batches():
    db = FakeSupabase()
    store.upsert_snapshots(db, [snap(i) for i in range(1200)])
    upserts = db.queries(store.SNAPSHOT_TABLE, "upsert")
    assert [len(q.rows_sent) for q in upserts] == [500, 500, 200]
    assert {q.on_conflict for q in upserts} == {"stock_code"}
    assert len(db.tables[store.SNAPSHOT_TABLE]) == 1200
    assert db.snapshot(code(7))["close"] == 1007


def test_no_rows_send_nothing():
    db = FakeSupabase()
    store.upsert_snapshots(db, [])
    assert db.executed == []


def test_an_upsert_of_some_columns_leaves_the_others():
    db = FakeSupabase(snapshots=[snap(1, flags=["halted"], updated_at="2026-10-07T09:40:00+00:00")])
    store.upsert_snapshots(db, [{"stock_code": code(1), "flags": ["no_data", "halted"]}])
    row = db.snapshot(code(1))
    assert row["flags"] == ["no_data", "halted"]
    assert (row["close"], row["ret_1w"], row["updated_at"]) == (1001, 1.0, "2026-10-07T09:40:00+00:00")


def test_snapshots_are_read_by_code_in_chunks():
    db = FakeSupabase(snapshots=[snap(i) for i in range(1200)])
    wanted = [code(i) for i in range(0, 2400, 2)]   # 1200 codes, half of them stored
    found = store.read_snapshots(db, wanted)
    assert sorted(found) == [code(i) for i in range(0, 1200, 2)]
    assert found[code(4)]["close"] == 1004 and found[code(4)]["stock_code"] == code(4)
    reads = db.queries(store.SNAPSHOT_TABLE, "select")
    assert [len(q.filter_values("in", "stock_code")[0]) for q in reads] == [200] * 6
    assert all(q.columns == list(store.SNAPSHOT_COLUMNS) for q in reads)


def test_a_snapshot_read_can_ask_for_some_columns():
    db = FakeSupabase(snapshots=[snap(1, flags=["halted"])])
    assert store.read_snapshots(db, [code(1)], columns=("stock_code", "flags")) == {
        code(1): {"stock_code": code(1), "flags": ["halted"]}}


def test_reading_no_codes_asks_nothing():
    db = FakeSupabase()
    assert store.read_snapshots(db, []) == {}
    assert db.executed == []


def test_all_stored_returns_are_read_1000_rows_a_page():
    db = FakeSupabase(snapshots=[snap(i, ret_6m=1.5) for i in range(2558)])
    rows = store.read_all_returns(db)
    assert [row["stock_code"] for row in rows] == [code(i) for i in range(2558)]
    assert rows[5] == {"stock_code": code(5), "market": "KOSPI", "ret_1w": 5.0, "ret_1m": None,
                       "ret_3m": None, "ret_6m": 1.5}
    reads = db.queries(store.SNAPSHOT_TABLE, "select")
    assert [(q.offset, q.limit_n) for q in reads] == [(0, 1000), (1000, 1000), (2000, 1000)]
    assert all(q.orders == [("stock_code", False)] for q in reads)
    assert len({id(q) for q in reads}) == 3   # a new query for each page


def test_a_full_last_page_is_followed_by_an_empty_one():
    db = FakeSupabase(snapshots=[snap(i) for i in range(1000)])
    assert len(store.read_all_returns(db)) == 1000
    assert len(db.queries(store.SNAPSHOT_TABLE, "select")) == 2


# ── run records ──────────────────────────────────────────────────────────────

def test_a_run_starts_as_running_and_gets_its_id():
    db = FakeSupabase(runs=[{"run_id": 4, "status": "ok"}])
    run_id = store.start_run(db, started_at="2026-10-08T09:30:00+00:00", total=2558)
    assert run_id == 5
    assert db.runs()[-1] == {
        "run_id": 5, "started_at": "2026-10-08T09:30:00+00:00", "finished_at": None, "status": "running",
        "as_of": None, "stocks_total": 2558, "stocks_ok": None, "stocks_failed": None, "message": None}


def test_finishing_a_run_writes_its_outcome_only_to_that_run():
    db = FakeSupabase(runs=[{"run_id": 1, "status": "running"}, {"run_id": 2, "status": "running"}])
    store.finish_run(db, 2, status="partial", finished_at="2026-10-08T09:45:00+00:00", as_of="2026-10-08",
                     stocks_ok=9, stocks_failed=1, message="못 받은 종목 1개")
    first, second = db.runs()
    assert first["status"] == "running" and first["finished_at"] is None
    assert {k: second[k] for k in ("status", "finished_at", "as_of", "stocks_ok", "stocks_failed",
                                   "message")} == {
        "status": "partial", "finished_at": "2026-10-08T09:45:00+00:00", "as_of": "2026-10-08",
        "stocks_ok": 9, "stocks_failed": 1, "message": "못 받은 종목 1개"}


RUNS = [
    {"run_id": 1, "started_at": "2026-10-06T09:30:00+00:00", "status": "ok", "as_of": "2026-10-06"},
    {"run_id": 2, "started_at": "2026-10-07T09:30:00+00:00", "status": "partial", "as_of": "2026-10-07"},
    {"run_id": 3, "started_at": "2026-10-08T09:30:00+00:00", "status": "failed", "as_of": "2026-10-08"},
]


def test_the_latest_run_and_the_latest_successful_run():
    db = FakeSupabase(runs=list(reversed(RUNS)))
    assert store.latest_run(db)["run_id"] == 3
    assert store.latest_successful_run(db)["run_id"] == 2
    for q in db.queries(store.RUNS_TABLE):
        assert q.orders == [("started_at", True), ("run_id", True)] and q.limit_n == 1


def test_a_tie_on_the_start_time_goes_to_the_later_run_id():
    db = FakeSupabase(runs=[{"run_id": 7, "started_at": "2026-10-08T09:30:00+00:00", "status": "ok"},
                            {"run_id": 8, "started_at": "2026-10-08T09:30:00+00:00", "status": "failed"}])
    assert store.latest_run(db)["run_id"] == 8
    assert store.latest_successful_run(db)["run_id"] == 7


def test_no_runs_give_none():
    db = FakeSupabase(runs=[{"run_id": 1, "status": "failed"}, {"run_id": 2, "status": "running"}])
    assert store.latest_run(FakeSupabase()) is None
    assert store.latest_successful_run(db) is None
