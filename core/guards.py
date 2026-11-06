"""出门前的闸：什么时候可以醒，什么时候可以开口。

两个判断，都在模型之外——**不靠她自觉**：

    should_tick()   该不该醒（静默时段 / 距上次太近）
    can_speak()     该不该主动说话（静默 / 每日上限 / 最小间隔）

为什么单独一个文件：这些是"程序对她说不行"的地方。混进场景里，
哪天模型说"我就要发"，代码里找不着拦截点。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from . import clock

DEFAULT_AWAKE = ["07:00", "00:00"]
DEFAULT_TICK_MINUTES = 30
DEFAULT_MIN_GAP = 60


def _parse_hhmm(text: str) -> tuple[int, int] | None:
    try:
        hour, minute = str(text).split(":")[:2]
        return int(hour), int(minute)
    except (ValueError, AttributeError):
        return None


def in_awake_window(awake: list[str] | None, moment: datetime | None = None) -> bool:
    """在不在醒着的时段里。支持跨零点（07:00 → 00:00）。"""
    moment = moment or clock.now()
    window = awake or DEFAULT_AWAKE
    if len(window) != 2:
        return True
    start, end = _parse_hhmm(window[0]), _parse_hhmm(window[1])
    if start is None or end is None:
        return True

    now = moment.hour * 60 + moment.minute
    lo = start[0] * 60 + start[1]
    hi = end[0] * 60 + end[1]
    if lo == hi:
        return True
    if lo < hi:
        return lo <= now < hi
    return now >= lo or now < hi


# ---------------------------------------------------------------- 记账


def book_path(cfg, persona: str = "xi") -> Path:
    return cfg.store_root() / "processed" / "6-state" / persona / "schedule.json"


def read_book(cfg, persona: str = "xi") -> dict[str, Any]:
    path = book_path(cfg, persona)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def write_book(cfg, book: dict[str, Any], persona: str = "xi") -> None:
    path = book_path(cfg, persona)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(book, ensure_ascii=False, indent=1), encoding="utf-8")


def _persona_cfg(cfg, persona: str) -> dict[str, Any]:
    try:
        return cfg.persona(persona) or {}
    except Exception:  # noqa: BLE001 - 读不到就用默认
        return {}


def _minutes_since(iso: str | None, moment: datetime) -> float | None:
    if not iso:
        return None
    try:
        then = datetime.fromisoformat(str(iso))
    except ValueError:
        return None
    return (moment - then).total_seconds() / 60


# ---------------------------------------------------------------- 该不该醒


def should_tick(cfg, persona: str = "xi", moment: datetime | None = None) -> tuple[bool, str]:
    """到点了吗。返回 (要不要醒, 原因)。"""
    moment = moment or clock.now()
    conf = _persona_cfg(cfg, persona)

    if not in_awake_window((conf.get("schedule") or {}).get("awake"), moment):
        return False, "静默时段"

    interval = int((conf.get("schedule") or {}).get("tick_minutes") or DEFAULT_TICK_MINUTES)
    book = read_book(cfg, persona)
    elapsed = _minutes_since(book.get("last_tick_at"), moment)
    if elapsed is not None and elapsed < interval:
        return False, f"距上次心跳才 {elapsed:.0f} 分钟"

    return True, "到点了"


def mark_tick(cfg, persona: str = "xi", moment: datetime | None = None) -> None:
    moment = moment or clock.now()
    book = read_book(cfg, persona)
    book["last_tick_at"] = moment.isoformat(timespec="seconds")
    write_book(cfg, book, persona)


# ---------------------------------------------------------------- 该不该开口


def can_speak(cfg, persona: str = "xi", moment: datetime | None = None) -> tuple[bool, str]:
    """三道闸：静默 / 每日上限 / 最小间隔。"""
    moment = moment or clock.now()
    conf = _persona_cfg(cfg, persona)
    proactive = conf.get("proactive") or {}

    if not in_awake_window((conf.get("schedule") or {}).get("awake"), moment):
        return False, "静默时段"

    book = read_book(cfg, persona)
    today = clock.day_dir(moment)
    if book.get("speak_day") != today:
        book["speak_day"] = today
        book["speak_count"] = 0

    limit = int(proactive.get("max_per_day") or 0)
    if limit and int(book.get("speak_count") or 0) >= limit:
        return False, f"今天已经主动说过 {limit} 次"

    gap = float(proactive.get("min_gap_minutes") or DEFAULT_MIN_GAP)
    elapsed = _minutes_since(book.get("last_speak_at"), moment)
    if elapsed is not None and elapsed < gap:
        return False, f"距上次主动开口才 {elapsed:.0f} 分钟"

    return True, "可以开口"


def mark_spoke(cfg, persona: str = "xi", moment: datetime | None = None) -> None:
    moment = moment or clock.now()
    book = read_book(cfg, persona)
    today = clock.day_dir(moment)
    if book.get("speak_day") != today:
        book["speak_day"] = today
        book["speak_count"] = 0
    book["speak_count"] = int(book.get("speak_count") or 0) + 1
    book["last_speak_at"] = moment.isoformat(timespec="seconds")
    write_book(cfg, book, persona)
