"""Human-readable daily reports and portable exports."""

import csv
import io
import json
from datetime import date, timedelta
from pathlib import Path

from .calculations import FORMAL_STAGES, POOLS, STAGE_NAMES, Pair, rate
from .chapters import CHAPTERS, validate_chapter
from .database import DEFAULT_DB, ROOT, connect, read_settings


def _totals(rows):
    result = {}
    for stage in FORMAL_STAGES:
        items = [r for r in rows if r["stage"] == stage]
        result[stage] = {
            kind: {key: sum(r[f"{key}_{kind}"] for r in items)
                   for key in ("attempted", "correct", "wrong")}
            for kind in ("single", "multiple")
        }
        result[stage]["total"] = {
            key: result[stage]["single"][key] + result[stage]["multiple"][key]
            for key in ("attempted", "correct", "wrong")
        }
    return result


def _pool_snapshot(rows):
    pools = {name: {"single": 0, "multiple": 0} for name in POOLS}
    for r in rows:
        stage = r["stage"]
        for kind in ("single", "multiple"):
            if stage.endswith("0"):
                pools[stage[0] + "1"][kind] += r[f"wrong_{kind}"]
            else:
                source = stage if stage.endswith(("1", "2")) else stage[0] + "3+"
                pools[source][kind] -= r[f"attempted_{kind}"]
                if stage.endswith("1"):
                    pools[stage[0] + "2"][kind] += r[f"wrong_{kind}"]
                elif stage.endswith("2"):
                    pools[stage[0] + "3+"][kind] += r[f"wrong_{kind}"]
    return pools


def _chapter_summary(rows):
    last = rows[-1] if rows else None
    return {
        "totals": _totals(rows), "pools": _pool_snapshot(rows),
        "wrongbook": {kind: last[f"after_wrongbook_{kind}"] if last else 0
                      for kind in ("single", "multiple")},
        "a_completed": sum(r["attempted_single"] + r["attempted_multiple"]
                           for r in rows if r["stage"] == "A0"),
    }


def snapshot(db, through_date=None, before_event_id=None, chapter=None):
    if chapter is not None:
        validate_chapter(chapter)
    query = "SELECT * FROM sessions"
    args = []
    clauses = []
    if chapter is not None:
        clauses.append("chapter = ?")
        args.append(chapter)
    if through_date:
        clauses.append("session_date <= ?")
        args.append(through_date)
    if before_event_id is not None:
        clauses.append("id < ?")
        args.append(before_event_id)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id"
    rows = [dict(r) for r in db.execute(query, args)]
    settings = read_settings(db)
    pools = _pool_snapshot(rows)
    latest = {r["chapter"]: r for r in rows}
    a_completed = sum(r["attempted_single"] + r["attempted_multiple"] for r in rows if r["stage"] == "A0")
    return {
        "settings": settings, "chapter": chapter or "全部", "sessions": rows, "totals": _totals(rows),
        "chapters": {name: _chapter_summary([r for r in rows if r["chapter"] == name])
                     for name in CHAPTERS if chapter is None or name == chapter},
        "pools": pools, "wrongbook": {
            kind: sum(r[f"after_wrongbook_{kind}"] for r in latest.values())
            for kind in ("single", "multiple")
        },
        "a_completed": a_completed,
        "a_completed_single": sum(r["attempted_single"] for r in rows if r["stage"] == "A0"),
        "a_completed_multiple": sum(r["attempted_multiple"] for r in rows if r["stage"] == "A0"),
    }


def _session_markdown(r):
    stage = r["stage"]
    lines = [f"### Session {r['id']}", "", f"阶段：{stage} {STAGE_NAMES[stage]}"]
    if r["source"] == "historical_import":
        lines.append("来源：历史汇总导入；实际刷题日期未提供")
    if r["chapter"]:
        lines.append(f"章节：{r['chapter']}")
    for label, kind in (("单选", "single"), ("多选", "multiple"), ("总体", None)):
        attempted = sum(r[f"attempted_{k}"] for k in ("single", "multiple")) if kind is None else r[f"attempted_{kind}"]
        correct = sum(r[f"correct_{k}"] for k in ("single", "multiple")) if kind is None else r[f"correct_{kind}"]
        wrong = attempted-correct
        lines += ["", f"{label}：", f"- 作答：{attempted}", f"- 正确：{correct}",
                  f"- {'再错' if stage.endswith(('1','2')) else '错误'}：{wrong}",
                  f"- 正确率：{rate(correct, attempted) if stage not in ('AX','BX') else '不计入统计'}"]
    if r["note"]:
        lines += ["", f"备注：{r['note']}"]
    if r["screenshot_path"]:
        lines += [f"截图：{r['screenshot_path']}"]
    return "\n".join(lines)


