"""引擎：按档位发模型。

M2 只有单次调用；工具循环在 M3 长出来，接口不用变——
场景始终只看到 run(messages, tier=..., toolbox=..., steps=...)。

依赖方向：core 不认 support。模型客户端和执行器都从外面注入：
    Engine(cfg, llm_factory=..., tool_executor=...)
不传就用本模块的默认实现，旧调用 `Engine(cfg)` 行为不变。
默认实现里那一处 support 延迟导入是唯一的退路，契约测试盯着它（见
support/tests/test_boundaries.py），正式装配由 main.py 显式注入。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable

from . import clock
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
        tracer: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        """llm_factory(cfg, tier, persona, session) -> 有 complete() 的客户端。

        tool_executor(spec, args, ToolContext) -> 有 .ok / .text 的结果对象。

        tracer(kind, record) 是账本入口：kind 是 events / runs / tools。
        有它就在这里**实时**记账（每次调用一完成就写），而不是等整轮结束——
        否则超时或崩溃时，已经发生的调用会一起消失。
        """
        self.cfg = cfg
        self.persona = persona
        self.channel = channel
        self.llm_factory = llm_factory or default_llm_factory
        self.tool_executor = tool_executor or default_tool_executor
        self.tracer = tracer or (lambda kind, record: None)

    def _trace(self, kind: str, record: dict[str, Any]) -> None:
        """写一条账。留痕失败绝不能挡住回复。"""
        try:
            self.tracer(kind, record)
        except Exception:  # noqa: BLE001
            pass

    async def run(
        self,
        messages: list[dict[str, Any]],
        *,
        tier: str,
        toolbox: str = "封装",
        steps: int = 6,
        turn=None,
        scene: str = "",
        tools: list | None = None,
    ) -> dict[str, Any]:
        """发一次模型；如果菜谱声明了工具箱，就跑工具循环。

        tools 是 ToolSpec 列表；None 或空 = 这轮不给她动手能力。
        """
        max_steps = int(steps or 6)
        llm = self.llm_factory(
            self.cfg, tier, self.persona, getattr(turn, "turn_id", None)
        )
        tool_map = {t.name: t for t in (tools or [])}
        tool_schema = [t.to_openai() for t in (tools or [])] or None

        convo = list(messages)
        calls: list[dict[str, Any]] = []
        tool_uses: list[dict[str, Any]] = []

        turn_id = str(getattr(turn, "turn_id", "") or "")
        scope = scene or "x"
        seq = 0

        def _start(what: str, **extra: Any) -> int:
            """记一次"开工"。卡住时，这就是最后一条线索。"""
            nonlocal seq
            seq += 1
            self._trace(
                "events",
                {
                    "ts": clock.iso(),
                    "kind": "call.start",
                    "detail": {
                        "turn_id": turn_id,
                        "scene": scene,
                        "seq": seq,
                        "what": what,
                        **extra,
                    },
                },
            )
            return seq

        for step in range(max_steps + 1):
            def _call():
                return llm.complete(convo, tools=tool_schema)

            n = _start("llm", model=llm.model, provider=llm.provider_name)
            started = time.perf_counter()
            try:
                result = await asyncio.to_thread(_call)
            except LLMError as exc:
                record = {
                    "run_id": f"{turn_id}-{scope}-r{n}",
                    "turn_id": turn_id,
                    "ts": clock.iso(),
                    "duration_ms": int((time.perf_counter() - started) * 1000),
                    "tier": tier,
                    "model": llm.model,
                    "provider": llm.provider_name,
                    "scene": scene,
                    "prompt": convo,
                    "output": "",
                    "ok": False,
                    "error": str(exc),
                }
                self._trace("runs", record)
                calls.append(record)
                return {
                    "output": "",
                    "calls": calls,
                    "tool_uses": tool_uses,
                    "error": str(exc),
                }

            requested = result.get("tool_calls") or []
            record = {
                "run_id": f"{turn_id}-{scope}-r{n}",
                "turn_id": turn_id,
                "ts": clock.iso(),
                "duration_ms": int((time.perf_counter() - started) * 1000),
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
            self._trace("runs", record)
            calls.append(record)

            # 没要工具 / 没给工具 / 步数用完 → 收尾
            if not requested or not tool_map or step >= max_steps:
                if requested and step >= max_steps:
                    convo.append(
                        {
                            "role": "user",
                            "content": "（工具用完了，别再调，直接用现有信息回话。）",
                        }
                    )
                    w = _start("llm", model=llm.model, note="收尾")
                    wrap_started = time.perf_counter()
                    final = await asyncio.to_thread(lambda: llm.complete(convo))
                    # 收尾这次也是一次真实调用，必须进账——否则 token 和调用次数都对不上
                    record = {
                        "run_id": f"{turn_id}-{scope}-r{w}",
                        "turn_id": turn_id,
                        "ts": clock.iso(),
                        "duration_ms": int((time.perf_counter() - wrap_started) * 1000),
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
                    self._trace("runs", record)
                    calls.append(record)
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

                tool_ms = 0
                t = _start("tool", tool=name or "(空)")
                if spec is None:
                    content = f"没有这个工具：{name}"
                    ok = False
                else:
                    tool_started = time.perf_counter()
                    outcome = self.tool_executor(
                        spec,
                        args,
                        ToolContext(self.cfg, self.persona, channel=self.channel, turn=turn),
                    )
                    tool_ms = int((time.perf_counter() - tool_started) * 1000)
                    content, ok = outcome.text, outcome.ok

                call_id = f"{turn_id}-{scope}-t{t}"
                self._trace(
                    "tools",
                    {
                        "call_id": call_id,
                        "turn_id": turn_id,
                        "ts": clock.iso(),
                        "tool": name or "(空)",
                        "args": args,
                        "ok": ok,
                        "duration_ms": tool_ms,
                        "output": content[:2000],
                    },
                )
                # 内存里保持老形状（name），call_id 当"已落账"的标记
                tool_uses.append(
                    {
                        "call_id": call_id,
                        "name": name or "(空)",
                        "args": args,
                        "ok": ok,
                        "duration_ms": tool_ms,
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
