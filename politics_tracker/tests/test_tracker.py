import json

import pytest

from src.backup import backup
from src.calculations import Pair, ValidationError
from src.database import (add_session, connect, initialize, preview, rebuild,
                          start_b, state, undo_last)
from src.reports import snapshot, write_daily_reports


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "politics.db"
    initialize(path)
    return path


def test_new_questions_calculate_single_and_multiple(db_path):
    add_session("A0", Pair(4, 5), Pair(25, 8), path=db_path)
    with connect(db_path) as db:
        row = dict(db.execute("SELECT * FROM sessions").fetchone())
    assert (row["attempted_single"], row["correct_single"], row["wrong_single"]) == (25, 21, 4)
    assert (row["attempted_multiple"], row["correct_multiple"], row["wrong_multiple"]) == (8, 3, 5)


def test_incremental_new_questions_replay_from_raw_input(db_path):
    add_session("A0", Pair(1, 1), Pair(10, 10), path=db_path)
    add_session("A0", Pair(2, 2), Pair(13, 14), path=db_path,
                completed_input_mode="incremental", completed_input=Pair(3, 4))
    with connect(db_path) as db:
        before = state(db)
        event = json.loads(db.execute("SELECT payload FROM events ORDER BY id DESC LIMIT 1").fetchone()[0])
        session = db.execute("SELECT attempted_single,attempted_multiple FROM sessions ORDER BY id DESC LIMIT 1").fetchone()
    assert event["completed_input_mode"] == "incremental"
    assert event["completed_input"] == {"single": 3, "multiple": 4}
    assert tuple(session) == (3, 4)
    rebuild(db_path)
    with connect(db_path) as db:
        assert state(db) == before


def test_retry_formula_and_pool_transition(db_path):
    add_session("A0", Pair(18, 32), Pair(68, 58), path=db_path)
    add_session("A1", Pair(7, 30), rewrong=Pair(3, 0), path=db_path)
    with connect(db_path) as db:
        row = dict(db.execute("SELECT * FROM sessions WHERE stage='A1'").fetchone())
        _, _, wrongbook, pools = state(db)
    assert (row["attempted_single"], row["correct_single"], row["wrong_single"]) == (14, 11, 3)
    assert (row["attempted_multiple"], row["correct_multiple"], row["wrong_multiple"]) == (2, 2, 0)
    assert (pools["A1"], pools["A2"], wrongbook) == (Pair(4, 30), Pair(3, 0), Pair(7, 30))


def test_retry_cannot_exceed_source_pool(db_path):
    add_session("A0", Pair(18, 0), Pair(18, 0), path=db_path)
    with pytest.raises(ValidationError, match="待刷池不足"):
        add_session("A1", Pair(0, 0), rewrong=Pair(2, 0), path=db_path)
    with connect(db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


def test_mismatch_is_reported(db_path):
    add_session("A0", Pair(18, 32), Pair(68, 58), path=db_path)
    add_session("A1", Pair(7, 30), rewrong=Pair(3, 0), path=db_path)
    with connect(db_path) as db:
        db.execute("UPDATE current_state SET wrongbook_single=6 WHERE id=1")
    with connect(db_path) as db, pytest.raises(ValidationError, match="数据不一致"):
        preview(db, "A1", Pair(6, 30), rewrong=Pair(0, 0))


def test_undo_replays_exact_prior_state(db_path):
    add_session("A0", Pair(18, 32), Pair(68, 58), path=db_path)
    with connect(db_path) as db:
        prior = state(db)
        prior_stats = snapshot(db)["totals"]
    add_session("A1", Pair(7, 30), rewrong=Pair(3, 0), path=db_path)
    undo_last(db_path)
    with connect(db_path) as db:
        assert state(db) == prior
        assert snapshot(db)["totals"] == prior_stats


def test_rebuild_recovers_deleted_derived_tables(db_path):
    add_session("A0", Pair(18, 32), Pair(68, 58), path=db_path)
    add_session("A1", Pair(7, 30), rewrong=Pair(3, 0), path=db_path)
    with connect(db_path) as db:
        prior_state = state(db)
        prior_stats = snapshot(db)["totals"]
        db.execute("DELETE FROM sessions")
        db.execute("DELETE FROM wrong_pools")
        db.execute("DELETE FROM current_state")
    rebuild(db_path)
    with connect(db_path) as db:
        assert state(db) == prior_state
        assert snapshot(db)["totals"] == prior_stats


def test_second_pass_baseline_and_stubborn_pool(db_path):
    add_session("A0", Pair(2, 0), Pair(5, 0), path=db_path)
    add_session("A1", Pair(2, 0), rewrong=Pair(2, 0), path=db_path)
    add_session("A2", Pair(2, 0), rewrong=Pair(2, 0), path=db_path)
    with connect(db_path) as db:
        assert state(db)[3]["A3+"] == Pair(2, 0)
    add_session("AX", Pair(1, 0), path=db_path)
    start_b(Pair(100, 80), path=db_path)
    add_session("B0", Pair(2, 1), Pair(103, 81), path=db_path)
    add_session("AX", Pair(1, 1), path=db_path)
    with connect(db_path) as db:
        phase, completed, wrongbook, pools = state(db)
        totals = snapshot(db)["totals"]
    assert (phase, completed, wrongbook) == ("B", Pair(103, 81), Pair(1, 1))
    assert pools["A3+"] == Pair(0, 0) and pools["B1"] == Pair(1, 1)
    assert totals["A0"]["total"] == {"attempted": 5, "correct": 3, "wrong": 2}
    assert totals["B0"]["total"] == {"attempted": 4, "correct": 2, "wrong": 2}


def test_reports_are_regenerated_after_undo(db_path, tmp_path):
    add_session("A0", Pair(1, 0), Pair(1, 0), session_date="2026-09-30", path=db_path)
    out = tmp_path / "daily"
    write_daily_reports(db_path, out)
    assert (out / "2026-09-30.md").exists()
    undo_last(db_path)
    write_daily_reports(db_path, out)
    assert not (out / "2026-09-30.md").exists()


def test_second_pass_snapshot_tracks_event_log(db_path, tmp_path):
    add_session("A0", Pair(1, 0), Pair(1, 0), session_date="2026-09-30", path=db_path)
    start_b(Pair(10, 20), event_date="2026-09-30", path=db_path)
    out = tmp_path / "daily"
    write_daily_reports(db_path, out)
    assert len(list((tmp_path / "snapshots").glob("start_b_*.md"))) == 1
    undo_last(db_path)
    write_daily_reports(db_path, out)
    assert not list((tmp_path / "snapshots").glob("start_b_*.md"))


def test_sqlite_backup_is_consistent(db_path, tmp_path):
    add_session("A0", Pair(1, 0), Pair(2, 0), path=db_path)
    copy = backup(db_path, tmp_path / "backups", manual=True)
    with connect(copy) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