def statistics_markdown(snap):
    lines = ["| 阶段 | 作答 | 正确 | 错误 | 总正确率 | 单选正确率 | 多选正确率 |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for stage in FORMAL_STAGES:
        t = snap["totals"][stage]
        total, single, multiple = t["total"], t["single"], t["multiple"]
        lines.append(
            f"| {stage} {STAGE_NAMES[stage]} | {total['attempted'] or '-'} | "
            f"{total['correct'] if total['attempted'] else '-'} | "
            f"{total['wrong'] if total['attempted'] else '-'} | "
            f"{rate(total['correct'],total['attempted'])} | "
            f"{rate(single['correct'],single['attempted'])} | "
            f"{rate(multiple['correct'],multiple['attempted'])} |"
        )
    return "\n".join(lines)


def daily_markdown(day, snap):
    return period_markdown(day, day, snap, "日报")


def period_bounds(day, report_type):
    day = date.fromisoformat(str(day))
    if report_type == "日报":
        return day.isoformat(), day.isoformat()
    if report_type == "周报":
        start = day - timedelta(days=day.weekday())
        end = start + timedelta(days=6)
    elif report_type == "月报":
        start = day.replace(day=1)
        end = (start.replace(year=start.year + 1, month=1) if start.month == 12
               else start.replace(month=start.month + 1)) - timedelta(days=1)
    else:
        raise ValueError("不支持的报表类型")
    return start.isoformat(), end.isoformat()


def validate_period(start, end):
    start, end = date.fromisoformat(str(start)), date.fromisoformat(str(end))
    if start > end:
        raise ValueError("开始日期不能晚于结束日期")
    return start.isoformat(), end.isoformat()


def period_markdown(start, end, snap, report_type="自选时间段报"):
    start, end = validate_period(start, end)
    today = [r for r in snap["sessions"] if start <= r["session_date"] <= end]
    daily = report_type == "日报"
    label = "今日" if daily else "期间"
    if not today and daily:
        return ""
    settings, pools, wrongbook = snap["settings"], snap["pools"], snap["wrongbook"]
    bank_name = settings["question_bank_name"]
    bank_total = int(settings["question_bank_total"])
    logical = {kind: sum(p[kind] for p in pools.values()) for kind in ("single", "multiple")}
    consistent = logical == wrongbook
    span = start if daily else f"{start} ～ {end}（含起止日）"
    lines = [f"# 考研政治刷题{report_type}｜{span}", "",
             f"## {label}统计", "",
             f"记录数：{len(today)}；刷题天数：{len({r['session_date'] for r in today})}", "",
             statistics_markdown({"totals": _totals(today)}), "",
             "AX/BX 只维护顽固错题余额，不计入六轨作答量和正确率。", "",
             f"## {label}操作", ""]
    if not today:
        lines += ["该时间段没有刷题记录。", ""]
    for r in today:
        if not daily:
            lines += [f"记录日期：{r['session_date']}", ""]
        lines += [_session_markdown(r), ""]
    lines += ["## 累计统计（全局）", "", f"截至 {end}", "", statistics_markdown(snap), "",
              "## 当前题库进度", "", f"{bank_name}：",
              f"- 总题量：{bank_total}", f"- 一刷已完成：{snap['a_completed']}",
              f"- 一刷进度：{rate(snap['a_completed'],bank_total)}",
              f"- 单选累计完成：{snap['a_completed_single']}",
              f"- 多选累计完成：{snap['a_completed_multiple']}", "",
              "## 当前错题池", "",
              "| 错题池 | 单选 | 多选 | 合计 |", "|---|---:|---:|---:|"]
    for name in POOLS:
        p = pools[name]
        lines.append(f"| {name} | {p['single']} | {p['multiple']} | {p['single']+p['multiple']} |")
    for chapter in CHAPTERS:
        if any(r["chapter"] == chapter for r in today):
            chapter_snap = snap["chapters"][chapter]
            lines += ["", f"## {'当天' if daily else '期间'}涉及章节累计｜{chapter}", "",
                      statistics_markdown(chapter_snap), "",
                      f"一刷累计完成：{chapter_snap['a_completed']}题",
                      "章节错题池：",
                      *(f"- {name}：单选{p['single']}，多选{p['multiple']}"
                        for name, p in chapter_snap["pools"].items()),
                      f"章节App错题本快照：单选{chapter_snap['wrongbook']['single']}，多选{chapter_snap['wrongbook']['multiple']}"]
    lines += ["", "各章节App错题本快照合计：", f"- 单选：{wrongbook['single']}",
              f"- 多选：{wrongbook['multiple']}",
              f"- 总计：{sum(wrongbook.values())}", "",
              "## 数据一致性检查", "",
              f"数据库逻辑错题数：{sum(logical.values())}",
              f"章节快照合计：{sum(wrongbook.values())}",
              f"状态：{'正常' if consistent else '⚠ 数据不一致'}", "",
              f"## {label}原始备注", ""]
    notes = [r["note"] for r in today if r["note"]]
    lines += ["用户备注：", *(f"- {note}" for note in notes)] if notes else ["用户备注：无"]
    lines += ["", "## 供大模型分析的数据摘要", "",
              f"{label}完成："]
    for stage in FORMAL_STAGES:
        count = sum(r["attempted_single"]+r["attempted_multiple"] for r in today if r["stage"] == stage)
        if count:
            lines.append(f"- {stage}：{count}题")
    lines += ["", f"{label}各阶段正确率："]
    for stage in FORMAL_STAGES:
        stage_rows = [r for r in today if r["stage"] == stage]
        for kind, label in (("single", "单选"), ("multiple", "多选")):
            attempted = sum(r[f"attempted_{kind}"] for r in stage_rows)
            if attempted:
                correct = sum(r[f"correct_{kind}"] for r in stage_rows)
                lines.append(f"- {stage}{label}：{rate(correct, attempted)}（{attempted}题）")
                if attempted < 5:
                    lines.append(f"  - 注意：{stage}{label}样本仅{attempted}题。")
    lines += ["", "请结合历史趋势判断：单选、多选掌握情况，错题复刷效果，刷题节奏与复习权重。", ""]
    return "\n".join(lines)


def write_daily_reports(path=DEFAULT_DB, output_dir=None):
    output_dir = Path(output_dir or ROOT / "reports" / "daily")
    output_dir.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        dates = [r[0] for r in db.execute("SELECT DISTINCT session_date FROM sessions ORDER BY session_date")]
        for day in dates:
            snap = snapshot(db, day)
            content = daily_markdown(day, snap)
            raw_events = [{**dict(r), "payload": json.loads(r["payload"])} for r in db.execute(
                "SELECT * FROM events WHERE event_date <= ? ORDER BY id", (day,))]
            for suffix, data in (("md", content), ("json", json.dumps({
                "date": day, "daily_sessions": [r for r in snap["sessions"] if r["session_date"] == day],
                "snapshot": snap, "raw_events_through_date": raw_events,
            }, ensure_ascii=False, indent=2))):
                target = output_dir / f"{day}.{suffix}"
                temp = target.with_suffix(f".{suffix}.tmp")
                temp.write_text(data, encoding="utf-8")
                temp.replace(target)
    for file in output_dir.iterdir():
        if file.suffix in (".md", ".json") and file.stem not in dates:
            try:
                import datetime
                datetime.date.fromisoformat(file.stem)
            except ValueError:
                continue
            file.unlink()
    write_phase_snapshots(path, output_dir.parent / "snapshots")
    write_period_reports(path, output_dir.parent)


def period_report(db, start, end, report_type="自选时间段报"):
    start, end = validate_period(start, end)
    snap = snapshot(db, end)
    sessions = [r for r in snap["sessions"] if start <= r["session_date"] <= end]
    raw_events = [{**dict(r), "payload": json.loads(r["payload"])} for r in db.execute(
        "SELECT * FROM events WHERE event_date <= ? ORDER BY id", (end,))]
    return period_markdown(start, end, snap, report_type), {
        "report_type": report_type, "start_date": start, "end_date": end,
        "period_sessions": sessions, "period_totals": _totals(sessions),
        "snapshot": snap, "raw_events_through_date": raw_events,
    }


def _save_period_report(db, start, end, report_type, folder):
    content, data = period_report(db, start, end, report_type)
    folder.mkdir(parents=True, exist_ok=True)
    for suffix, value in (("md", content), ("json", json.dumps(data, ensure_ascii=False, indent=2))):
        target = folder / f"{start}_{end}.{suffix}"
        temp = target.with_suffix(f".{suffix}.tmp")
        temp.write_text(value, encoding="utf-8")
        temp.replace(target)


def save_custom_report(start, end, path=DEFAULT_DB, output_dir=None):
    start, end = validate_period(start, end)
    folder = Path(output_dir or ROOT / "reports" / "custom")
    with connect(path) as db:
        _save_period_report(db, start, end, "自选时间段报", folder)
    return folder / f"{start}_{end}.md"


def write_period_reports(path=DEFAULT_DB, output_dir=None):
    root = Path(output_dir or ROOT / "reports")
    with connect(path) as db:
        dates = [r[0] for r in db.execute("SELECT DISTINCT session_date FROM sessions")]
        for report_type, name in (("周报", "weekly"), ("月报", "monthly")):
            folder = root / name
            folder.mkdir(parents=True, exist_ok=True)
            periods = {period_bounds(day, report_type) for day in dates}
            expected = {f"{start}_{end}.{suffix}" for start, end in periods for suffix in ("md", "json")}
            for start, end in sorted(periods):
                _save_period_report(db, start, end, report_type, folder)
            for file in folder.iterdir():
                if file.suffix in (".md", ".json") and file.name not in expected:
                    try:
                        validate_period(*file.stem.split("_"))
                    except (ValueError, TypeError):
                        continue
                    file.unlink()
        # Saved custom ranges stay available and are refreshed after edits/undo.
        for file in (root / "custom").glob("*.md"):
            try:
                start, end = validate_period(*file.stem.split("_"))
            except (ValueError, TypeError):
                continue
            _save_period_report(db, start, end, "自选时间段报", file.parent)


def write_phase_snapshots(path=DEFAULT_DB, output_dir=None):
    output_dir = Path(output_dir or ROOT / "reports" / "snapshots")
    output_dir.mkdir(parents=True, exist_ok=True)
    expected = set()
    with connect(path) as db:
        transitions = db.execute("SELECT * FROM events WHERE kind='start_b' ORDER BY id").fetchall()
        for event in transitions:
            payload = json.loads(event["payload"])
            chapter = payload["chapter"]
            snap = snapshot(db, before_event_id=event["id"], chapter=chapter)
            base = payload["baseline_completed"]
            target = output_dir / f"start_b_{event['id']}_{event['event_date']}.md"
            expected.add(target.name)
            content = "\n".join([
                f"# 开始二刷阶段快照｜{chapter}｜{event['event_date']}", "",
                f"一刷已完成：{snap['a_completed']}题", "",
                "## 一刷结束时统计", "", statistics_markdown(snap), "",
                "## 一刷剩余错题池", "",
                *(f"- {name}：单选{snap['pools'][name]['single']}，多选{snap['pools'][name]['multiple']}"
                  for name in ("A1", "A2", "A3+")), "",
                f"二刷基准：单选{base['single']}，多选{base['multiple']}", "",
            ])
            temp = target.with_suffix(".md.tmp")
            temp.write_text(content, encoding="utf-8")
            temp.replace(target)
    for file in output_dir.glob("start_b_*.md"):
        if file.name not in expected:
            file.unlink()


def sessions_csv(db):
    rows = [dict(r) for r in db.execute("SELECT * FROM sessions ORDER BY id")]
    if not rows:
        return ""
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return "\ufeff" + stream.getvalue()


def sessions_json(db):
    return json.dumps([dict(r) for r in db.execute("SELECT * FROM sessions ORDER BY id")],
                      ensure_ascii=False, indent=2)
