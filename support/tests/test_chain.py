"""前置链：四种走法都要走通。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core import chain  # noqa: E402
from core.config import Config  # noqa: E402
from core.rawlog import RawLog  # noqa: E402
from scenes.base import SceneContext, Turn, load_scenes  # noqa: E402
from support.mock_llm import MockEngine  # noqa: E402


class TestChain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(ROOT)
        self.cfg.override(
            "config/core.yml",
            {"paths": {"store": str(Path(self.tmp.name) / "store")}},
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
        self.raw.flush()
        self.tmp.cleanup()

    def _run(self, text: str, images=None):
        turn = Turn(
            turn_id="t1",
            chat_type="private",
            chat_id=self.ctx.admin_id,
            user_id=self.ctx.admin_id,
            text=text,
            images=list(images or []),
        )
        return turn, __import__("asyncio").run(chain.run(turn, self.scenes, self.ctx))

    def test_no_facts_no_image(self):
        turn, result = self._run("今天好累")
        self.assertEqual(result.label.need_facts, "no")
        self.assertEqual(result.chain, ["label", "chat.reply"])

    def test_facts_triggers_qa(self):
        turn, result = self._run("明天天气怎么样")
        self.assertEqual(result.label.need_facts, "yes")
        self.assertIn("chat.qa", result.chain)
        self.assertEqual(result.chain[-1], "chat.reply")
        self.assertIsNotNone(result.facts)

    def test_uncertain_also_qa(self):
        turn, result = self._run("这个好吃吗")
        self.assertEqual(result.label.need_facts, "uncertain")
        self.assertIn("chat.qa", result.chain)

    def test_image_goes_to_image_scene(self):
        turn, result = self._run("你看这个", images=["(假图)"])
        self.assertEqual(result.chain[-1], "chat.image")

    def test_image_plus_facts(self):
        turn, result = self._run("这里天气怎么样", images=["(假图)"])
        self.assertEqual(result.chain, ["label", "chat.qa", "chat.image"])

    def test_label_written_back_to_turn(self):
        turn, result = self._run("今天好累")
        self.assertIsNotNone(turn.label)
        self.assertEqual(turn.label["need_facts"], "no")


if __name__ == "__main__":
    unittest.main()


class TestFactsBlock(unittest.TestCase):
    """回归：曾经「有结论」是硬编码 False，事实块永远拼不进去。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(ROOT)
        self.cfg.override(
            "config/core.yml",
            {"paths": {"store": str(Path(self.tmp.name) / "store")}},
        )
        self.scenes = load_scenes(ROOT / "scenes")
        self.ctx = SceneContext(
            cfg=self.cfg, scenes_dir=ROOT / "scenes", rawlog=None, engine=MockEngine()
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_facts_means_false(self):
        turn = Turn(turn_id="t", text="x")
        self.assertFalse(self.ctx.when("有结论", turn))
        self.assertTrue(self.ctx.when("无结论", turn))

    def test_with_facts_means_true(self):
        turn = Turn(
            turn_id="t",
            text="x",
            facts={"facts": [{"statement": "明天下午有阵雨", "source": "open-meteo"}]},
        )
        self.assertTrue(self.ctx.when("有结论", turn))
        self.assertFalse(self.ctx.when("无结论", turn))
        text = self.ctx.fetch({"产品": "4.事实块"}, turn)
        self.assertIn("明天下午有阵雨", text)
        self.assertIn("open-meteo", text)

    def test_empty_facts_list_counts_as_no_conclusion(self):
        turn = Turn(turn_id="t", text="x", facts={"facts": [], "not_found": ["a"]})
        self.assertFalse(self.ctx.when("有结论", turn))

    def test_facts_reach_the_reply_recipe(self):
        """装配层面：有结论时，出话菜谱里那两个条件块真的会出现。"""
        from scenes.base import assemble

        turn = Turn(
            turn_id="t",
            text="明天天气",
            facts={"facts": [{"statement": "明天有雨", "source": "open-meteo"}]},
        )
        recipe = self.ctx.recipe("chat.reply")
        messages, used = assemble(recipe, turn, self.ctx)
        self.assertIn("产品:4.事实块", used)
        self.assertIn("常量:事实纪律", used)
        self.assertIn("明天有雨", messages[0]["content"])
