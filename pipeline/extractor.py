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


def format_batch(rows: list[dict], cfg=None, persona: str = "xi") -> str:
    """按大纲附录 F.1 的格式：一行一轮，工具行为用括号。

    说话人只认 `person_id`（0 = 汐），名字从 people 表现查——不再靠
    "他/她"这种文字标签判断，也就不用把主人名字写死在代码里。
    """
    from core import repo
    from core.db import SELF_ID

    self_name = "汐"
    if cfg is not None:
        self_name = str((cfg.persona(persona) or {}).get("name") or "汐")

    names: dict[int, str] = {}

    def name_of(pid: int) -> str:
        if pid == SELF_ID:
            return self_name
        if pid not in names:
            person = repo.get_person(cfg, pid, persona=persona) if cfg is not None else None
            names[pid] = str((person or {}).get("name") or f"{pid}号")
        return names[pid]

    lines = []
    for row in rows:
        ts = str(row.get("ts") or "")[:16].replace("T", " ")
        pid = int(row.get("person_id") or 0)
        kind = row.get("kind")
        content = row.get("content") or ""
        who = name_of(pid)
        if kind == "消息":
            lines.append(f"[{ts}] {who}: {content}")
        else:
            lines.append(f"[{ts}] （{who}{kind}：{content}）")
    return "\n".join(lines)


async def run_once(
    cfg,
    ctx,
    scenes: dict,
    raw=None,
    persona: str = "xi",
    vec_store: bool = True,
    batch: int = 60,
) -> int:
    """提炼一批（默认 60 轮对话），返回写了几条记忆。"""
    cursor = read_cursor(cfg, persona)
    rows = repo.since_id(cfg, cursor, persona=persona, limit=batch)
    if not rows:
        return 0

    scene = scenes.get("memory.extract")
    if scene is None:
        return 0

    text = format_batch(rows, cfg, persona)
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

    # 批次里出现过的人：名字 → 人员 id。模型只说名字，id 由程序补
    people: dict[str, int] = {}
    for row in rows:
        pid = int(row.get("person_id") or 0)
        if not pid:
            continue
        person = repo.get_person(cfg, pid, persona=persona) or {}
        name = str(person.get("name") or "").strip()
        if name:
            people[name] = pid
        people.setdefault(f"{pid}号", pid)

    # 兜底：模型没标 speaker 时，按这一批里说话的人补上（先找主人）
    default_pid = 0
    for pid in people.values():
        person = repo.get_person(cfg, pid, persona=persona) or {}
        if person.get("is_owner"):
            default_pid = pid
            break
    if not default_pid and people:
        default_pid = next(iter(people.values()))

    for item in items:
        who = str(item.get("speaker") or "").strip()
        if who in people:
            item["speaker_id"] = people[who]
        elif who == "汐":
            item["speaker_id"] = 0
        else:
            item["speaker_id"] = default_pid

    fresh = [
        item
        for item in items
        if not repo.similar_exists(
            cfg, item["content"], kind=item["kind"], persona=persona
        )
    ]
    new_ids: list[int] = []
    try:
        # 整批一个事务：任一条写失败，这批一条都不落
        new_ids = repo.add_memories_bulk(cfg, fresh, persona=persona)
        saved = len(new_ids)
    except Exception as exc:  # noqa: BLE001 - 整批原子，失败不推进游标
        if raw is not None:
            raw.append(
                "events",
                {"ts": clock.iso(), "kind": "error", "detail": f"写记忆失败，游标不推进：{exc}"},
            )
        raise

    # 批量补向量（失败的会一直缺，检索时自动退回关键词）
    if new_ids and vec_store:
        try:
            from core import embed

            vectors = embed.embed_texts(cfg, [it["content"] for it in fresh])
            for mid, vec in zip(new_ids, vectors):
                repo.save_embedding(cfg, mid, vec, persona=persona)
        except Exception as exc:  # noqa: BLE001
            if raw is not None:
                raw.append(
                    "events",
                    {"ts": clock.iso(), "kind": "error", "detail": f"补向量失败：{exc}"},
                )

    write_cursor(cfg, int(rows[-1]["id"]), persona)

    # 成功也要留痕：不然"今晚为什么没提炼出东西"只能靠猜
    if raw is not None:
        raw.append(
            "events",
            {
                "ts": clock.iso(),
                "kind": "extract",
                "detail": {
                    "candidates": len(items),          # 模型吐了几条
                    "duplicates": len(items) - len(fresh),  # 判重跳过几条
                    "saved": saved,                     # 真写进库几条
                    "range": [int(rows[0]["id"]), int(rows[-1]["id"])],
                },
            },
        )
    return saved


def _parse_at(at: str) -> tuple[int, int]:
    h, m = str(at or "03:00").split(":")[:2]
    return int(h), int(m)


async def drain(cfg, ctx, scenes: dict, raw=None, persona: str = "xi", batch: int = 60) -> int:
    """把积压的对话一次提炼完：一批批跑，直到没有新对话。

    为什么分批：一天几百轮塞进一次调用会撑爆上下文。
    为什么一次跑完：只跑一批的话，积压会越堆越多，第二天更跑不动。
    """
    total = 0
    for _ in range(200):  # 硬上限，别无限跑
        before = read_cursor(cfg, persona)
        try:
            saved = await run_once(
                cfg, ctx, scenes, raw=raw, persona=persona, batch=batch
            )
        except Exception as exc:  # noqa: BLE001 - 一批失败就停，剩下的明天再说
            if raw is not None:
                raw.append(
                    "events",
                    {"ts": clock.iso(), "kind": "error", "detail": f"提炼批次失败：{exc}"},
                )
            break
        total += saved
        if read_cursor(cfg, persona) == before:
            break  # 游标没动 = 没有新东西了
    return total


async def daily_loop(
    cfg,
    ctx,
    scenes: dict,
    raw=None,
    at: str = "03:00",
    batch: int = 60,
    persona: str = "xi",
) -> None:
    """每天这个点提炼一次。

    启动时先补一次——服务停过一段时间的，积压的那几天要先追平。
    之后每天到点跑。
    """
    hour, minute = _parse_at(at)

    try:
        await drain(cfg, ctx, scenes, raw=raw, persona=persona, batch=batch)
    except Exception as exc:  # noqa: BLE001
        if raw is not None:
            raw.append(
                "events",
                {"ts": clock.iso(), "kind": "error", "detail": f"启动补提炼失败：{exc}"},
            )

    while True:
        now = clock.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target = target + __import__("datetime").timedelta(days=1)
        await asyncio.sleep(max(30, (target - now).total_seconds()))
        try:
            await drain(cfg, ctx, scenes, raw=raw, persona=persona, batch=batch)
        except Exception as exc:  # noqa: BLE001
            if raw is not None:
                raw.append(
                    "events",
                    {"ts": clock.iso(), "kind": "error", "detail": f"每日提炼失败：{exc}"},
                )
