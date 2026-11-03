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
from core.config import Config  # noqa: E402
from core.rawlog import RawLog  # noqa: E402
from scenes.base import SceneContext, load_scenes  # noqa: E402
from support.channel.base import Incoming  # noqa: E402
from support.mock_llm import MockEngine  # noqa: E402


class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(ROOT)
        # 把 store 指到临时目录，别污染真账本
        self.cfg.override(
            "config/core.yml",
            {
                "paths": {"store": str(Path(self.tmp.name) / "store")},
                "channel": {"type": "stub"},
            },
        )
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
        self.tmp.cleanup()

    def test_reply_round_trip(self):
        from main import handle

        msg = Incoming(chat_id=self.ctx.admin_id, user_id=self.ctx.admin_id, text="明天要下雨吗")
        reply = asyncio.run(handle(msg, self.scenes, self.ctx, self.raw))

        self.assertIn("明天要下雨吗", reply)
        self.assertIn("假模型", reply)

        day = self.cfg.store_root() / "raw" / clock.day_dir()
        turns = (day / "turns.jsonl").read_text(encoding="utf-8").splitlines()
        runs = (day / "runs.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(turns), 1)
        self.assertEqual(len(runs), 1)

        # 成品留档也写了
        products = list((self.cfg.store_root() / "products").rglob("*.md"))
        self.assertTrue(products)

    def test_run_id_enumerates_not_counts_fields(self):
        """回归：曾经用 len(dict) 当序号，导致 id 是 -8 这种。"""
        import json

        from main import handle

        msg = Incoming(chat_id=self.ctx.admin_id, user_id=self.ctx.admin_id, text="你好")
        asyncio.run(handle(msg, self.scenes, self.ctx, self.raw))
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
        day = self.cfg.store_root() / "raw" / clock.day_dir()
        ids = [
            json.loads(line)["run_id"]
            for line in (day / "runs.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2)

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
