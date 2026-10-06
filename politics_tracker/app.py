"""Start with: streamlit run app.py"""

from datetime import date
import json
from uuid import uuid4

import streamlit as st

from src.backup import backup
from src.calculations import FORMAL_STAGES, POOLS, STAGE_NAMES, Pair, ValidationError, rate
from src.chapters import CHAPTERS
from src.structured_import import form_values
from src.database import (ROOT, add_session, connect, initialize, preview,
                          integrity_check, read_settings, rebuild, start_b, state, undo_last, update_settings)
from src.reports import (period_bounds, period_report, save_custom_report,
                         sessions_csv, sessions_json, snapshot, statistics_markdown, write_daily_reports)

st.set_page_config(page_title="考研政治刷题统计", page_icon="📘", layout="wide")
initialize()
backup()


def refresh():
    write_daily_reports()
    st.rerun()


def metric_table(snap):
    rows = []
    for stage in FORMAL_STAGES:
        data = snap["totals"][stage]
        def cell(kind):
            item = data[kind]
            return f"{item['correct']}/{item['attempted']} · {rate(item['correct'], item['attempted'])}"
        rows.append({"阶段": f"{stage} {STAGE_NAMES[stage]}", "总体": cell("total"),
                     "单选": cell("single"), "多选": cell("multiple")})
    st.table(rows)


with connect() as db:
    snap = snapshot(db)
    settings = read_settings(db)
    try:
        integrity_check(db)
    except ValidationError as exc:
        st.error(str(exc))
        if st.button("从事件日志重新计算全部统计"):
            rebuild()
            refresh()
        st.stop()

st.title("考研政治刷题统计")
st.caption("本地事件日志 · 自动计算 · 日报 / 周报 / 月报 / 自选时间段报")
page = st.sidebar.radio("导航", ["概览", "新建记录", "历史与报表", "导出与维护"])

if page == "概览":
    chapter_filter = st.selectbox("章节筛选", ("全部", *CHAPTERS))
    with connect() as db:
        snap = snapshot(db, chapter=None if chapter_filter == "全部" else chapter_filter)
    bank_total = int(settings["question_bank_total"])
    st.subheader(f"{settings['question_bank_name']} 一刷｜{chapter_filter}")
    a_done = snap["a_completed"]
    st.metric("已完成 / 总题量", f"{a_done} / {bank_total}", f"{rate(a_done, bank_total)}")
    st.progress(min(a_done / bank_total, 1.0))
    if chapter_filter != "全部":
        st.caption("总题量为整本题库题量，此比例表示本章节已完成题目占整本题库的比例。")
    st.subheader("六轨正确率")
    metric_table(snap)
    st.subheader("当前错题池与章节快照")
    wrongbook = Pair(**snap["wrongbook"])
    pools = {name: Pair(**values) for name, values in snap["pools"].items()}
    st.metric("章节App错题本快照合计" if chapter_filter == "全部" else "本章节App错题本快照",
              f"{wrongbook.total} 题", f"单选 {wrongbook.single} · 多选 {wrongbook.multiple}")
    st.table([{"错题池": name, "单选": pools[name].single,
               "多选": pools[name].multiple, "合计": pools[name].total} for name in POOLS])
    st.info("完成量与正确率由历史Session聚合。一次记录只选择一个章节和一个阶段。")

