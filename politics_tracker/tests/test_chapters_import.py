import json
import sqlite3
from pathlib import Path

import pytest

from src.calculations import Pair, ValidationError
from src.chapters import CHAPTERS
from src.database import (add_session, connect, initialize, integrity_check,
                          rebuild, start_b, state, undo_last)
from src.reports import snapshot, write_daily_reports
from src.structured_import import form_values, parse_structured


NEW_TEXT = """[POLITICS]
stage=A0
chapter=马原
single_completed=68
multiple_completed=58
wrong_single=18
wrong_multiple=32
"""
RETRY_TEXT = """[POLITICS]
stage=A1
chapter=马原
wrong_single=7
wrong_multiple=30
rewrong_single=3
rewrong_multiple=0
"""


@pytest.fixture
def ledger(tmp_path):
    path = tmp_path / "data" / "politics.db"
    initialize(path)
    return path


def test_interleaved_chapters_do_not_change_deltas_or_retry_pools(ledger):
    add_session("A0", Pair(18, 32), Pair(68, 58), chapter="马原", path=ledger)
    add_session("A0", Pair(2, 3), Pair(10, 20), chapter="史纲", path=ledger)
    add_session("A1", Pair(7, 30), rewrong=Pair(3, 0), chapter="马原", path=ledger)
    add_session("A0", Pair(3, 4), Pair(13, 24), chapter="史纲", path=ledger,
                completed_input_mode="incremental", completed_input=Pair(3, 4))
    with connect(ledger) as db:
        integrity_check(db)
        assert state(db, "马原")[1:3] == (Pair(68, 58), Pair(7, 30))
        assert state(db, "马原")[3]["A2"] == Pair(3, 0)
        assert state(db, "史纲")[1:3] == (Pair(13, 24), Pair(3, 4))
        assert state(db, "史纲")[3]["A2"] == Pair(0, 0)
        assert snapshot(db)["totals"]["A0"]["total"]["attempted"] == 163
        assert snapshot(db)["wrongbook"] == {"single": 10, "multiple": 34}
        assert snapshot(db, chapter="马原")["totals"]["A0"]["total"]["attempted"] == 126
    with pytest.raises(ValidationError, match="待刷池不足"):
        add_session("A2", Pair(2, 4), rewrong=Pair(0, 0), chapter="史纲", path=ledger)


def test_all_five_chapters_have_independent_second_pass_and_pools(ledger):
    for chapter in CHAPTERS:
        add_session("A0", Pair(4, 4), Pair(10, 10), chapter=chapter, path=ledger)
    for i, chapter in enumerate(CHAPTERS):
        add_session("A1", Pair(3, 3), rewrong=Pair(1, 1), chapter=chapter, path=ledger)
        add_session("A2", Pair(2, 2), chapter=chapter, path=ledger)
        start_b(Pair(100 + i, 80 + i), chapter=chapter, path=ledger)
        add_session("B0", Pair(4, 4), Pair(103 + i, 83 + i), chapter=chapter, path=ledger)
        add_session("B1", Pair(3, 3), rewrong=Pair(1, 1), chapter=chapter, path=ledger)
        add_session("B2", Pair(2, 2), chapter=chapter, path=ledger)
    with connect(ledger) as db:
        integrity_check(db)
        before = {chapter: state(db, chapter) for chapter in CHAPTERS}
        totals = snapshot(db)["totals"]
        assert totals["B0"]["total"]["attempted"] == 30
        assert totals["A0"]["total"]["attempted"] == 100
        for i, chapter in enumerate(CHAPTERS):
            phase, completed, wrongbook, pools = before[chapter]
            assert (phase, completed, wrongbook) == ("B", Pair(103 + i, 83 + i), Pair(2, 2))
            assert pools["A1"] == Pair(2, 2)
            assert pools["B1"] == Pair(0, 0)
            assert pools["A2"] == pools["B2"] == Pair(0, 0)
    rebuild(ledger)
    with connect(ledger) as db:
        assert {chapter: state(db, chapter) for chapter in CHAPTERS} == before
    undo_last(ledger)
    with connect(ledger) as db:
        integrity_check(db)
        assert state(db, "思修")[2] == Pair(3, 3)
        assert all(state(db, chapter) == before[chapter] for chapter in CHAPTERS[:-1])


def test_second_pass_start_is_chapter_specific(ledger):
    add_session("A0", Pair(1, 1), Pair(2, 2), path=ledger)
    with pytest.raises(ValidationError):
        start_b(Pair(0, 0), chapter="史纲", path=ledger)
    start_b(Pair(50, 60), path=ledger)
    add_session("A0", Pair(1, 0), Pair(2, 0), chapter="史纲", path=ledger)
    with connect(ledger) as db:
        assert state(db, "史纲")[0] == "A"
        assert state(db, "马原")[0] == "B"


