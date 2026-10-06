"""Parse one structured Session into form values. Never write to the database."""

import re

from .calculations import STAGES, ValidationError
from .chapters import validate_chapter

ALIASES = {
    "阶段": "stage", "章节": "chapter", "大章节": "chapter",
    "单选累计完成数": "single_completed", "单选完成数": "single_completed",
    "单选已完成": "single_completed", "单选累计完成": "single_completed",
    "多选累计完成数": "multiple_completed", "多选完成数": "multiple_completed",
    "多选已完成": "multiple_completed", "多选累计完成": "multiple_completed",
    "单选错题数": "wrong_single", "单选错题": "wrong_single",
    "刷后App错题本单选数": "wrong_single", "错题本单选数": "wrong_single",
    "多选错题数": "wrong_multiple", "多选错题": "wrong_multiple",
    "刷后App错题本多选数": "wrong_multiple", "错题本多选数": "wrong_multiple",
    "单选再错数": "rewrong_single", "单选再错": "rewrong_single",
    "本轮单选再错数": "rewrong_single",
    "多选再错数": "rewrong_multiple", "多选再错": "rewrong_multiple",
    "本轮多选再错数": "rewrong_multiple",
}
FIELDS = {"stage", "chapter", "single_completed", "multiple_completed",
          "wrong_single", "wrong_multiple", "rewrong_single", "rewrong_multiple"}


def parse_structured(text):
    lines = [line.strip() for line in text.strip().lstrip("\ufeff").splitlines() if line.strip()]
    if not lines or lines[0] != "[POLITICS]":
        raise ValidationError("结构化数据必须以 [POLITICS] 开头。")
    data = {}
    for line in lines[1:]:
        if "=" not in line:
            raise ValidationError(f"格式错误，应为 字段=值：{line}")
        key, value = (part.strip() for part in line.split("=", 1))
        key = ALIASES.get(key, key)
        if key not in FIELDS:
            raise ValidationError(f"未知字段：{key}")
        if key in data:
            raise ValidationError(f"字段重复：{key}")
        if key in ("chapter", "stage"):
            data[key] = value
        else:
            if not re.fullmatch(r"[0-9]+", value):
                raise ValidationError(f"{key} 必须是非负整数。")
            data[key] = int(value)
    required = {"stage", "chapter", "wrong_single", "wrong_multiple"}
    missing = required - data.keys()
    if missing:
        raise ValidationError("缺少字段：" + ", ".join(sorted(missing)))
    validate_chapter(data["chapter"])
    if data["stage"] not in STAGES:
        raise ValidationError("未知阶段：" + data["stage"])
    if data["stage"].endswith("0"):
        required |= {"single_completed", "multiple_completed"}
        if data.get("rewrong_single", 0) or data.get("rewrong_multiple", 0):
            raise ValidationError("新题阶段不能输入再错数。")
    else:
        if {"single_completed", "multiple_completed"} & data.keys():
            raise ValidationError("错题阶段不能导入新题完成数。")
        if data["stage"] in ("AX", "BX"):
            if data.get("rewrong_single", 0) or data.get("rewrong_multiple", 0):
                raise ValidationError("顽固题维护不能输入再错数。")
        else:
            required |= {"rewrong_single", "rewrong_multiple"}
    missing = required - data.keys()
    if missing:
        raise ValidationError("缺少字段：" + ", ".join(sorted(missing)))
    return data


def form_values(text, chapter, stage):
    data = parse_structured(text)
    conflicts = [f"{field}：文本为 {data[field]}，当前选择为 {value}"
                 for field, value in (("chapter", chapter), ("stage", stage))
                 if data[field] != value]
    if conflicts:
        raise ValidationError("结构化数据与当前选择冲突；请手动调整下拉菜单后重新解析。" + "；".join(conflicts))
    return data
