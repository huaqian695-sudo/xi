"""引擎：按档位发模型。

M2 只有单次调用；工具循环在 M3 长出来，接口不用变——
场景始终只看到 run(messages, tier=..., toolbox=..., budget=...)。

依赖方向：core 不认 support。模型客户端和执行器都从外面注入：
    Engine(cfg, llm_factory=..., tool_executor=...)
不传就用本模块的默认实现，旧调用 `Engine(cfg)` 行为不变。
默认实现里那一处 support 延迟导入是唯一的退路，契约测试盯着它（见
support/tests/test_boundaries.py），正式装配由 main.py 显式注入。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable

from .llm import LLM, LLMError
from .parsing import parse_args

__all__ = ["Engine", "LLMError", "ToolContext"]


@dataclass
class ToolContext:
    """工具能碰到的东西。比 SceneContext 轻——工具不需要拼 prompt 的能力。"""

    cfg: Any
    persona_id: str = "xi"
    channel: Any = None       # 有通道就能真的发出去；没有就只落盘
    turn: Any = None


def default_llm_factory(cfg, tier: str, persona: str, session: str | None = None):
    """默认模型客户端。换客户端（本地模型、录制回放）就传自己的 factory。"""
    return LLM(cfg, tier, persona, session=session)


def default_tool_executor(spec, args: dict[str, Any], tool_ctx: ToolContext):
    """默认工具执行器：退回到 support 的统一入口。

    这是 core 里**唯一**还认 support 的地方，而且是延迟导入（import 时不触发）。
    保留它是为了 `Engine(cfg)` 这种老写法还能跑；正式装配在 main.py 里注入，
    不需要时就删掉这个函数和契约测试里的豁免项。
    """
    from support.tools.base import execute

    return execute(spec, args, tool_ctx)


class Engine:
    def __init__(
        self,
        cfg,
        persona: str = "xi",
        channel=None,
        *,
        llm_factory: Callable[..., Any] | None = None,
        tool_executor: Callable[..., Any] | None = None,
    ):
        """llm_factory(cfg, tier, persona, session) -> 有 complete() 的客户端。

        tool_executor(spec, args, ToolContext) -> 有 .ok / .text 的结果对象。
        """
        self.cfg = cfg
        self.persona = persona
        self.channel = channel
        self.llm_factory = llm_factory or default_llm_factory
        self.tool_executor = tool_executor or default_tool_executor

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
        budget = budget or {}
        max_tokens = int(budget.get("token") or 0) or None
        max_steps = int(budget.get("工具步") or 6)
        llm = self.llm_factory(
            self.cfg, tier, self.persona, getattr(turn, "turn_id", None)
        )
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
                    outcome = self.tool_executor(
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