@pytest.mark.parametrize("chapter", ["", None, "马原/史纲", "未知", ["马原", "史纲"]])
def test_session_rejects_invalid_chapter(ledger, chapter):
    with pytest.raises(ValidationError):
        add_session("A0", Pair(0, 0), Pair(1, 1), chapter=chapter, path=ledger)
    with connect(ledger) as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_reports_global_totals_are_historical_and_show_today_chapters(ledger, tmp_path):
    day = "2026-09-30"
    add_session("A0", Pair(18, 32), Pair(68, 58), session_date=day, path=ledger)
    add_session("A0", Pair(2, 0), Pair(10, 0), session_date=day, chapter="史纲", path=ledger)
    add_session("A1", Pair(7, 30), rewrong=Pair(3, 0), session_date="2026-10-01", path=ledger)
    out = tmp_path / "reports" / "daily"
    write_daily_reports(ledger, out)
    raw = json.loads((out / "2026-10-01.json").read_text(encoding="utf-8"))
    assert raw["daily_sessions"][0]["chapter"] == "马原"
    assert raw["snapshot"]["a_completed"] == 136
    assert raw["snapshot"]["wrongbook"] == {"single": 9, "multiple": 30}
    assert raw["snapshot"]["chapters"]["史纲"]["a_completed"] == 10
    md = (out / "2026-10-01.md").read_text(encoding="utf-8")
    assert "累计统计（全局）" in md and "当天涉及章节累计｜马原" in md
    assert "当天涉及章节累计｜史纲" not in md
    first = json.loads((out / f"{day}.json").read_text(encoding="utf-8"))
    assert first["snapshot"]["wrongbook"] == {"single": 20, "multiple": 32}


def make_legacy(path):
    # Populate actual Sessions first, then restore the prior global schema.
    day = "2026-09-30"
    add_session("A0", Pair(18, 32), Pair(68, 58), session_date=day, note="保留备注", path=path)
    add_session("A1", Pair(7, 30), rewrong=Pair(3, 0), session_date=day, path=path)
    start_b(Pair(100, 80), event_date=day, path=path)
    add_session("B0", Pair(8, 31), Pair(102, 81), session_date=day, path=path)
    with connect(path) as db:
        for operation in ("insert", "update"):
            db.execute(f"DROP TRIGGER sessions_chapter_{operation}")
        schema = db.execute("SELECT sql FROM sqlite_master WHERE name='sessions'").fetchone()[0]
        schema = schema.replace("sessions", "legacy_sessions", 1).replace(
            "chapter TEXT NOT NULL CHECK(chapter IN ('马原','毛中特','新思想','史纲','思修'))", "chapter TEXT")
        db.execute(schema)
        db.execute("INSERT INTO legacy_sessions SELECT * FROM sessions")
        db.execute("DROP TABLE sessions")
        db.execute("ALTER TABLE legacy_sessions RENAME TO sessions")
        db.execute("UPDATE sessions SET chapter=CASE id WHEN 1 THEN '旧的自由文本' WHEN 2 THEN '' ELSE NULL END")
        for row in db.execute("SELECT id,payload FROM events").fetchall():
            payload = json.loads(row["payload"])
            if row["id"] in (1, 2):
                payload["chapter"] = "旧的自由文本" if row["id"] == 1 else ""
            else:
                payload.pop("chapter")
            db.execute("UPDATE events SET payload=? WHERE id=?", (json.dumps(payload, ensure_ascii=False), row["id"]))
        db.execute("ALTER TABLE current_state RENAME TO chapter_state")
        db.execute("CREATE TABLE current_state (id INTEGER PRIMARY KEY CHECK(id=1), phase TEXT, completed_single INTEGER, completed_multiple INTEGER, wrongbook_single INTEGER, wrongbook_multiple INTEGER, updated_at TEXT)")
        db.execute("INSERT INTO current_state SELECT id,phase,completed_single,completed_multiple,wrongbook_single,wrongbook_multiple,updated_at FROM chapter_state WHERE id=1")
        db.execute("DROP TABLE chapter_state")
        db.execute("ALTER TABLE wrong_pools RENAME TO chapter_pools")
        db.execute("CREATE TABLE wrong_pools (pool TEXT PRIMARY KEY, pending_single INTEGER, pending_multiple INTEGER)")
        db.execute("INSERT INTO wrong_pools SELECT pool,pending_single,pending_multiple FROM chapter_pools WHERE chapter='马原'")
        db.execute("DROP TABLE chapter_pools")


