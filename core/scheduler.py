"""到点叫她：心跳循环。

它只做三件事：看表、判该不该醒、叫一次。**不解释、不判断内容**——
内容归 `scenes/tick/`，能不能开口归 `core/guards.py`。

一条纪律（大纲 §8.2）：不因为刚聊过就额外多想。判断只在这里，
不看模型心情。旧项目一上午烧掉 8 万 token，就是这么来的。
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from . import clock, guards

# 每分钟看一次表。用固定间隔而不是"算到下一个整点"，
# 因为电脑休眠/改时钟之后，算出来的时间会飘。
CHECK_EVERY = 60


async def loop(
    cfg,
    ctx,
    scenes: dict[str, Any],
    raw=None,
    persona: str = "xi",
    on_speak: Callable[[str], Awaitable[None]] | None = None,
) -> None:
    """一直转。收到 CancelledError 就干净退出（main 的 finally 会 cancel）。"""
    from pipeline import heartbeat

    while True:
        try:
            ready, why = guards.should_tick(cfg, persona)
            if ready:
                # 先记账再干活：万一 tick 崩了，也不会每分钟重试一次
                guards.mark_tick(cfg, persona)
                outcome = await heartbeat.run_once(
                    cfg, ctx, scenes, raw=raw, persona=persona
                )
                if outcome.get("allowed") and outcome.get("say") and on_speak:
                    await on_speak(outcome["say"])
                    guards.mark_spoke(cfg, persona)
            elif raw is not None and why.startswith("距上次心跳"):
                pass  # 正常情况，不记流水，免得账本被刷屏
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 一轮心跳失败不能停掉整个循环
            if raw is not None:
                raw.append(
                    "events",
                    {
                        "ts": clock.iso(),
                        "kind": "error",
                        "detail": f"心跳失败：{type(exc).__name__}: {str(exc) or '(没有消息)'}",
                    },
                )

        await asyncio.sleep(CHECK_EVERY)
