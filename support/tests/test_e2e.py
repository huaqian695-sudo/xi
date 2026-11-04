"""一条消息走完全程：通道 → 场景 → 假模型 → 账本 → 回复。"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core import clock  # noqa: E402
from core import db
from core.config import Config  # noqa: E402
from core.rawlog import RawLog  # noqa: E402
from scenes.base import SceneContext, load_scenes  # noqa: E402
from support.channel.base import Incoming  # noqa: E402
from support.mock_llm import MockEngine
from support.tests.fakes import FakeEngine  # noqa: E402


class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._flushed = False
        self.cfg = Config(ROOT)
        # 把 store 指到临时目录，别污染真账本
        self.cfg.override(
            "config/core.yml",
            {
                "paths": {"store": str(Path(self.tmp.name) / "store")},
                "channel": {"type": "stub"},
            },
        )
        db.ensure_all(self.cfg)
        self.raw = RawLog(self.cfg.store_root())
        self.raw.ensure_day()
        self.scenes = load_scenes(ROOT / "scenes")
        self.ctx = SceneContext(
            cfg=self.cfg,
            scenes_dir=ROOT / "scenes",
            rawlog=self.raw,
            engine=MockEngine(),
        )

    def tearDown(self):
        self.raw.flush()
        self.tmp.cleanup()

    def test_reply_round_trip(self):
        from main import handle

        msg = Incoming(chat_id=self.ctx.admin_id, user_id=self.ctx.admin_id, text="今天好累")
        reply = asyncio.run(handle(msg, self.scenes, self.ctx, self.raw))
        self.raw.flush()

        self.assertIn("今天好累", reply)
        self.assertIn("假模型", reply)

        day = self.cfg.store_root() / "raw" / clock.day_dir()
        turns = (day / "turns.jsonl").read_text(encoding="utf-8").splitlines()
        runs = (day / "runs.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(turns), 1)
        # label + reply = 2 次调用（quick_judge 没命中"今天好累"）
        self.assertGreaterEqual(len(runs), 1)

        # 成品留档也写了
        products = list((self.cfg.store_root() / "products").rglob("*.md"))
        self.assertTrue(products)

    def test_run_id_enumerates_not_counts_fields(self):
        """回归：曾经用 len(dict) 当序号，导致 id 是 -8 这种。"""
        import json

        from main import handle

        msg = Incoming(chat_id=self.ctx.admin_id, user_id=self.ctx.admin_id, text="你好")
        asyncio.run(handle(msg, self.scenes, self.ctx, self.raw))
        self.raw.flush()
        day = self.cfg.store_root() / "raw" / clock.day_dir()
        runs = (day / "runs.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertTrue(json.loads(runs[0])["run_id"].endswith("-r1"))

    def test_multiple_calls_get_distinct_ids(self):
        """一轮里多次调用，id 不能撞。"""

        class TwoCallsEngine:
            async def run(self, messages, *, tier, toolbox, budget, turn, scene=""):
                call = {"tier": tier, "model": "mock", "scene": scene, "output": "x"}
                return {"output": "x", "calls": [dict(call), dict(call)], "tool_uses": []}

        from main import handle

        ctx = type(self.ctx)(
            cfg=self.cfg,
            scenes_dir=ROOT / "scenes",
            rawlog=self.raw,
            engine=TwoCallsEngine(),
        )
        msg = Incoming(chat_id=ctx.admin_id, user_id=ctx.admin_id, text="你好")
        asyncio.run(handle(msg, self.scenes, ctx, self.raw))
        self.raw.flush()
        day = self.cfg.store_root() / "raw" / clock.day_dir()
        ids = [
            json.loads(line)["run_id"]
            for line in (day / "runs.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertGreaterEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), len(ids), f"id 撞了：{ids}")

    def test_qa_scene_returns_facts_shape(self):
        from main import handle

        msg = Incoming(chat_id=self.ctx.admin_id, user_id=self.ctx.admin_id, text="明天几点下雨")
        # 直接跑 qa 场景，验证它返回的是结论结构而不是话
        scene = self.scenes["chat.qa"]

        async def run():
            from scenes.base import Turn

            turn = Turn(
                turn_id=clock.new_turn_id(),
                scene="chat.qa",
                chat_type="private",
                chat_id=msg.chat_id,
                user_id=msg.user_id,
                text=msg.text,
            )
            return await scene.run(turn, self.ctx)

        result = asyncio.run(run())
        self.assertIsInstance(result.output, dict)
        self.assertIn("facts", result.output)
        self.assertIn("not_found", result.output)


if __name__ == "__main__":
    unittest.main()


class TestFailureIsolation(unittest.TestCase):
    """外围动作出错不能让她闭嘴，但必须留痕。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
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
        self.tmp.cleanup()

    def test_chain_failure_returns_fallback_and_logs(self):
        from main import handle
        from support.tests.fakes import FakeEngine

        ctx = SceneContext(
            cfg=self.cfg,
            scenes_dir=ROOT / "scenes",
            rawlog=self.raw,
            engine=FakeEngine(raise_on="chat.reply"),
        )
        msg = Incoming(chat_id=ctx.admin_id, user_id=ctx.admin_id, text="你好")
        reply = asyncio.run(handle(msg, self.scenes, ctx, self.raw))
        self.raw.flush()

        self.assertIn("岔子", reply)

        day = self.cfg.store_root() / "raw" / clock.day_dir()
        errors = [
            json.loads(line)
            for line in (day / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if json.loads(line).get("kind") == "error"
        ]
        self.assertTrue(errors, "失败没有留痕")
        self.assertIn("前置链失败", errors[-1]["detail"])

        # 失败的这一轮也要入库，投递状态标成未发送
        turns = [
            json.loads(line)
            for line in (day / "turns.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(turns[-1]["delivery"]["status"], "未发送")

    def test_record_failure_does_not_break_reply(self):
        from main import handle
        from support.tests.fakes import FakeEngine

        ctx = SceneContext(
            cfg=self.cfg,
            scenes_dir=ROOT / "scenes",
            rawlog=self.raw,
            engine=FakeEngine(reply="我很好。"),
        )
        # 让整理器炸掉（模拟库坏了）
        import core.organize as org

        original = org.from_turn
        org.from_turn = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("库坏了"))
        try:
            msg = Incoming(chat_id=ctx.admin_id, user_id=ctx.admin_id, text="你好")
            reply = asyncio.run(handle(msg, self.scenes, ctx, self.raw))
        finally:
            org.from_turn = original
            self.raw.flush()

        self.assertEqual(reply, "我很好。", "整理失败不该影响回话")
        day = self.cfg.store_root() / "raw" / clock.day_dir()
        errors = [
            json.loads(line)
            for line in (day / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if json.loads(line).get("kind") == "error"
        ]
        self.assertTrue(any("记账失败" in e["detail"] and "库坏了" in e["detail"] for e in errors),
                        f"没记下记账失败：{errors}")
