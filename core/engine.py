"""引擎：按档位发模型。

M2 只有单次调用；工具循环在 M3 长出来，接口不用变——
场景始终只看到 run(messages, tier=..., toolbox=..., budget=...)。
"""

from __future__ import annotations

import asyncio
from typing import Any

from .llm import LLM, LLMError

__all__ = ["Engine", "LLMError"]


class Engine:
    def __init__(self, cfg, persona: str = "xi"):
        self.cfg = cfg
        self.persona = persona

    async def run(
        self,
        messages: list[dict[str, Any]],
        *,
        tier: str,
        toolbox: str = "封装",
        budget: dict[str, Any] | None = None,
        turn=None,
        scene: str = "",
    ) -> dict[str, Any]:
        budget = budget or {}
        max_tokens = int(budget.get("token") or 0) or None
        llm = LLM(self.cfg, tier, self.persona, session=getattr(turn, "turn_id", None))

        def _call():
            return llm.complete(messages, max_tokens=max_tokens)

        try:
            result = await asyncio.to_thread(_call)
        except LLMError as exc:
            call = {
                "tier": tier,
                "model": llm.model,
                "provider": llm.provider_name,
                "scene": scene,
                "prompt": messages,
                "output": "",
                "ok": False,
                "error": str(exc),
            }
            return {"output": "", "calls": [call], "tool_uses": [], "error": str(exc)}

        call = {
            "tier": tier,
            "model": result.get("model"),
            "provider": result.get("provider"),
            "scene": scene,
            "prompt": messages,
            "output": result.get("text"),
            "reasoning": result.get("reasoning"),
            "usage": result.get("usage"),
            "finish": result.get("finish"),
            "ok": True,
        }
        return {"output": result.get("text") or "", "calls": [call], "tool_uses": []}
