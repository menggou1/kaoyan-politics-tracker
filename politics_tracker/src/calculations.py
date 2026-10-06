"""Pure, integer-only session calculations. No UI or database code belongs here."""

from dataclasses import dataclass

FORMAL_STAGES = ("A0", "A1", "A2", "B0", "B1", "B2")
STAGES = FORMAL_STAGES + ("AX", "BX")
POOLS = ("A1", "A2", "A3+", "B1", "B2", "B3+")
STAGE_NAMES = {
    "A0": "一刷新题", "A1": "一刷一次错题", "A2": "一刷二次错题",
    "AX": "一刷顽固题维护", "B0": "二刷新题", "B1": "二刷一次错题",
    "B2": "二刷二次错题", "BX": "二刷顽固题维护",
}


class ValidationError(ValueError):
    """A submitted event cannot be reconciled with the ledger."""


@dataclass(frozen=True)
class Pair:
    single: int
    multiple: int

    def __post_init__(self):
        if any(type(v) is not int or v < 0 for v in (self.single, self.multiple)):
            raise ValidationError("单选和多选数量必须是非负整数。")

    @property
    def total(self):
        return self.single + self.multiple

    def as_dict(self):
        return {"single": self.single, "multiple": self.multiple}


@dataclass(frozen=True)
class Result:
    stage: str
    before_completed: Pair
    after_completed: Pair
    before_wrongbook: Pair
    after_wrongbook: Pair
    rewrong: Pair
    attempted: Pair
    correct: Pair
    wrong: Pair
    pools: dict[str, Pair]


def empty_pools():
    return {name: Pair(0, 0) for name in POOLS}


def pool_total(pools):
    return Pair(sum(p.single for p in pools.values()),
                sum(p.multiple for p in pools.values()))


def check_balance(wrongbook: Pair, pools: dict[str, Pair]):
    logical = pool_total(pools)
    if logical != wrongbook:
        raise ValidationError(
            f"数据不一致：App错题本 单选{wrongbook.single}/多选{wrongbook.multiple}/总计{wrongbook.total}；"
            f"数据库错题池 单选{logical.single}/多选{logical.multiple}/总计{logical.total}；"
            f"差值 单选{wrongbook.single-logical.single:+d}/多选{wrongbook.multiple-logical.multiple:+d}。"
            "请检查是否有未记录的刷题操作。"
        )


def calculate(stage: str, before_completed: Pair, before_wrongbook: Pair,
              pools: dict[str, Pair], after_wrongbook: Pair,
              after_completed: Pair | None = None, rewrong: Pair | None = None) -> Result:
    if stage not in STAGES:
        raise ValidationError(f"未知阶段：{stage}")
    if set(pools) != set(POOLS):
        raise ValidationError("错题池结构不完整。")
    check_balance(before_wrongbook, pools)
    rewrong = rewrong or Pair(0, 0)
    updated = dict(pools)
    if stage in ("A0", "B0"):
        if after_completed is None:
            raise ValidationError("新题阶段需要填写App累计完成数。")
        if rewrong.total:
            raise ValidationError("新题阶段不能输入再错数。")
        attempts = []
        wrongs = []
        for kind in ("single", "multiple"):
            n = getattr(after_completed, kind) - getattr(before_completed, kind)
            w = getattr(after_wrongbook, kind) - getattr(before_wrongbook, kind)
            if n < 0 or w < 0 or w > n:
                raise ValidationError(f"{kind}：累计完成数或错题本变化不合法，错误数不能超过作答数。")
            attempts.append(n)
            wrongs.append(w)
        attempted = Pair(*attempts)
        wrong = Pair(*wrongs)
        correct = Pair(attempted.single-wrong.single, attempted.multiple-wrong.multiple)
        destination = stage[0] + "1"
        old = updated[destination]
        updated[destination] = Pair(old.single+wrong.single, old.multiple+wrong.multiple)
    else:
        if after_completed is not None and after_completed != before_completed:
            raise ValidationError("错题复刷时不能更改累计新题完成数。")
        after_completed = before_completed
        source = stage if stage in ("A1", "A2", "B1", "B2") else stage[0] + "3+"
        if stage in ("AX", "BX") and rewrong.total:
            raise ValidationError("顽固题维护只记录从错题本清除的题数。")
        cleared = Pair(before_wrongbook.single-after_wrongbook.single,
                       before_wrongbook.multiple-after_wrongbook.multiple) if (
                           after_wrongbook.single <= before_wrongbook.single and
                           after_wrongbook.multiple <= before_wrongbook.multiple) else None
        if cleared is None:
            raise ValidationError("错题复刷后，App错题本不能增加。")
        attempted = Pair(cleared.single+rewrong.single, cleared.multiple+rewrong.multiple)
        available = updated[source]
        if attempted.single > available.single or attempted.multiple > available.multiple:
            raise ValidationError(
                f"{source}待刷池不足：待刷 单选{available.single}/多选{available.multiple}，"
                f"本轮实际作答 单选{attempted.single}/多选{attempted.multiple}。"
            )
        updated[source] = Pair(available.single-attempted.single,
                               available.multiple-attempted.multiple)
        correct = cleared
        wrong = rewrong
        if stage in ("A1", "B1", "A2", "B2"):
            destination = stage[0] + ("2" if stage.endswith("1") else "3+")
            old = updated[destination]
            updated[destination] = Pair(old.single+wrong.single, old.multiple+wrong.multiple)
    check_balance(after_wrongbook, updated)
    return Result(stage, before_completed, after_completed, before_wrongbook,
                  after_wrongbook, rewrong, attempted, correct, wrong, updated)


def rate(correct: int, attempted: int) -> str:
    return f"{correct/attempted:.2%}" if attempted else "-"
