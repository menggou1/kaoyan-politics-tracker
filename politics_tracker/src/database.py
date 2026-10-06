"""SQLite event log and deterministic projection rebuild."""

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

from .calculations import (FORMAL_STAGES, POOLS, Pair, ValidationError,
                           calculate, check_balance, empty_pools)

from .chapters import CHAPTERS, DEFAULT_CHAPTER, validate_chapter

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "politics.db"


class TrackerConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect(path=DEFAULT_DB):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15, factory=TrackerConnection)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA busy_timeout = 15000")
    return db


def initialize(path=DEFAULT_DB):
    with connect(path) as db:
        legacy = db.execute("SELECT 1 FROM sqlite_master WHERE name='current_state'").fetchone()
        legacy = legacy and 'chapter' not in {r['name'] for r in db.execute("PRAGMA table_info(current_state)")}
        if legacy:
            # Back up the original schema and event log before any migration writes.
            folder = Path(path).parent.parent / "backups"
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / f"politics_pre_chapters_{datetime.now():%Y-%m-%d_%H%M%S_%f}.db"
            with sqlite3.connect(target) as destination:
                db.backup(destination)
        db.executescript("""
        CREATE TABLE IF NOT EXISTS settings (
          key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          event_date TEXT NOT NULL,
          created_at TEXT NOT NULL,
          kind TEXT NOT NULL CHECK(kind IN ('session','start_b')),
          payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (
          id INTEGER PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
          session_date TEXT NOT NULL, created_at TEXT NOT NULL,
          stage TEXT NOT NULL CHECK(stage IN ('A0','A1','A2','AX','B0','B1','B2','BX')),
          chapter TEXT NOT NULL CHECK(chapter IN ('马原','毛中特','新思想','史纲','思修')),
          note TEXT, screenshot_path TEXT, source TEXT NOT NULL,
          before_completed_single INTEGER NOT NULL, before_completed_multiple INTEGER NOT NULL,
          after_completed_single INTEGER NOT NULL, after_completed_multiple INTEGER NOT NULL,
          before_wrongbook_single INTEGER NOT NULL, before_wrongbook_multiple INTEGER NOT NULL,
          after_wrongbook_single INTEGER NOT NULL, after_wrongbook_multiple INTEGER NOT NULL,
          rewrong_single INTEGER NOT NULL, rewrong_multiple INTEGER NOT NULL,
          attempted_single INTEGER NOT NULL, attempted_multiple INTEGER NOT NULL,
          correct_single INTEGER NOT NULL, correct_multiple INTEGER NOT NULL,
          wrong_single INTEGER NOT NULL, wrong_multiple INTEGER NOT NULL);
        """)
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT OR IGNORE INTO settings VALUES ('question_bank_name','肖1000')")
        db.execute("INSERT OR IGNORE INTO settings VALUES ('question_bank_total','1098')")
        if legacy:
            for row in db.execute("SELECT id,payload FROM events").fetchall():
                payload = json.loads(row["payload"])
                payload["chapter"] = DEFAULT_CHAPTER
                db.execute("UPDATE events SET payload=? WHERE id=?",
                           (json.dumps(payload, ensure_ascii=False), row["id"]))
            db.execute("UPDATE sessions SET chapter=?", (DEFAULT_CHAPTER,))
            db.execute("DROP TABLE current_state")
            db.execute("DROP TABLE wrong_pools")
        # These are projections only; original events and Session columns are retained.
        db.execute("""CREATE TABLE IF NOT EXISTS current_state (
          id INTEGER PRIMARY KEY, chapter TEXT NOT NULL UNIQUE
          CHECK(chapter IN ('马原','毛中特','新思想','史纲','思修')),
          phase TEXT NOT NULL CHECK(phase IN ('A','B')),
          completed_single INTEGER NOT NULL, completed_multiple INTEGER NOT NULL,
          wrongbook_single INTEGER NOT NULL, wrongbook_multiple INTEGER NOT NULL,
          updated_at TEXT NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS wrong_pools (
          chapter TEXT NOT NULL CHECK(chapter IN ('马原','毛中特','新思想','史纲','思修')),
          pool TEXT NOT NULL, pending_single INTEGER NOT NULL CHECK(pending_single >= 0),
          pending_multiple INTEGER NOT NULL CHECK(pending_multiple >= 0),
          PRIMARY KEY(chapter,pool))""")
        # Enforce the same invariant on legacy Session tables without changing their columns.
        for operation in ("INSERT", "UPDATE"):
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS sessions_chapter_{operation.lower()}
              BEFORE {operation} ON sessions
              WHEN NEW.chapter IS NULL OR NEW.chapter NOT IN ('马原','毛中特','新思想','史纲','思修')
              BEGIN SELECT RAISE(ABORT, 'Session requires one valid chapter'); END""")
        if legacy:
            _project(db)
        else:
            for index, chapter in enumerate(CHAPTERS, 1):
                db.execute("INSERT OR IGNORE INTO current_state VALUES (?,?,'A',0,0,0,0,?)",
                           (index, chapter, datetime.now().isoformat(timespec="seconds")))
                for name in POOLS:
                    db.execute("INSERT OR IGNORE INTO wrong_pools VALUES (?,?,0,0)", (chapter, name))


def state(db, chapter=DEFAULT_CHAPTER):
    validate_chapter(chapter)
    row = db.execute("SELECT * FROM current_state WHERE chapter=?", (chapter,)).fetchone()
    pools = {r["pool"]: Pair(r["pending_single"], r["pending_multiple"])
             for r in db.execute("SELECT * FROM wrong_pools WHERE chapter=?", (chapter,))}
    if row is None or set(pools) != set(POOLS):
        raise ValidationError("章节账本不完整，请重新计算全部统计。")
    return row["phase"], Pair(row["completed_single"], row["completed_multiple"]), Pair(
        row["wrongbook_single"], row["wrongbook_multiple"]), pools


def preview(db, stage, after_wrongbook, after_completed=None, rewrong=None, chapter=DEFAULT_CHAPTER):
    phase, completed, wrongbook, pools = state(db, chapter)
    if stage[0] != phase and not (phase == "B" and stage in ("A1", "A2", "AX")):
        raise ValidationError("当前阶段为" + ("一刷" if phase == "A" else "二刷") + "，请使用对应阶段。")
    return calculate(stage, completed, wrongbook, pools, after_wrongbook,
                     after_completed, rewrong)


def _date(value):
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValidationError("日期必须为 YYYY-MM-DD。") from exc


def _check_chronology(db, event_date):
    last = db.execute("SELECT event_date FROM events ORDER BY id DESC LIMIT 1").fetchone()
    if last and event_date < last["event_date"]:
        raise ValidationError("记录日期不能早于上一条操作；请按实际录入顺序记录。")


def _project(db):
    """Replace every derived table from original events inside caller's transaction."""
    rows = db.execute("SELECT * FROM events ORDER BY id").fetchall()
    db.execute("DELETE FROM sessions")
    db.execute("DELETE FROM wrong_pools")
    db.execute("DELETE FROM current_state")
    ledgers = {chapter: ("A", Pair(0, 0), Pair(0, 0), empty_pools(),
                         datetime.now().isoformat(timespec="seconds")) for chapter in CHAPTERS}
    for row in rows:
        payload = json.loads(row["payload"])
        chapter = validate_chapter(payload.get("chapter"))
        phase, completed, wrongbook, pools, _ = ledgers[chapter]
        updated_at = row["created_at"]
        if row["kind"] == "start_b":
            if phase != "A" or not db.execute("SELECT 1 FROM sessions WHERE stage='A0' AND chapter=? LIMIT 1", (chapter,)).fetchone():
                raise ValidationError("开始二刷只能在已有一刷新题记录后执行一次。")
            phase = "B"
            completed = Pair(**payload["baseline_completed"])
            check_balance(wrongbook, pools)
            ledgers[chapter] = phase, completed, wrongbook, pools, updated_at
            continue
        stage = payload["stage"]
        if stage[0] != phase and not (phase == "B" and stage in ("A1", "A2", "AX")):
            raise ValidationError(f"事件 {row['id']} 的阶段 {stage} 与当前阶段不符。")
        raw_completed = payload.get("after_completed")
        if payload.get("completed_input_mode") == "incremental":
            delta = Pair(**payload["completed_input"])
            replayed_completed = Pair(completed.single + delta.single,
                                      completed.multiple + delta.multiple)
            if raw_completed != replayed_completed.as_dict():
                raise ValidationError(f"事件 {row['id']} 的增量与累计完成数不一致。")
            raw_completed = replayed_completed.as_dict()
        result = calculate(
            stage, completed, wrongbook, pools,
            Pair(**payload["after_wrongbook"]),
            Pair(**raw_completed) if raw_completed is not None else None,
            Pair(**payload["rewrong"]),
        )
        db.execute("""INSERT INTO sessions VALUES (
          ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            row["id"], row["event_date"], row["created_at"], stage,
            chapter, payload.get("note"), payload.get("screenshot_path"),
            payload.get("source", "manual"),
            result.before_completed.single, result.before_completed.multiple,
            result.after_completed.single, result.after_completed.multiple,
            result.before_wrongbook.single, result.before_wrongbook.multiple,
            result.after_wrongbook.single, result.after_wrongbook.multiple,
            result.rewrong.single, result.rewrong.multiple,
            result.attempted.single, result.attempted.multiple,
            result.correct.single, result.correct.multiple,
            result.wrong.single, result.wrong.multiple,
        ))
        completed, wrongbook, pools = result.after_completed, result.after_wrongbook, result.pools
        ledgers[chapter] = phase, completed, wrongbook, pools, updated_at
    for index, chapter in enumerate(CHAPTERS, 1):
        phase, completed, wrongbook, pools, updated_at = ledgers[chapter]
        for name in POOLS:
            p = pools[name]
            db.execute("INSERT INTO wrong_pools VALUES (?,?,?,?)",
                       (chapter, name, p.single, p.multiple))
        db.execute("INSERT INTO current_state VALUES (?,?,?,?,?,?,?,?)", (
            index, chapter, phase, completed.single, completed.multiple,
            wrongbook.single, wrongbook.multiple, updated_at))
        check_balance(wrongbook, pools)


def rebuild(path=DEFAULT_DB):
    initialize(path)
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        _project(db)


def add_session(stage, after_wrongbook: Pair, after_completed: Pair | None = None,
                rewrong: Pair | None = None, session_date=None, chapter=DEFAULT_CHAPTER, note="",
                screenshot_path=None, source="manual", path=DEFAULT_DB,
                completed_input_mode="cumulative", completed_input: Pair | None = None):
    initialize(path)
    validate_chapter(chapter)
    session_date = _date(session_date or date.today().isoformat())
    if stage not in FORMAL_STAGES + ("AX", "BX"):
        raise ValidationError("未知阶段。")
    if completed_input_mode not in ("cumulative", "incremental"):
        raise ValidationError("未知的新题完成数录入方式。")
    if completed_input_mode == "incremental" and (not stage.endswith("0") or completed_input is None):
        raise ValidationError("增量模式仅用于新题阶段，且需要填写本次新增题数。")
    if completed_input_mode == "cumulative" and completed_input is not None and completed_input != after_completed:
        raise ValidationError("累计模式输入与保存的累计完成数不一致。")
    payload = {
        "stage": stage, "after_wrongbook": after_wrongbook.as_dict(),
        "after_completed": after_completed.as_dict() if after_completed else None,
        "rewrong": (rewrong or Pair(0, 0)).as_dict(), "chapter": chapter.strip(),
        "note": note.strip(), "screenshot_path": screenshot_path, "source": source,
        "completed_input_mode": completed_input_mode,
        "completed_input": completed_input.as_dict() if completed_input else None,
    }
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        _check_chronology(db, session_date)
        if completed_input_mode == "incremental":
            _, before_completed, _, _ = state(db, chapter)
            expected = Pair(before_completed.single + completed_input.single,
                            before_completed.multiple + completed_input.multiple)
            if after_completed != expected:
                raise ValidationError("本次新增题数与计算出的累计完成数不一致，请刷新页面后重试。")
        result = preview(db, stage, after_wrongbook, after_completed, rewrong, chapter)
        if result.attempted.total == 0:
            raise ValidationError("本轮实际作答为0，未保存记录。")
        stamp = datetime.now().isoformat(timespec="seconds")
        cursor = db.execute("INSERT INTO events(event_date,created_at,kind,payload) VALUES (?,?,?,?)",
                            (session_date, stamp, "session", json.dumps(payload, ensure_ascii=False)))
        _project(db)
        return cursor.lastrowid


def start_b(baseline_completed: Pair, event_date=None, path=DEFAULT_DB, chapter=DEFAULT_CHAPTER):
    validate_chapter(chapter)
    initialize(path)
    event_date = _date(event_date or date.today().isoformat())
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        _check_chronology(db, event_date)
        phase, _, wrongbook, pools = state(db, chapter)
        if phase != "A" or not db.execute("SELECT 1 FROM sessions WHERE stage='A0' AND chapter=?", (chapter,)).fetchone():
            raise ValidationError("已有一刷新题记录且尚未开始二刷，才能建立二刷基准。")
        check_balance(wrongbook, pools)
        db.execute("INSERT INTO events(event_date,created_at,kind,payload) VALUES (?,?,?,?)", (
            event_date, datetime.now().isoformat(timespec="seconds"), "start_b",
            json.dumps({"baseline_completed": baseline_completed.as_dict(), "chapter": chapter}, ensure_ascii=False)))
        _project(db)


def undo_last(path=DEFAULT_DB):
    initialize(path)
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        last = db.execute("SELECT id,kind FROM events ORDER BY id DESC LIMIT 1").fetchone()
        if not last:
            raise ValidationError("没有可撤销的操作。")
        db.execute("DELETE FROM events WHERE id=?", (last["id"],))
        _project(db)
        return dict(last)


def read_settings(db):
    return {r["key"]: r["value"] for r in db.execute("SELECT * FROM settings")}


def integrity_check(db):
    """Fail visibly if derived state is missing or does not match the latest event."""
    for chapter in CHAPTERS:
        _chapter_integrity_check(db, chapter)


def _chapter_integrity_check(db, chapter):
    phase, completed, wrongbook, pools = state(db, chapter)
    check_balance(wrongbook, pools)
    events = db.execute("SELECT COUNT(*) FROM events WHERE kind='session'").fetchone()[0]
    sessions = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    if events != sessions:
        raise ValidationError("事件日志与Session派生表数量不一致。请重新计算全部统计。")
    last = db.execute("SELECT * FROM sessions WHERE chapter=? ORDER BY id DESC LIMIT 1", (chapter,)).fetchone()
    if last and wrongbook != Pair(last["after_wrongbook_single"], last["after_wrongbook_multiple"]):
        raise ValidationError("当前错题本与最近一次Session不一致。请重新计算全部统计。")
    expected_phase = "B" if db.execute("SELECT 1 FROM events WHERE kind='start_b' AND json_extract(payload,'$.chapter')=? LIMIT 1", (chapter,)).fetchone() else "A"
    if phase != expected_phase:
        raise ValidationError("当前阶段与事件日志不一致。请重新计算全部统计。")
    totals = {stage: {kind: {field: 0 for field in ("attempted", "wrong")}
                      for kind in ("single", "multiple")}
              for stage in FORMAL_STAGES + ("AX", "BX")}
    for row in db.execute("SELECT * FROM sessions WHERE chapter=?", (chapter,)):
        for kind in ("single", "multiple"):
            for field in ("attempted", "wrong"):
                totals[row["stage"]][kind][field] += row[f"{field}_{kind}"]
    for prefix in ("A", "B"):
        for kind in ("single", "multiple"):
            expected = (
                totals[prefix+"0"][kind]["wrong"] - totals[prefix+"1"][kind]["attempted"],
                totals[prefix+"1"][kind]["wrong"] - totals[prefix+"2"][kind]["attempted"],
                totals[prefix+"2"][kind]["wrong"] - totals[prefix+"X"][kind]["attempted"],
            )
            actual = tuple(getattr(pools[name], kind) for name in (prefix+"1", prefix+"2", prefix+"3+"))
            if actual != expected:
                raise ValidationError("错题池分布与Session记录不一致。请重新计算全部统计。")
    if phase == "A":
        expected_completed = Pair(*(totals["A0"][kind]["attempted"] for kind in ("single", "multiple")))
    else:
        row = db.execute("SELECT payload FROM events WHERE kind='start_b' AND json_extract(payload,'$.chapter')=? LIMIT 1", (chapter,)).fetchone()
        baseline = Pair(**json.loads(row["payload"])["baseline_completed"])
        expected_completed = Pair(*(getattr(baseline, kind) + totals["B0"][kind]["attempted"]
                                    for kind in ("single", "multiple")))
    if completed != expected_completed:
        raise ValidationError("当前累计完成数与Session记录不一致。请重新计算全部统计。")


def update_settings(name, total, path=DEFAULT_DB):
    if not name.strip() or type(total) is not int or total <= 0:
        raise ValidationError("题库名称不能为空，总题量必须是正整数。")
    initialize(path)
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE settings SET value=? WHERE key='question_bank_name'", (name.strip(),))
        db.execute("UPDATE settings SET value=? WHERE key='question_bank_total'", (str(total),))
