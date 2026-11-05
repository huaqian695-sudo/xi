"""引擎的注入缝：模型客户端和工具执行器都能从外面换。

为什么要有这个文件：`core → support` 之前是写死的，能改掉不等于改对了。
这里同时钉两件事：
1. 传了 llm_factory / tool_executor 就真的用它（不然注入是装饰）
2. 什么都不传时旧写法 `Engine(cfg)` 还能跑工具（不然兼容是假的）
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.engine import Engine  # noqa: E402
from scenes.base import Turn  # noqa: E402
from support.tools.base import ToolResult, ToolSpec  # noqa: E402


class FakeLLM:
    """按顺序吐准备好的回复，记下它收到的对话。"""

    model = "fake-model"
    provider_name = "fake"

    def __init__(self, replies):
        self.replies = list(replies)
        self.seen = []

    def complete(self, convo, max_tokens=None, tools=None):
        self.seen.append({"convo": [dict(m) for m in convo], "tools": tools})
        return self.replies.pop(0)


def reply(text: str, tool_calls=None) -> dict:
    return {
        "text": text,
        "model": "fake-model",
        "provider": "fake",
        "usage": {"in": 1, "out": 1},
        "finish": "stop",
        "tool_calls": tool_calls,
    }


def tool_call(name: str, raw_args: str) -> dict:
    return {"id": f"c-{name}", "function": {"name": name, "arguments": raw_args}}


class TestEngineInjection(unittest.TestCase):
    def test_llm_factory_is_used(self):
        made = []

        def factory(cfg, tier, persona, session):
            made.append((tier, persona, session))
            return FakeLLM([reply("在的")])

        engine = Engine(None, llm_factory=factory)
        turn = Turn(turn_id="t-inject", text="在吗")
        out = asyncio.run(
            engine.run(
                [{"role": "user", "content": "在吗"}], tier="chat.reply", turn=turn
            )
        )

        self.assertEqual(out["output"], "在的")
        self.assertEqual(made, [("chat.reply", "xi", "t-inject")])
        self.assertEqual(out["calls"][0]["tier"], "chat.reply")

    def test_injected_executor_runs_tools(self):
        used = []

        def executor(spec, args, tool_ctx):
            used.append((spec.name, args, tool_ctx.persona_id))
            return SimpleNamespace(ok=True, text=f"{args.get('x', 0) * 2}")

        llm = FakeLLM(
            [
                reply("", [tool_call("double", '{"x": 21}')]),
                reply("算好了，42。"),
            ]
        )
        engine = Engine(
            None, llm_factory=lambda *a, **kw: llm, tool_executor=executor
        )
        spec = SimpleNamespace(
            name="double",
            to_openai=lambda: {
                "type": "function",
                "function": {"name": "double", "parameters": {"properties": {}}},
            },
        )

        out = asyncio.run(
            engine.run(
                [{"role": "user", "content": "21 的两倍"}],
                tier="chat.reply",
                turn=Turn(turn_id="t-tool", text="21 的两倍"),
                tools=[spec],
            )
        )

        self.assertEqual(used, [("double", {"x": 21}, "xi")])
        self.assertEqual(out["output"], "算好了，42。")
        self.assertEqual(out["tool_uses"][0]["name"], "double")
        self.assertTrue(out["tool_uses"][0]["ok"])
        # 工具结果必须回到对话里，模型才看得见
        tool_msgs = [m for m in llm.seen[1]["convo"] if m.get("role") == "tool"]
        self.assertEqual(tool_msgs[0]["content"], "42")
        self.assertEqual(tool_msgs[0]["tool_call_id"], "c-double")

    def test_defaults_still_work_without_injection(self):
        """旧写法 Engine(cfg) 必须还能跑工具——默认实现没被改坏。"""
        llm = FakeLLM(
            [
                reply("", [tool_call("echo", '{"text": "嗨"}')]),
                reply("她说嗨。"),
            ]
        )
        engine = Engine(None, llm_factory=lambda *a, **kw: llm)
        spec = ToolSpec(
            name="echo",
            description="原样返回",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            func=lambda ctx, text: ToolResult(True, f"回声：{text}"),
        )

        out = asyncio.run(
            engine.run(
                [{"role": "user", "content": "试一下"}],
                tier="chat.reply",
                turn=Turn(turn_id="t-default", text="试一下"),
                tools=[spec],
            )
        )

        self.assertEqual(out["output"], "她说嗨。")
        self.assertEqual(out["tool_uses"][0]["output"], "回声：嗨")
        self.assertTrue(out["tool_uses"][0]["ok"])


if __name__ == "__main__":
    unittest.main()
