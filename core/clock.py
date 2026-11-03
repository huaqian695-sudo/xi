"""时间、时区、自然日边界、编号生成。

所有日期算术收在这里，别散到各处——"3 个自然日"那种边界最容易错。
"""

from __future__ import annotations

import random
import string
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def now() -> datetime:
    return datetime.now(TZ)


def iso(dt: datetime | None = None) -> str:
    return (dt or now()).isoformat(timespec="seconds")


def today() -> str:
    return now().strftime("%Y-%m-%d")


def day_dir(dt: datetime | None = None) -> str:
    return (dt or now()).strftime("%Y-%m-%d")


def stamp(dt: datetime | None = None) -> str:
    """给人看的时间：11-03 15:30 周三"""
    d = dt or now()
    return f"{d:%m-%d %H:%M} {WEEKDAYS[d.weekday()]}"


def time_slot(dt: datetime | None = None) -> str:
    h = (dt or now()).hour
    if 5 <= h < 9:
        return "清早"
    if 9 <= h < 12:
        return "上午"
    if 12 <= h < 14:
        return "中午"
    if 14 <= h < 18:
        return "下午"
    if 18 <= h < 23:
        return "晚上"
    return "深夜"


def new_turn_id(dt: datetime | None = None) -> str:
    d = dt or now()
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"{d:%Y%m%d-%H%M%S}-{suffix}"


def in_quiet_hours(cfg: dict, dt: datetime | None = None) -> bool:
    """静默时段判断，支持跨零点。"""
    quiet = (cfg.get("guards") or {}).get("quiet_hours") or []
    if len(quiet) != 2:
        return False
    d = dt or now()
    cur = d.hour * 60 + d.minute

    def parse(s: str) -> int:
        h, m = str(s).split(":")
        return int(h) * 60 + int(m)

    start, end = parse(quiet[0]), parse(quiet[1])
    if start <= end:
        return start <= cur < end
    return cur >= start or cur < end
