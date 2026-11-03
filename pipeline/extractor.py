"""提炼器：60 秒一轮，把新对话熬成记忆。

三条规矩：
1. 整批原子——这批里任一条写库失败，游标不推进，下一轮重来
2. 判重——相似的跳过，同一句话说两遍只留一条
3. 不悄悄吞——解析不出东西就记一条事件，游标仍推进（那是模型的问题，重试也没用）
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from core import clock, repo
from scenes.base import Turn

CURSOR_TEMPLATE = "processed/5-memories/{persona}.cursor"


def cursor_path(cfg, persona: str = "xi") -> Path:
    return cfg.store_root() / CURSOR_TEMPLATE.format(persona=persona)


def read_cursor(cfg, persona: str = "xi") -> int:
    path = cursor_path(cfg, persona)
    if not path.exists():
        return 0
    try:
        return int(path.read_text(encoding="utf-8").strip() or 0)
    except ValueError:
        return 0


def write_cursor(cfg, last_id: int, persona: str = "xi") -> None:
    path = cursor_path(cfg, persona)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(int(last_id)), encoding="utf-8")


def format_batch(rows: list[dict]) -> str:
    """按大纲附录 F.1 的格式：一行一轮，工具行为用括号。"""
    lines = []
    for row in rows:
        ts = str(row.get("ts") or "")[:16].replace("T", " ")
        actor = row.get("actor")
        kind = row.get("kind")
        content = row.get("content") or ""
        if kind == "消息":
            who = "浅华" if actor == "他" else "汐"
            lines.append(f"[{ts}] {who}: {content}")
        else:
            lines.append(f"[{ts}] （汐{kind}：{content}）")
    return "\n".join(lines)


async def run_once(cfg, ctx, scenes: dict, raw=None, persona: str = "xi") -> int:
    """跑一轮提炼，返回写了几条记忆。"""
    cursor = read_cursor(cfg, persona)
    rows = repo.since_id(cfg, cursor, persona=persona, limit=100)
    if not rows:
        return 0

    scene = scenes.get("memory.extract")
    if scene is None:
        return 0

    text = format_batch(rows)
    turn = Turn(
        turn_id=f"extract-{clock.now():%Y%m%d%H%M%S}",
        persona_id=persona,
        scene="memory.extract",
        trigger="batch",
        text=text,
    )

    result = await scene.run(turn, ctx)
    items = result.output or []

    if items and not (result.meta or {}).get("raw_ok", True):
        if raw is not None:
            raw.append(
                "events",
                {"ts": clock.iso(), "kind": "error", "detail": "提炼输出解析不出来"},
            )

    saved = 0
    try:
        for item in items:
            if repo.similar_exists(
                cfg, item["content"], kind=item["kind"], persona=persona
            ):
                continue
            repo.add_memory(
                cfg,
                content=item["content"],
                kind=item["kind"],
                status=item["status"],
                source=item.get("source"),
                persona=persona,
            )
            saved += 1
    except Exception as exc:  # noqa: BLE001 - 整批原子，失败不推进游标
        if raw is not None:
            raw.append(
                "events",
                {"ts": clock.iso(), "kind": "error", "detail": f"写记忆失败，游标不推进：{exc}"},
            )
        raise

    write_cursor(cfg, int(rows[-1]["id"]), persona)
    return saved


async def loop(cfg, ctx, scenes: dict, raw=None, interval: int = 60, persona: str = "xi") -> None:
    while True:
        try:
            await run_once(cfg, ctx, scenes, raw=raw, persona=persona)
        except Exception as exc:  # noqa: BLE001
            if raw is not None:
                raw.append(
                    "events",
                    {"ts": clock.iso(), "kind": "error", "detail": f"提炼轮次失败：{exc}"},
                )
        await asyncio.sleep(interval)
