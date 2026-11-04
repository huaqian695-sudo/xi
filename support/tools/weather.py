"""查天气：先看库里有没有，没有再上网。

为什么这么设计：天气是最常问的，每天 07:00 已经预取好了今明两天。
直接读库 = 秒回、不怕网断；库里没有（问后天、问别的城市）才现查。
"""

from __future__ import annotations

import re
from typing import Any

from support.tools.base import ToolResult, ToolSpec

DAY_WORDS = ("今天", "明天", "后天")


def _target_date(day: str) -> str:
    """「今天/明天/后天」→ 具体日期。别的写法先当今天。"""
    from datetime import timedelta

    from core import clock

    offset = DAY_WORDS.index(day) if day in DAY_WORDS else 0
    return (clock.now() + timedelta(days=offset)).strftime("%Y-%m-%d")


def _from_db(ctx, city: str, date: str) -> str | None:
    """事实库里有没有这个城市这一天的天气。"""
    from core import repo

    key_head = repo.question_key(f"天气:{city}:{date}")
    for fact in repo.active_facts(ctx.cfg, limit=50, persona=ctx.persona_id):
        if repo.question_key(str(fact.get("question_key") or "")) == key_head:
            return str(fact.get("statement") or "")
    return None


def _online(ctx, city: str, days: int) -> list[dict[str, Any]]:
    from pipeline import weather as box

    return box.fetch_forecast(city, days=days)


def _store(ctx, items: list[dict[str, Any]]) -> int:
    from pipeline import weather as box

    return box.save(ctx.cfg, items, persona=ctx.persona_id)


def _weather(ctx, city: str = "北京", day: str = "今天") -> ToolResult:
    city = str(city or "").strip()
    day = str(day or "今天").strip()
    if not city:
        return ToolResult(False, "要查哪个城市？")

    date = _target_date(day)

    # ① 先读库
    hit = _from_db(ctx, city, date)
    if hit:
        return ToolResult(True, f"{hit}（来自今早的预取）", {"source": "db", "date": date})

    # ② 库里没有 → 现查
    offset = DAY_WORDS.index(day) if day in DAY_WORDS else 0
    items = _online(ctx, city, days=max(2, offset + 1))
    if not items:
        return ToolResult(False, f"查不到{city}的天气（可能是城市名不对，或者网断了）")

    saved = _store(ctx, items)
    for item in items:
        if item["date"] == date:
            from pipeline import weather as box

            return ToolResult(
                True,
                f"{box.statement(item)}（刚查的）",
                {"source": "online", "saved": saved, "date": date},
            )

    # 查到了但没这一天（比如问的是过去）
    avail = "、".join(f"{i['date']} {i['weather']}" for i in items[:3])
    return ToolResult(True, f"没有{date}的预报，能查到的是：{avail}", {"source": "online"})


TOOL = ToolSpec(
    name="weather",
    description=(
        "查天气。今明两天的数据每天 07:00 已经预取好，直接读库很快。"
        "支持「今天/明天/后天」和任意城市名。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "城市名，比如 洛阳、北京、安阳"},
            "day": {
                "type": "string",
                "enum": ["今天", "明天", "后天"],
                "description": "哪一天，默认今天",
            },
        },
        "required": ["city"],
    },
    toolbox="封装",
    func=_weather,
)
