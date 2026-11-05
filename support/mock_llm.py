"""M0 的假模型。

不联网、不花钱、不读 key。它只证明一件事：
一条消息能从通道走进场景、拼出 prompt、拿到结果、再回到通道。

接真模型是 M3 的事（core/tiers.py + core/engine.py）。
"""

from __future__ import annotations

from typing import Any


class MockEngine:
    name = "mock"

    async def run(
        self,
        messages: list[dict[str, Any]],
        *,
        tier: str,
        toolbox: str,
        steps: int = 6,
        turn,
        scene: str = "",
        tools=None,
    ) -> dict[str, Any]:
        total_chars = sum(len(str(m.get("content", ""))) for m in messages)
        user_text = ""
        for m in messages:
            if m.get("role") == "user":
                user_text = str(m.get("content", ""))
        output = (
            f"（假模型收到：{len(messages)} 条消息、{total_chars} 字。"
            f"你说的是「{turn.text}」）"
        )
        call = {
            "duration_ms": 0,   # 假模型不联网，耗时记 0
            "tier": tier,
            "model": "mock",
            "scene": scene,
            "toolbox": toolbox,
            "prompt_chars": total_chars,
            "output": output,
            "prompt": messages,
            "usage": {"in": total_chars, "out": len(output)},
            "ok": True,
        }
        return {"output": output, "calls": [call], "tool_uses": []}
