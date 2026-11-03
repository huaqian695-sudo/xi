"""真模型冒烟：只有显式打开才跑（要联网、要花钱）。

    XI_REAL_LLM=1 python3 -m unittest support.tests.test_real_model -v

默认 skip。为什么单独放：M2 的假模型测试只能证明"链路不崩"，
证明不了"标注判得对"——那件事只能靠真模型。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core import db  # noqa: E402
from core.config import Config  # noqa: E402
from core.engine import Engine  # noqa: E402
from scenes.base import SceneContext, Turn, load_scenes  # noqa: E402

RUN = os.environ.get("XI_REAL_LLM") == "1"


@unittest.skipUnless(RUN, "设 XI_REAL_LLM=1 才跑真模型")
class TestRealLabel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(ROOT)
        self.cfg.override(
            "config/core.yml",
            {"paths": {"store": str(Path(self.tmp.name) / "store")}, "llm": {"use_mock": False}},
        )
        db.ensure_all(self.cfg)
        self.scenes = load_scenes(ROOT / "scenes")
        self.ctx = SceneContext(
            cfg=self.cfg, scenes_dir=ROOT / "scenes", rawlog=None, engine=Engine(self.cfg)
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _label(self, text):
        turn = Turn(turn_id="t", chat_id=1, user_id=1, text=text)
        return asyncio.run(self.scenes["label"].run(turn, self.ctx)).output

    def test_weather_needs_facts(self):
        self.assertEqual(self._label("明天要下雨吗")["need_facts"], "yes")

    def test_emotion_should_not_trigger_fact_check(self):
        # 情绪话不该被当成事实问题；判成 uncertain 是可接受的（宁可多查一次）
        self.assertNotEqual(self._label("今天好累啊")["need_facts"], "yes")

    def test_chitchat_skipped_by_rule(self):
        # 规则抢答在 chain 里先拦，不花模型调用
        from core.chain import quick_judge

        self.assertEqual(quick_judge("哈哈哈").need_facts, "no")
        self.assertEqual(quick_judge("明天天气怎么样").need_facts, "yes")

    def test_opinion_not_a_fact_question(self):
        self.assertNotEqual(self._label("你觉得我该换工作吗")["need_facts"], "yes")


if __name__ == "__main__":
    unittest.main()
