"""留痕的时机：调用一完成就写，不等整轮结束。

要解决的问题（2026-11-05 实测踩到）：

    一条消息走了查证链，卡了 120 秒后超时，
    账本里 runs=0、tools=0，那两分钟干了什么完全查不到。

所以现在：
- 引擎每完成一次模型调用/工具执行，**立刻**写 runs/tools
- 每次调用**开始前**写一条 call.start（卡住时，这就是最后一条线索）
- 收到消息立刻写 turn.start（进程崩了也知道这条来过）
- main._record 见到 run_id / call_id 就跳过，避免重复
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core import db  # noqa: E402
from core.config import Config  # noqa: E402
from core.engine import Engine  # noqa: E402
from core.rawlog import RawLog  # noqa: E402
from scenes.base import RecipeScene, SceneContext, Turn, load_scenes  # noqa: E402
from support.channel.base import Incoming  # noqa: E402
from support.tests.fakes import FakeEngine  # noqa: E402


def reply(text: str = "好") -> dict:
    return {
        "text": text,
        "model": "fake-model",
        "provider": "fake",
        "usage": {"in": 10, "out": 5, "total": 15},
        "finish": "stop",
        "tool_calls": [],
    }


class FakeLLM:
    model = "fake-model"
    provider_name = "fake"

    def __init__(self, replies=None):
        self.replies = list(replies or [reply()])

    def complete(self, convo, tools=None):
        return self.replies.pop(0)


class SlowLLM:
    model = "slow-model"
    provider_name = "fake"

    def complete(self, convo, tools=None):
        import time

        time.sleep(0.6)
        return reply("太慢了")


class TestEngineTracing(unittest.TestCase):
    def test_start_is_written_before_finish(self):
        traced = []
        engine = Engine(
            None,
            llm_factory=lambda *a, **kw: FakeLLM(),
            tracer=lambda kind, rec: traced.append((kind, rec)),
        )
        asyncio.run(
            engine.run(
                [{"role": "user", "content": "在吗"}],
                tier="chat.reply",
                turn=Turn(turn_id="t1", text="在吗"),
                scene="chat.reply",
            )
        )

        kinds = [k for k, _ in traced]
        self.assertEqual(kinds, ["events", "runs"])
        start = traced[0][1]
        self.assertEqual(start["kind"], "call.start")
        self.assertEqual(start["detail"]["what"], "llm")
        self.assertEqual(start["detail"]["turn_id"], "t1")

        run = traced[1][1]
        self.assertEqual(run["run_id"], "t1-chat.reply-r1")
        self.assertEqual(run["turn_id"], "t1")
        self.assertIn("duration_ms", run)

    def test_tool_execution_is_traced(self):
        traced = []
        engine = Engine(
            None,
            llm_factory=lambda *a, **kw: FakeLLM(
                [
                    {
                        "text": "",
                        "model": "m",
                        "provider": "p",
                        "usage": {},
                        "finish": "tool_calls",
                        "tool_calls": [
                            {"id": "c1", "function": {"name": "echo", "arguments": "{}"}}
                        ],
                    },
                    reply("好了"),
                ]
            ),
            tracer=lambda kind, rec: traced.append((kind, rec)),
            tool_executor=lambda spec, args, ctx: _Echo(),
        )
        from support.tools.base import ToolSpec

        spec = ToolSpec(
            name="echo",
            description="回显",
            parameters={"type": "object", "properties": {}},
            func=lambda ctx: None,
        )
        asyncio.run(
            engine.run(
                [{"role": "user", "content": "x"}],
                tier="chat.reply",
                turn=Turn(turn_id="t2"),
                scene="chat.qa",
                tools=[spec],
            )
        )

        whats = [
            r["detail"]["what"] for k, r in traced if k == "events" and r["kind"] == "call.start"
        ]
        self.assertEqual(whats, ["llm", "tool", "llm"])
        tools = [r for k, r in traced if k == "tools"]
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["tool"], "echo")
        self.assertEqual(tools[0]["call_id"], "t2-chat.qa-t2")

    def test_timeout_leaves_a_start_but_no_result(self):
        """卡住时：有"开工"，没有"结果"——这就是要能查到的样子。"""
        traced = []
        engine = Engine(
            None,
            llm_factory=lambda *a, **kw: SlowLLM(),
            tracer=lambda kind, rec: traced.append((kind, rec)),
        )
        ctx = _ctx(engine)
        turn = Turn(turn_id="t3", text="在吗")

        class Dummy(RecipeScene):
            name = "chat.reply"

        with self.assertRaises(asyncio.TimeoutError):
            asyncio.run(Dummy().call_engine({"档位": "chat.reply", "超时": 0.05}, [], turn, ctx))

        kinds = [k for k, _ in traced]
        self.assertIn("events", kinds, "至少要有开工记录")
        self.assertNotIn("runs", kinds, "卡住的那次不该有结果记录")
        start = next(r for k, r in traced if k == "events")
        self.assertEqual(start["kind"], "call.start")


class _Echo:
    ok = True
    text = "回声"


def _ctx(engine) -> SceneContext:
    cfg = Config(ROOT)
    tmp = tempfile.mkdtemp()
    cfg.override("config/core.yml", {"paths": {"store": str(Path(tmp) / "store")}})
    return SceneContext(cfg=cfg, scenes_dir=ROOT / "scenes", rawlog=None, engine=engine)


class TestNoDuplicate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Config(ROOT)
        self.cfg.override(
            "config/core.yml",
            {"paths": {"store": str(Path(self.tmp.name) / "store")}},
        )
        db.ensure_all(self.cfg)
        self.raw = RawLog(self.cfg.store_root())
        self.raw.ensure_day()
        self.scenes = load_scenes(ROOT / "scenes")

    def tearDown(self):
        self.raw.flush()

    def test_record_skips_already_traced_calls(self):
        """引擎已经写过的，_record 不能再写一遍。"""
        import main

        class R:
            chain = ["chat.reply"]
            blocks = []
            label = None
            facts = {}
            tool_uses = [
                {"call_id": "t-x-t1", "name": "echo", "ok": True, "output": "r"}
            ]
            calls = [
                {"run_id": "t-x-r1", "tier": "chat.reply", "output": "hi", "ok": True}
            ]

        turn = Turn(turn_id="t-x", text="在吗", conversation="direct:1")
        main._record(self.cfg, turn, R(), "hi", None, self.raw)
        self.raw.flush()

        self.assertEqual(self.raw.tail("runs", 10), [], "重复写了 runs")
        self.assertEqual(self.raw.tail("tools", 10), [], "重复写了 tools")

    def test_handle_writes_turn_start(self):
        import main

        ctx = SceneContext(
            cfg=self.cfg,
            scenes_dir=ROOT / "scenes",
            rawlog=self.raw,
            engine=FakeEngine(reply="嗯"),
        )
        msg = Incoming(
            person_id=1, person_name="浅华", is_owner=True,
            conversation="direct:owner", text="你好",
        )
        asyncio.run(main.handle(msg, self.scenes, ctx, self.raw))
        self.raw.flush()

        events = self.raw.tail("events", 50)
        starts = [e for e in events if e.get("kind") == "turn.start"]
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0]["detail"]["text"], "你好")
        self.assertEqual(starts[0]["detail"]["person_id"], 1)


class TestFailureOutput(unittest.TestCase):
    """模型调用失败时引擎返回空文本——不能把空话发出去。"""

    def test_empty_reply_falls_back_to_a_sentence(self):
        from types import SimpleNamespace

        import main

        result = SimpleNamespace(reply=SimpleNamespace(output=""), chain=[], facts=None)
        self.assertEqual(main._output_text(result, None), main.FALLBACK_LINE)

    def test_normal_reply_passes_through(self):
        from types import SimpleNamespace

        import main

        result = SimpleNamespace(reply=SimpleNamespace(output="在呢"), chain=[], facts=None)
        self.assertEqual(main._output_text(result, None), "在呢")


if __name__ == "__main__":
    unittest.main()