elif page == "新建记录":
    st.subheader("新建刷题记录")
    chapter = st.selectbox("章节", CHAPTERS, key="record_chapter")
    with connect() as db:
        phase, completed, wrongbook, pools = state(db, chapter)
    options = [s for s in ("A0", "A1", "A2", "AX", "B0", "B1", "B2", "BX")
               if s[0] == phase or (phase == "B" and s in ("A1", "A2", "AX"))]
    stage = st.selectbox("今天做什么？", options, format_func=lambda s: f"{s} {STAGE_NAMES[s]}",
                         key=f"record_stage_{chapter}")
    prefix = f"record_{chapter}_{stage}_"
    st.text_area("结构化数据导入", key="structured_text", height=180,
                 placeholder="[POLITICS]\nstage=A0\nchapter=马原\nsingle_completed=68\nmultiple_completed=58\nwrong_single=18\nwrong_multiple=32")
    st.caption("wrong_single / wrong_multiple 表示本章节刷后App错题本余额；完成数表示当前累计。解析只填表，请查看预览后确认保存。")

    def fill_structured():
        try:
            values = form_values(st.session_state["structured_text"], chapter, stage)
        except ValidationError as exc:
            st.session_state["import_message"] = (False, str(exc))
            return
        # Callback executes before the next render; selectors are never overwritten.
        if stage.endswith("0"):
            st.session_state[prefix + "mode"] = "当前累计"
        for field in ("single_completed", "multiple_completed", "wrong_single", "wrong_multiple",
                      "rewrong_single", "rewrong_multiple"):
            if field in values:
                st.session_state[prefix + field] = values[field]
        st.session_state["import_message"] = (True, "已填充表单，尚未保存。请检查本轮预览后点击“确认保存”。")

    st.button("解析并填充表单", on_click=fill_structured)
    if "import_message" in st.session_state:
        ok, message = st.session_state["import_message"]
        (st.success if ok else st.error)(message)

    st.caption(f"刷题前App错题本：单选 {wrongbook.single}，多选 {wrongbook.multiple}。")
    if stage.endswith("0"):
        completed_mode = st.radio("新题完成数录入方式", ("本次新增", "当前累计"), horizontal=True, key=prefix + "mode")
        st.caption(f"数据库当前累计：单选 {completed.single}，多选 {completed.multiple}。")
        c1, c2 = st.columns(2)
        if completed_mode == "本次新增":
            completed_input = Pair(
                c1.number_input("本次新增单选题数", min_value=0, step=1, key=prefix + "increment_single"),
                c2.number_input("本次新增多选题数", min_value=0, step=1, key=prefix + "increment_multiple"))
            after_completed = Pair(completed.single + completed_input.single,
                                   completed.multiple + completed_input.multiple)
            st.caption(f"保存后的累计完成数：单选 {after_completed.single}，多选 {after_completed.multiple}。")
        else:
            completed_input = Pair(
                c1.number_input("当前累计单选完成数", min_value=0, value=completed.single, step=1, key=prefix + "single_completed"),
                c2.number_input("当前累计多选完成数", min_value=0, value=completed.multiple, step=1, key=prefix + "multiple_completed"))
            after_completed = completed_input
        rewrong = Pair(0, 0)
    else:
        after_completed = None
        completed_input = None
        completed_mode = None
        if stage in ("AX", "BX"):
            st.caption("顽固题维护只记录从错题本清除的题数，不计入六轨正确率。")
            rewrong = Pair(0, 0)
        else:
            c1, c2 = st.columns(2)
            rewrong = Pair(c1.number_input("本轮单选再错数", min_value=0, step=1, key=prefix + "rewrong_single"),
                           c2.number_input("本轮多选再错数", min_value=0, step=1, key=prefix + "rewrong_multiple"))
    c1, c2 = st.columns(2)
    after_wrongbook = Pair(
        c1.number_input("刷后App错题本单选数", min_value=0, value=wrongbook.single, step=1, key=prefix + "wrong_single"),
        c2.number_input("刷后App错题本多选数", min_value=0, value=wrongbook.multiple, step=1, key=prefix + "wrong_multiple"))
    session_day = st.date_input("记录日期", value=date.today())
    note = st.text_area("备注（可选）")
    attachment = st.file_uploader("截图（可选，仅作证据）", type=["png", "jpg", "jpeg", "webp"])
    try:
        with connect() as db:
            result = preview(db, stage, after_wrongbook, after_completed, rewrong, chapter)
        st.subheader("本轮预览")
        st.table([{"题型": label, "作答": n, "正确": c, "错误": w,
                   "正确率": rate(c, n) if stage not in ("AX", "BX") else "不计入统计"}
                  for label, n, c, w in (
                      ("单选", result.attempted.single, result.correct.single, result.wrong.single),
                      ("多选", result.attempted.multiple, result.correct.multiple, result.wrong.multiple),
                      ("总体", result.attempted.total, result.correct.total, result.wrong.total))])
        valid = result.attempted.total > 0
        if not valid:
            st.info("请输入本轮刷题后的数字。")
    except ValidationError as exc:
        st.error(str(exc))
        valid = False
    if st.button("确认保存", type="primary", disabled=not valid):
        attachment_path = None
        try:
            if attachment:
                folder = ROOT / "attachments"
                folder.mkdir(parents=True, exist_ok=True)
                target = folder / f"{session_day.isoformat()}_{uuid4().hex}.{attachment.name.rsplit('.', 1)[-1].lower()}"
                target.write_bytes(attachment.getvalue())
                attachment_path = str(target.relative_to(ROOT))
            add_session(stage, after_wrongbook, after_completed, rewrong,
                        session_day.isoformat(), chapter, note, attachment_path,
                        completed_input_mode="incremental" if completed_mode == "本次新增" else "cumulative",
                        completed_input=completed_input)
        except (ValidationError, OSError) as exc:
            if attachment_path:
                (ROOT / attachment_path).unlink(missing_ok=True)
            st.error(str(exc))
        else:
            st.success("记录已保存。")
            refresh()

