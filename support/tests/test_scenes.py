"""场景注册与菜谱校验。

不联网、不读真模型，只验证"加一个场景只加一个文件夹"这条约束。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.config import Config  # noqa: E402
from scenes.base import SceneContext, Turn, load_scenes  # noqa: E402


class TestScenes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenes = load_scenes(ROOT / "scenes")
        cls.cfg = Config(ROOT)
        cls.ctx = SceneContext(
            cfg=cls.cfg, scenes_dir=ROOT / "scenes", rawlog=None, engine=None
        )

    def test_basic_scenes_loaded(self):
        self.assertIn("chat.reply", self.scenes)
        self.assertIn("chat.qa", self.scenes)

    def test_every_recipe_loads_and_matches_name(self):
        for name in self.scenes:
            recipe = self.ctx.recipe(name)
            self.assertEqual(recipe.get("场景"), name)
            self.assertIn("档位", recipe)

    def test_every_recipe_tier_exists(self):
        tiers = self.cfg.tiers.get("tiers") or {}
        for name in self.scenes:
            recipe = self.ctx.recipe(name)
            self.assertIn(recipe["档位"], tiers, f"{name} 的档位不存在")

    def test_blocks_are_known_kinds(self):
        known = {"人格", "此刻", "状态", "产品", "常量", "工具箱", "当前", "动作"}
        for name in self.scenes:
            recipe = self.ctx.recipe(name)
            for spec in (
                list(recipe.get("块") or [])
                + list(recipe.get("用户") or [])
                + list(recipe.get("历史") or [])
            ):
                self.assertTrue(
                    known & set(spec), f"{name} 里有不认识的块：{spec}"
                )

    def test_fetch_persona_and_message(self):
        turn = Turn(turn_id="t1", text="在吗")
        self.assertIn("汐", self.ctx.fetch({"人格": "self.md"}, turn))
        self.assertEqual(self.ctx.fetch({"当前": "消息"}, turn), "在吗")

    def test_condition_predicates(self):
        private_admin = Turn(turn_id="t", person_id=1, is_owner=True, conversation="direct:owner")
        private_stranger = Turn(turn_id="t", person_id=2, is_owner=False, conversation="direct:2")
        group = Turn(turn_id="t", person_id=1, is_owner=True, conversation="group:999")
        self.assertFalse(self.ctx.when("非管理员私聊", private_admin))
        self.assertTrue(self.ctx.when("非管理员私聊", private_stranger))
        self.assertTrue(self.ctx.when("群聊", group))
        self.assertFalse(self.ctx.when("带图", private_admin))


if __name__ == "__main__":
    unittest.main()