def test_legacy_migration_is_backed_up_idempotent_and_preserves_formulas(ledger):
    make_legacy(ledger)
    initialize(ledger)
    with connect(ledger) as db:
        integrity_check(db)
        rows = [dict(r) for r in db.execute("SELECT * FROM sessions ORDER BY id")]
        events = [dict(r) for r in db.execute("SELECT * FROM events ORDER BY id")]
        assert {r["chapter"] for r in rows} == {"马原"}
        assert rows[0]["note"] == "保留备注"
        assert (rows[1]["attempted_single"], rows[1]["attempted_multiple"]) == (14, 2)
        assert (rows[2]["attempted_single"], rows[2]["attempted_multiple"]) == (2, 1)
        assert state(db)[0:3] == ("B", Pair(102, 81), Pair(8, 31))
        assert all(state(db, ch)[1:3] == (Pair(0, 0), Pair(0, 0)) for ch in CHAPTERS[1:])
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE sessions SET chapter=NULL WHERE id=1")
    copies = list((ledger.parent.parent / "backups").glob("politics_pre_chapters_*.db"))
    assert len(copies) == 1
    with connect(copies[0]) as db:
        assert "chapter" not in {r["name"] for r in db.execute("PRAGMA table_info(current_state)")}
        assert json.loads(db.execute("SELECT payload FROM events WHERE id=1").fetchone()[0])["chapter"] == "旧的自由文本"
    initialize(ledger)
    rebuild(ledger)
    with connect(ledger) as db:
        assert [dict(r) for r in db.execute("SELECT * FROM sessions ORDER BY id")] == rows
        assert [dict(r) for r in db.execute("SELECT * FROM events ORDER BY id")] == events


def test_failed_migration_rolls_back_original_events_and_schema(ledger):
    make_legacy(ledger)
    with connect(ledger) as db:
        db.execute("UPDATE events SET payload=? WHERE id=1", (json.dumps({
            "stage": "A0", "chapter": "旧备注章节", "after_completed": {"single": 1, "multiple": 0},
            "after_wrongbook": {"single": 2, "multiple": 0}, "rewrong": {"single": 0, "multiple": 0},
        }, ensure_ascii=False),))
        before = [tuple(r) for r in db.execute("SELECT * FROM events")]
    with pytest.raises(ValidationError):
        initialize(ledger)
    with connect(ledger) as db:
        assert [tuple(r) for r in db.execute("SELECT * FROM events")] == before
        assert "chapter" not in {r["name"] for r in db.execute("PRAGMA table_info(current_state)")}


def test_snapshot_stats_do_not_depend_on_current_state_balances(ledger):
    add_session("A0", Pair(1, 2), Pair(10, 20), path=ledger)
    add_session("A0", Pair(2, 1), Pair(20, 10), chapter="毛中特", path=ledger)
    with connect(ledger) as db:
        before = snapshot(db)
        db.execute("UPDATE current_state SET wrongbook_single=999,completed_single=999")
        db.execute("UPDATE wrong_pools SET pending_single=999")
        assert snapshot(db) == before
        assert snapshot(db, chapter="新思想")["a_completed"] == 0


def test_parser_standard_and_chinese_fields_are_equivalent():
    assert parse_structured(NEW_TEXT)["single_completed"] == 68
    assert parse_structured(RETRY_TEXT)["rewrong_single"] == 3
    chinese = NEW_TEXT.replace("stage", "阶段").replace("chapter", "章节").replace("single_completed", "单选累计完成数").replace("multiple_completed", "多选累计完成数").replace("wrong_single", "单选错题数").replace("wrong_multiple", "多选错题数")
    assert parse_structured(chinese) == parse_structured(NEW_TEXT)
    chinese_retry = RETRY_TEXT.replace("rewrong_single", "本轮单选再错数").replace("rewrong_multiple", "多选再错数")
    assert parse_structured(chinese_retry) == parse_structured(RETRY_TEXT)


@pytest.mark.parametrize("text", [
    NEW_TEXT.replace("[POLITICS]", ""), NEW_TEXT + "章节=马原",
    NEW_TEXT + "unknown=1", NEW_TEXT.replace("=68", "=-1"),
    NEW_TEXT.replace("=68", "=1.5"), NEW_TEXT.replace("=68", "=abc"),
    NEW_TEXT.replace("chapter=马原", "chapter=马原/史纲"),
    NEW_TEXT.replace("stage=A0", "stage=C0"), NEW_TEXT.replace("single_completed=68", ""),
    RETRY_TEXT.replace("rewrong_multiple=0", ""), RETRY_TEXT + "single_completed=1",
    NEW_TEXT + "rewrong_single=1", NEW_TEXT + NEW_TEXT,
])
def test_parser_rejects_ambiguous_or_invalid_data(text):
    with pytest.raises(ValidationError):
        parse_structured(text)


@pytest.mark.parametrize("chapter,stage", [("史纲", "A0"), ("马原", "A1"), ("史纲", "B0")])
def test_import_conflicts_are_explicit(chapter, stage):
    with pytest.raises(ValidationError, match="冲突"):
        form_values(NEW_TEXT, chapter, stage)


