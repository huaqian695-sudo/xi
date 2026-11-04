"""整理器：把 raw 的一行变成能拼进 prompt 的聊天记录。

分工：
- `store/raw/turns.jsonl` 是真相：一轮一行，含"他说了什么、她回了什么、这轮怎么走的"
- `interactions` 是整理出来的对话稿：一轮拆成"他一句 + 她一句"

为什么要有这一步：拼历史要的是"一条一条的对话"，而 raw 是"一轮一轮的账"。
拆开、排好、带上说话人，才拼得进 prompt。

同步、容错：写完 raw 立刻整理；整理失败只记一条事件，不挡回复。
"""

from __future__ import annotations

from typing import Any


def from_turn(cfg, record: dict[str, Any], raw=None) -> int:
    """从 turns.jsonl 的一行生成 interactions。返回写了几条。

    幂等：同一个 turn_id 重复整理不会写重（先查后写）。
    """
    from core import clock, repo

    turn_id = str(record.get("turn_id") or "")
    if not turn_id:
        return 0

    channel = str(record.get("channel") or "stub")
    chat_type = str(record.get("chat_type") or "private")
    chat_id = str(record.get("chat_id") or "")
    session_no = int(record.get("session_no") or 1)
    ts = str(record.get("ts") or clock.iso())

    try:
        if repo.turn_rows(cfg, turn_id):
            return 0  # 已经整理过

        saved = 0

        # 他说的（防抖合并时可能有好几条）
        for p in record.get("participants") or []:
            text = str(p.get("text") or "").strip()
            if not text:
                continue
            repo.add_interaction(
                cfg,
                turn_id=turn_id,
                actor="他",
                kind="消息",
                content=text,
                channel=channel,
                chat_type=chat_type,
                chat_id=chat_id,
                session_no=session_no,
                user_id=str(p.get("user_id") or ""),
                nickname=str(p.get("nickname") or ""),
                image_refs=record.get("image_refs") or [],
                ts=ts,
            )
            saved += 1

        # 她说的
        reply = str(record.get("reply") or "").strip()
        if reply:
            repo.add_interaction(
                cfg,
                turn_id=turn_id,
                actor="她",
                kind="消息",
                content=reply,
                channel=channel,
                chat_type=chat_type,
                chat_id=chat_id,
                session_no=session_no,
                ts=ts,
            )
            saved += 1

        return saved
    except Exception as exc:  # noqa: BLE001 - 整理失败不挡回复
        if raw is not None:
            raw.append(
                "events",
                {"ts": clock.iso(), "kind": "error", "detail": f"整理交互流失败：{exc}"},
            )
        return 0


def rebuild(cfg, raw=None, days: int | None = None) -> int:
    """从 raw 重建 interactions（表空了、或者改了整理规则时用）。

    只重放 turns.jsonl，不碰其他账本。
    """
    import json

    from core import clock

    total = 0
    root = cfg.store_root() / "raw"
    if not root.exists():
        return 0

    day_dirs = sorted(p for p in root.iterdir() if p.is_dir())
    if days:
        day_dirs = day_dirs[-days:]

    for day in day_dirs:
        path = day / "turns.jsonl"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += from_turn(cfg, record, raw=raw)
        clock.now()  # noqa: B018 - 保持时钟调用位置可见
    return total