elif page == "历史与报表":
    st.subheader("历史记录")
    rows = snap["sessions"]
    st.dataframe([{
        "ID": r["id"], "日期": r["session_date"], "章节": r["chapter"], "阶段": r["stage"],
        "单选作答": r["attempted_single"], "多选作答": r["attempted_multiple"],
        "正确": r["correct_single"]+r["correct_multiple"],
        "错误": r["wrong_single"]+r["wrong_multiple"], "备注": r["note"],
    } for r in reversed(rows)], hide_index=True, width="stretch")
    dates = sorted({r["session_date"] for r in rows}, reverse=True)
    report_type = st.radio("报表类型", ("日报", "周报", "月报", "自选时间段报"), horizontal=True)
    if report_type == "自选时间段报":
        c1, c2 = st.columns(2)
        start = c1.date_input("开始日期", value=date.fromisoformat(dates[-1]) if dates else date.today())
        end = c2.date_input("结束日期", value=date.today())
        if start > end:
            st.error("开始日期不能晚于结束日期。")
            st.stop()
        start, end = start.isoformat(), end.isoformat()
    elif report_type == "日报" and dates:
        start = end = st.selectbox("查看日报", dates)
    else:
        reference = st.date_input("选择周内任一天" if report_type == "周报" else
                                  "选择月内任一天" if report_type == "月报" else "选择日期",
                                  value=date.fromisoformat(dates[0]) if dates else date.today())
        start, end = period_bounds(reference, report_type)
    st.caption(f"统计日期：{start} 至 {end}（含起止日）。周报按周一至周日，月报按自然月。累计数据截至所选期末。")
    with connect() as db:
        report, report_data = period_report(db, start, end, report_type)
    if not report:
        st.info("该日期没有刷题记录。")
    else:
        filename = start if report_type == "日报" else f"{start}_{end}"
        st.download_button(f"下载 Markdown {report_type}", report, f"{filename}.md", "text/markdown")
        st.download_button(f"下载 JSON {report_type}", json.dumps(report_data, ensure_ascii=False, indent=2),
                           f"{filename}.json", "application/json")
        if report_type == "自选时间段报" and st.button("保存此时间段报表到本地"):
            target = save_custom_report(start, end)
            st.success(f"已保存 Markdown 和 JSON：{target.parent}")
        st.markdown(report)

else:
    st.subheader("导出")
    with connect() as db:
        st.download_button("全部 Session CSV", sessions_csv(db), "sessions.csv", "text/csv")
        st.download_button("全部 Session JSON", sessions_json(db), "sessions.json", "application/json")
    st.download_button("当前统计 Markdown", "# 当前统计\n\n" + statistics_markdown(snap) + "\n",
                       "statistics.md", "text/markdown")
    if st.button("准备 SQLite 数据库导出"):
        export_path = backup(manual=True)
        st.session_state["db_export"] = export_path.read_bytes()
    if "db_export" in st.session_state:
        st.download_button("下载 SQLite 数据库", st.session_state["db_export"],
                           "politics.db", "application/vnd.sqlite3")
    st.divider()
    st.subheader("题库设置")
    with st.form("settings"):
        name = st.text_input("题库名称", value=settings["question_bank_name"])
        total = st.number_input("总题量", min_value=1, value=int(settings["question_bank_total"]), step=1)
        if st.form_submit_button("保存题库设置"):
            try:
                update_settings(name, total)
                refresh()
            except ValidationError as exc:
                st.error(str(exc))
    st.divider()
    st.subheader("维护")
    maintenance_chapter = st.selectbox("维护章节", CHAPTERS)
    with connect() as db:
        phase, _, _, _ = state(db, maintenance_chapter)
    if phase == "A":
        with st.expander("开始二刷阶段"):
            st.write("先确认本章节一刷已结束。输入App此时显示的本章节二刷累计完成数，后续B0将以此作差。")
            c1, c2 = st.columns(2)
            base = Pair(c1.number_input("二刷基准单选", min_value=0, step=1),
                        c2.number_input("二刷基准多选", min_value=0, step=1))
            if st.button("建立二刷基准"):
                try:
                    start_b(base, chapter=maintenance_chapter)
                    refresh()
                except ValidationError as exc:
                    st.error(str(exc))
    if st.button("手动备份数据库"):
        st.success(f"已保存：{backup(manual=True)}")
    if st.button("重新计算全部统计"):
        try:
            rebuild()
            refresh()
        except ValidationError as exc:
            st.error(str(exc))
    if st.checkbox("我确认要撤销最近一次操作"):
        if st.button("撤销最近一次操作", type="secondary"):
            try:
                undone = undo_last()
                st.success(f"已撤销 {undone['kind']} #{undone['id']}")
                refresh()
            except ValidationError as exc:
                st.error(str(exc))
