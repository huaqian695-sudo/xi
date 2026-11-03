"""真实语料：从旧项目只读若干条真实对话。

为什么用它：手写用例想不出真实的错别字、省略、半句话、突然换话题。
拿真消息跑一遍，能发现"设计时想不到"的崩法。

三条规矩：

1. **只读，不复制**。数据留在旧库里，不落进本仓库、不进 git。
2. **库不在就跳过**。测试必须能独立跑，不能依赖隔壁项目。
3. **不打印原文**。只打印数量和统计——日志里不该出现别人说过的话。
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

LEGACY_DB = Path(os.path.expanduser("~/deepseek/xi-core/data/memory.db"))


def available() -> bool:
    return LEGACY_DB.exists()


def load_legacy_messages(limit: int = 20, *, skip_commands: bool = True) -> list[dict[str, Any]]:
    """取旧项目里「他说的」最近若干条，转成能喂给通道的形状。

    只取 role='user'：我们要测的是"收到一句话之后怎么处理"。
    """
    if not available():
        return []

    try:
        con = sqlite3.connect(f"file:{LEGACY_DB}?mode=ro", uri=True)
    except sqlite3.Error:
        return []

    try:
        rows = con.execute(
            """
            SELECT chat_type, chat_id, user_id, content, ts
            FROM messages
            WHERE role = 'user'
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(limit * 3, limit),),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()

    out: list[dict[str, Any]] = []
    for chat_type, chat_id, user_id, content, ts in rows:
        text = (content or "").strip()
        if not text:
            continue
        if skip_commands and text.startswith("/"):
            continue
        # 太长的不适合当一条消息
        if len(text) > 300:
            continue
        out.append(
            {
                "chat_type": chat_type or "private",
                "chat_id": int(chat_id or 0),
                "user_id": int(user_id or 0),
                "text": text,
                "ts": ts,
            }
        )
        if len(out) >= limit:
            break
    return out
