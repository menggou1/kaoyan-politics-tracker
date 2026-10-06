"""Import the known historical aggregate once; actual practice dates are unknown."""

import argparse
import json
from datetime import date, datetime

from src.database import DEFAULT_DB, ValidationError, _date, _project, connect, initialize
from src.reports import write_daily_reports


def seed(path=DEFAULT_DB, on_date=None):
    initialize(path)
    on_date = _date(on_date or date.today().isoformat())
    with connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM events LIMIT 1").fetchone():
            raise ValidationError("数据库已有事件，不能再次导入历史汇总。")
        for stage, after_wrong, after_completed, rewrong, note in (
            ("A0", {"single": 18, "multiple": 32}, {"single": 68, "multiple": 58},
             {"single": 0, "multiple": 0}, "历史汇总导入：实际刷题日期未知；A0累计作答126题。"),
            ("A1", {"single": 7, "multiple": 30}, None,
             {"single": 3, "multiple": 0}, "历史汇总导入：实际刷题日期未知；A1累计作答16题。"),
        ):
            payload = {"stage": stage, "after_wrongbook": after_wrong,
                       "after_completed": after_completed, "rewrong": rewrong,
                       "chapter": "马原", "note": note, "screenshot_path": None,
                       "source": "historical_import"}
            db.execute("INSERT INTO events(event_date,created_at,kind,payload) VALUES (?,?,?,?)", (
                on_date, datetime.now().isoformat(timespec="seconds"), "session",
                json.dumps(payload, ensure_ascii=False)))
        _project(db)
    write_daily_reports(path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="导入记录日期 YYYY-MM-DD；实际刷题日期仍标记为未知")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    args = parser.parse_args()
    seed(args.db, args.date)
    print("历史汇总已导入。")
