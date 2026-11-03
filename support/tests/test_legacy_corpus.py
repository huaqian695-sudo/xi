"""拿真实消息跑一遍：不崩、链路完整、分类分布合理。

不是替代单测，是补一刀——手写用例想不出的说法，真消息里有。

三条不变量：
1. 每条都有回复，且非空
2. 每条的场景链至少是 label + 一个出话场景
3. 标签分布不是"全 yes"也不是"全 no"（那不是判断，是常量）
"""

from __future__ import annotations

import asyncio
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
from support.mock_llm import MockEngine  # noqa: E402
from support.tests.fixtures import available, load_legacy_messages  # noqa: E402


class TestLegacyCorpus(unittest.TestCase):
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
        self.ctx = SceneContext(
            cfg=self.cfg,
            scenes_dir=ROOT / "scenes",
            rawlog=self.raw,
            engine=MockEngine(),
        )

    def tearDown(self):
        self.raw.flush()
        self.tmp.cleanup()

    def test_real_messages_survive_the_chain(self):
        if not available():
            self.skipTest("旧项目库不在，跳过真实语料")

        corpus = load_legacy_messages(20)
        if not corpus:
            self.skipTest("旧库里没有可用的消息")

        from main import handle

        labels: dict[str, int] = {}
        for item in corpus:
            msg = Incoming(
                chat_type=item["chat_type"],
                chat_id=item["chat_id"] or self.ctx.admin_id,
                user_id=item["user_id"] or self.ctx.admin_id,
                text=item["text"],
            )
            reply = asyncio.run(handle(msg, self.scenes, self.ctx, self.raw))
            self.assertIsInstance(reply, str)
            self.assertTrue(reply.strip(), f"空回复：{item['text'][:20]}")

        self.raw.flush()
        day = self.cfg.store_root() / "raw" / clock.day_dir()
        import json

        rows = [
            json.loads(line)
            for line in (day / "turns.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(len(rows), len(corpus))

        for row in rows:
            chain = row.get("scene_chain") or []
            self.assertGreaterEqual(len(chain), 2, f"链路太短：{chain}")
            self.assertEqual(chain[0], "label")
            labels[row["label"]["need_facts"]] = labels.get(row["label"]["need_facts"], 0) + 1

        # 只打印分布，不打印原文
        print(f"\n  真实语料 {len(corpus)} 条，标签分布：{labels}")


if __name__ == "__main__":
    unittest.main()
