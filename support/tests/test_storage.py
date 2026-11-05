"""数据落地规则：写哪儿、写什么。

两条规则都是 2026-11-05 定的，这里把它们变成可执行的断言：

1. **写哪儿**：正式库 `store/` 在项目完成前是禁区，写它必须显式
   `XI_CONFIRM_PROD=1`。测试一律走临时库。
2. **写什么**：一轮交互要能复盘——reasoning / finish / 收尾说明 /
   工具请求次数 / 每轮 usage / 耗时，都必须落到原始层。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core import db  # noqa: E402
from core.config import Config, ProdStoreRefused, guard_production_store  # noqa: E402
from core.rawlog import RawLog  # noqa: E402
from scenes.base import SceneContext, load_scenes  # noqa: E402
from support.channel.base import Incoming  # noqa: E402
from support.tests.fakes import FakeEngine  # noqa: E402


class TestProductionStoreGuard(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(ROOT)

    def test_dev_store_is_fine(self):
        guard_production_store(self.cfg)  # 默认试验库，不抛

    def test_production_store_is_refused(self):
        with mock.patch.dict(os.environ, {"XI_STORE": "store"}, clear=False):
            os.environ.pop("XI_CONFIRM_PROD", None)
            with self.assertRaises(ProdStoreRefused):
                guard_production_store(self.cfg)

    def test_confirmed_production_store_is_allowed(self):
        with mock.patch.dict(
            os.environ, {"XI_STORE": "store", "XI_CONFIRM_PROD": "1"}, clear=False
        ):
            guard_production_store(self.cfg)  # 显式确认，放行

    def test_absolute_production_path_is_refused_too(self):
        with mock.patch.dict(
            os.environ, {"XI_STORE": str(ROOT / "store")}, clear=False
        ):
            os.environ.pop("XI_CONFIRM_PROD", None)
            with self.assertRaises(ProdStoreRefused):
                guard_production_store(self.cfg)


class RichEngine(FakeEngine):
    """每次调用都带回 reasoning / finish / tool_calls —— 模拟真引擎的返回。"""

    async def run(self, *args, **kwargs):
        out = await super().run(*args, **kwargs)
        out["calls"][0].update(
            {
                "reasoning": "先看她是不是在问我状态",
                "finish": "stop",
                "tool_calls": 2,
                "usage": {"in": 100, "out": 20, "total": 120},
            }
        )
        return out


class TestRawFields(unittest.TestCase):
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
        self.ctx = SceneContext(
            cfg=self.cfg,
            scenes_dir=ROOT / "scenes",
            rawlog=self.raw,
            engine=RichEngine(),
        )
        self.scenes = load_scenes(ROOT / "scenes")

    def tearDown(self):
        self.raw.flush()

    def test_turn_records_duration_and_full_usage(self):
        import main

        msg = Incoming(
            person_id=1, person_name="浅华", is_owner=True,
            conversation="direct:owner", text="你在吗",
        )
        asyncio.run(main.handle(msg, self.scenes, self.ctx, self.raw))
        self.raw.flush()

        turn = self.raw.tail("turns", 1)[0]
        runs = self.raw.tail("runs", 100)

        self.assertIsInstance(turn["duration_ms"], int)
        self.assertGreaterEqual(turn["duration_ms"], 0)

        # 整轮汇总 = 每次调用的合计，而不是只算第一轮
        self.assertEqual(turn["result"]["calls"], len(runs))
        self.assertEqual(
            turn["result"]["usage"]["in"], sum(r["usage"]["in"] for r in runs)
        )
        self.assertEqual(
            turn["result"]["usage"]["out"], sum(r["usage"]["out"] for r in runs)
        )
    def test_usage_summary_adds_up_across_tool_loop(self):
        """多轮工具循环时，整轮 tokens 是合计，不是第一轮。"""
        import main

        got = main._usage_summary(
            [
                {"usage": {"in": 100, "out": 20, "total": 120}},
                {"usage": {"in": 50, "out": 5, "total": 55}},
            ]
        )
        self.assertEqual(got["usage"], {"in": 150, "out": 25, "total": 175})
        self.assertEqual(got["tokens"], 150)
        self.assertEqual(got["calls"], 2)

    def test_usage_summary_fills_missing_total(self):
        """有的客户端只给 in/out，total 得自己算出来，不能记 0。"""
        import main

        got = main._usage_summary([{"usage": {"in": 10, "out": 5}}])
        self.assertEqual(got["usage"]["total"], 15)

    def test_runs_keep_reasoning_finish_and_note(self):
        import main

        msg = Incoming(
            person_id=1, person_name="浅华", is_owner=True,
            conversation="direct:owner", text="你在吗",
        )
        asyncio.run(main.handle(msg, self.scenes, self.ctx, self.raw))
        self.raw.flush()

        for run in self.raw.tail("runs", 100):
            self.assertIn("ts", run)
            self.assertEqual(run["reasoning"], "先看她是不是在问我状态")
            self.assertEqual(run["finish"], "stop")
            self.assertEqual(run["tool_calls"], 2)
            self.assertIn("note", run)  # 只有步数用尽才有值，但键必须在


if __name__ == "__main__":
    unittest.main()