@pytest.fixture
def app_test(ledger, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from src import backup as backup_module, database, reports
    original_connect = database.connect
    monkeypatch.setattr(database, "connect", lambda path=None: original_connect(ledger))
    monkeypatch.setattr(backup_module, "backup", lambda **kwargs: ledger)
    monkeypatch.setattr(reports, "write_daily_reports", lambda: None)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
    app.run()
    app.sidebar.radio[0].set_value("新建记录").run()
    assert not app.exception
    return app


def click(app, label):
    next(button for button in app.button if button.label == label).click().run()
    assert not app.exception


def test_ui_parse_fills_preview_without_writing_then_explicit_save(app_test, ledger):
    app = app_test
    app.text_area(key="structured_text").set_value(NEW_TEXT)
    click(app, "解析并填充表单")
    assert app.selectbox(key="record_chapter").value == "马原"
    assert app.number_input(key="record_马原_A0_single_completed").value == 68
    assert app.number_input(key="record_马原_A0_wrong_single").value == 18
    assert any(item.value.iloc[0]["作答"] == 68 for item in app.table)
    with connect(ledger) as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    click(app, "确认保存")
    with connect(ledger) as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    app.selectbox(key="record_stage_马原").set_value("A1").run()
    app.text_area(key="structured_text").set_value(RETRY_TEXT)
    click(app, "解析并填充表单")
    assert app.number_input(key="record_马原_A1_rewrong_single").value == 3
    with connect(ledger) as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    click(app, "确认保存")
    with connect(ledger) as db:
        assert state(db)[2] == Pair(7, 30)


def test_ui_conflict_does_not_overwrite_selectors_or_numbers(app_test, ledger):
    app = app_test
    app.selectbox(key="record_chapter").set_value("史纲").run()
    app.number_input(key="record_史纲_A0_wrong_single").set_value(2).run()
    app.text_area(key="structured_text").set_value(NEW_TEXT)
    click(app, "解析并填充表单")
    assert any("冲突" in error.value for error in app.error)
    assert app.selectbox(key="record_chapter").value == "史纲"
    assert app.selectbox(key="record_stage_史纲").value == "A0"
    assert app.number_input(key="record_史纲_A0_wrong_single").value == 2
    with connect(ledger) as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_ui_stage_conflict_and_invalid_preview_cannot_save(app_test, ledger):
    app = app_test
    app.text_area(key="structured_text").set_value(RETRY_TEXT)
    click(app, "解析并填充表单")
    assert any("冲突" in error.value and "stage" in error.value for error in app.error)
    assert app.selectbox(key="record_stage_马原").value == "A0"
    app.text_area(key="structured_text").set_value(NEW_TEXT.replace("wrong_single=18", "wrong_single=100"))
    click(app, "解析并填充表单")
    assert next(button for button in app.button if button.label == "确认保存").disabled
    with connect(ledger) as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_ui_dashboard_filters_and_chapter_maintenance(app_test, ledger):
    app = app_test
    add_session("A0", Pair(1, 0), Pair(10, 0), chapter="马原", path=ledger)
    add_session("A0", Pair(2, 0), Pair(20, 0), chapter="史纲", path=ledger)
    app.sidebar.radio[0].set_value("概览").run()
    assert app.metric[0].value == "30 / 1098"
    next(item for item in app.selectbox if item.label == "章节筛选").set_value("史纲").run()
    assert not app.exception
    assert app.metric[0].value == "20 / 1098"
    app.sidebar.radio[0].set_value("导出与维护").run()
    assert not app.exception
    next(item for item in app.selectbox if item.label == "维护章节").set_value("史纲").run()
    click(app, "建立二刷基准")
    with connect(ledger) as db:
        assert state(db, "史纲")[0] == "B"
        assert state(db, "马原")[0] == "A"


def test_ui_reports_select_periods_and_validate_dates(app_test, ledger):
    from datetime import date
    app = app_test
    add_session("A0", Pair(1, 0), Pair(10, 0), session_date="2026-10-04", path=ledger)
    app.sidebar.radio[0].set_value("历史与报表").run()
    assert not app.exception
    for kind in ("周报", "月报", "自选时间段报"):
        next(item for item in app.radio if item.label == "报表类型").set_value(kind).run()
        assert not app.exception
        assert any(f"考研政治刷题{kind}" in item.value for item in app.markdown)
    next(item for item in app.date_input if item.label == "开始日期").set_value(date(2026, 10, 8)).run()
    next(item for item in app.date_input if item.label == "结束日期").set_value(date(2026, 10, 7)).run()
    assert not app.exception
    assert any("开始日期不能晚于结束日期" in error.value for error in app.error)
