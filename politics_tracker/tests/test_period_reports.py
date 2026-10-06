import json

import pytest

from src.calculations import Pair
from src.database import add_session, connect, initialize, undo_last
from src.reports import (period_bounds, period_report, save_custom_report,
                         write_daily_reports)


@pytest.mark.parametrize("day,kind,expected", [
    ("2026-10-04", "周报", ("2026-09-28", "2026-10-04")),
    ("2026-10-05", "周报", ("2026-10-05", "2026-10-11")),
    ("2026-12-31", "周报", ("2026-12-28", "2027-01-03")),
    ("2024-02-15", "月报", ("2024-02-01", "2024-02-29")),
    ("2026-12-31", "月报", ("2026-12-01", "2026-12-31")),
])
def test_calendar_bounds(day, kind, expected):
    assert period_bounds(day, kind) == expected


@pytest.fixture
def ledger(tmp_path):
    path = tmp_path / "politics.db"
    initialize(path)
    add_session("A0", Pair(2, 1), Pair(10, 5), session_date="2026-09-27", path=path)
    add_session("A1", Pair(1, 1), rewrong=Pair(0, 0), session_date="2026-09-28", path=path)
    add_session("A0", Pair(2, 2), Pair(14, 8), session_date="2026-10-04", path=path)
    add_session("A0", Pair(3, 2), Pair(19, 10), session_date="2026-10-05", path=path)
    return path


def test_period_activity_and_end_snapshot_are_distinct(ledger):
    with connect(ledger) as db:
        content, data = period_report(db, "2026-09-28", "2026-10-04", "周报")
    assert [r["session_date"] for r in data["period_sessions"]] == ["2026-09-28", "2026-10-04"]
    assert data["period_totals"]["A0"]["total"] == {"attempted": 7, "correct": 5, "wrong": 2}
    assert data["period_totals"]["A1"]["total"]["attempted"] == 1
    assert data["snapshot"]["a_completed"] == 22
    assert data["snapshot"]["wrongbook"] == {"single": 2, "multiple": 2}
    assert all(e["event_date"] <= "2026-10-04" for e in data["raw_events_through_date"])
    assert "期间统计" in content and "截至 2026-10-04" in content
    assert "记录日期：2026-10-04" in content


def test_empty_and_invalid_ranges(ledger):
    with connect(ledger) as db:
        content, data = period_report(db, "2026-10-06", "2026-10-07")
        assert "该时间段没有刷题记录" in content
        assert data["period_sessions"] == []
        assert data["snapshot"]["a_completed"] == 29
        with pytest.raises(ValueError, match="开始日期"):
            period_report(db, "2026-10-07", "2026-10-06")


def test_archives_and_custom_ranges_refresh_after_undo(ledger, tmp_path):
    root = tmp_path / "reports"
    custom = save_custom_report("2026-10-05", "2026-10-05", ledger, root / "custom")
    write_daily_reports(ledger, root / "daily")
    week = root / "weekly" / "2026-10-05_2026-10-11.md"
    assert week.exists()
    month = root / "monthly" / "2026-10-01_2026-10-31.json"
    assert json.loads(month.read_text(encoding="utf-8"))["period_totals"]["A0"]["total"]["attempted"] == 14
    undo_last(ledger)
    write_daily_reports(ledger, root / "daily")
    assert not week.exists()
    assert not week.with_suffix(".json").exists()
    assert "该时间段没有刷题记录" in custom.read_text(encoding="utf-8")
    assert json.loads(month.read_text(encoding="utf-8"))["period_totals"]["A0"]["total"]["attempted"] == 7
