"""菜谱全量校验：写错了要在启动时就拦住。"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.config import Config  # noqa: E402
from core.validate import validate  # noqa: E402
from scenes.base import load_scenes  # noqa: E402


class TestValidate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.scenes_dir = Path(self.tmp.name) / "scenes"
        shutil.copytree(ROOT / "scenes", self.scenes_dir)
        self.cfg = Config(ROOT)

    def tearDown(self):
        self.tmp.cleanup()

    def _validate(self):
        scenes = load_scenes(self.scenes_dir)
        return validate(self.cfg, self.scenes_dir, scenes)

    def test_current_recipes_pass(self):
        self.assertEqual(self._validate(), [])

    def test_bad_tier_reported(self):
        path = self.scenes_dir / "chat_reply" / "recipe.yml"
        text = path.read_text(encoding="utf-8").replace("档位: chat.reply", "档位: 不存在")
        path.write_text(text, encoding="utf-8")
        problems = self._validate()
        self.assertTrue(any("不存在的档位" in p for p in problems), problems)

    def test_bad_product_reported(self):
        path = self.scenes_dir / "chat_reply" / "recipe.yml"
        text = path.read_text(encoding="utf-8").replace("产品: 5.记忆库", "产品: 99.不存在")
        path.write_text(text, encoding="utf-8")
        problems = self._validate()
        self.assertTrue(any("不存在的产品号" in p for p in problems), problems)

    def test_bad_constant_reported(self):
        path = self.scenes_dir / "chat_reply" / "recipe.yml"
        text = path.read_text(encoding="utf-8").replace("常量: 标记与表情", "常量: 没有这个")
        path.write_text(text, encoding="utf-8")
        problems = self._validate()
        self.assertTrue(any("不存在的常量" in p for p in problems), problems)

    def test_bad_predicate_reported(self):
        path = self.scenes_dir / "chat_reply" / "recipe.yml"
        text = path.read_text(encoding="utf-8").replace("当: 有结论", "当: 有结论吗")
        path.write_text(text, encoding="utf-8")
        problems = self._validate()
        self.assertTrue(any("不认识的条件" in p for p in problems), problems)

    def test_name_mismatch_reported(self):
        path = self.scenes_dir / "chat_reply" / "recipe.yml"
        text = path.read_text(encoding="utf-8").replace("场景: chat.reply", "场景: chat.wrong")
        path.write_text(text, encoding="utf-8")
        problems = self._validate()
        self.assertTrue(any("「场景」写的是" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
