"""引擎：按档位发模型。

M2 只有单次调用；工具循环在 M3 长出来，接口不用变——
场景始终只看到 run(messages, tier=..., toolbox=..., budget=...)。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .llm import LLM, LLMError

__all__ = ["Engine", "LLMError", "ToolContext"]


@dataclass
class ToolContext:
    """工具能碰到的东西。比 SceneContext 轻——工具不需要拼 prompt 的能力。"""

    cfg: Any
    persona_id: str = "xi"
    channel: Any = None       # 有通道就能真的发出去；没有就只落盘
    turn: Any = None


class Engine:
    def __init__(self, cfg, persona: str = "xi", channel=None):
        self.cfg = cfg
        self.persona = persona
        self.channel = channel

    async def run(
        self,
        messages: list[dict[str, Any]],
        *,
        tier: str,
        toolbox: str = "封装",
        budget: dict[str, Any] | None = None,
        turn=None,
        scene: str = "",
        tools: list | None = None,
    ) -> dict[str, Any]:
        """发一次模型；如果菜谱声明了工具箱，就跑工具循环。

        tools 是 ToolSpec 列表；None 或空 = 这轮不给她动手能力。
        """
        from support.tools.base import execute, parse_args

        budget = budget or {}
        max_tokens = int(budget.get("token") or 0) or None
        max_steps = int(budget.get("工具步") or 6)
        llm = LLM(self.cfg, tier, self.persona, session=getattr(turn, "turn_id", None))
        tool_map = {t.name: t for t in (tools or [])}
        tool_schema = [t.to_openai() for t in (tools or [])] or None

        convo = list(messages)
        calls: list[dict[str, Any]] = []
        tool_uses: list[dict[str, Any]] = []

        for step in range(max_steps + 1):
            def _call():
                return llm.complete(
                    convo, max_tokens=max_tokens, tools=tool_schema
                )

            try:
                result = await asyncio.to_thread(_call)
            except LLMError as exc:
                calls.append(
                    {
                        "tier": tier,
                        "model": llm.model,
                        "provider": llm.provider_name,
                        "scene": scene,
                        "prompt": convo,
                        "output": "",
                        "ok": False,
                        "error": str(exc),
                    }
                )
                return {
                    "output": "",
                    "calls": calls,
                    "tool_uses": tool_uses,
                    "error": str(exc),
                }

            requested = result.get("tool_calls") or []
            calls.append(
                {
                    "tier": tier,
                    "model": result.get("model"),
                    "provider": result.get("provider"),
                    "scene": scene,
                    "prompt": convo,
                    "output": result.get("text"),
                    "reasoning": result.get("reasoning"),
                    "usage": result.get("usage"),
                    "finish": result.get("finish"),
                    "tool_calls": len(requested),
                    "ok": True,
                }
            )

            # 没要工具 / 没给工具 / 步数用完 → 收尾
            if not requested or not tool_map or step >= max_steps:
                if requested and step >= max_steps:
                    convo.append(
                        {
                            "role": "user",
                            "content": "（工具用完了，别再调，直接用现有信息回话。）",
                        }
                    )
                    final = await asyncio.to_thread(
                        lambda: llm.complete(convo, max_tokens=max_tokens)
                    )
                    # 收尾这次也是一次真实调用，必须进账——否则 token 和调用次数都对不上
                    calls.append(
                        {
                            "tier": tier,
                            "model": final.get("model"),
                            "provider": final.get("provider"),
                            "scene": scene,
                            "prompt": convo,
                            "output": final.get("text"),
                            "reasoning": final.get("reasoning"),
                            "usage": final.get("usage"),
                            "finish": final.get("finish"),
                            "note": "步数用尽，要求收尾",
                            "ok": True,
                        }
                    )
                    return {
                        "output": final.get("text") or "",
                        "calls": calls,
                        "tool_uses": tool_uses,
                    }
                return {
                    "output": result.get("text") or "",
                    "calls": calls,
                    "tool_uses": tool_uses,
                }

            # 把模型这次的话（含工具请求）放进对话
            convo.append(
                {
                    "role": "assistant",
                    "content": result.get("text") or "",
                    "tool_calls": requested,
                }
            )

            for tc in requested:
                fn = (tc.get("function") or {})
                name = str(fn.get("name") or "")
                args = parse_args(fn.get("arguments"))
                spec = tool_map.get(name)

                if spec is None:
                    content = f"没有这个工具：{name}"
                    ok = False
                else:
                    outcome = execute(
                        spec,
                        args,
                        ToolContext(self.cfg, self.persona, channel=self.channel, turn=turn),
                    )
                    content, ok = outcome.text, outcome.ok

                tool_uses.append(
                    {
                        "name": name or "(空)",
                        "args": args,
                        "ok": ok,
                        "output": content[:2000],
                    }
                )
                convo.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id") or name,
                        "content": content,
                    }
                )

        return {"output": "", "calls": calls, "tool_uses": tool_uses}
