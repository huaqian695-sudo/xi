"""菜谱声明 vs 场景能力的对账。

菜谱是数据、场景是代码，两边会漂：菜谱写了「历史」却没被拼进 prompt、
菜谱写了工具箱却没把 tools 交出去。这类错不会抛异常，只会静默不生效，
所以 doctor 必须能说出来。

这里钉三件事：
1. 今天确实漂了哪几条——列表是写死的，将来多一条就得显式改这里
2. 声明真的驱动行为（RecipeScene 直接用 recipe_sections 拼），不是装饰
3. 检查本身会报（拿一个"说谎"的场景验证），且 doctor 真打印出来
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.config import Config  # noqa: E402
from core.validate import unconsumed_recipe_parts  # noqa: E402
from scenes.base import RecipeScene, SceneContext, Turn, load_scenes  # noqa: E402
from scenes.chat_qa.scene import ChatQa  # noqa: E402


class TestRecipeContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Config(ROOT)
        self.cfg.override(
            "config/core.yml",
            {"paths": {"store": str(Path(self.tmp.name) / "store")}},
        )
        self.scenes = load_scenes(ROOT / "scenes")

    def warnings(self, scenes=None):
        return unconsumed_recipe_parts(
            self.cfg, ROOT / "scenes", scenes if scenes is not None else self.scenes
        )

    def ctx(self):
        return SceneContext(
            cfg=self.cfg, scenes_dir=ROOT / "scenes", rawlog=None, engine=None
        )

    # ---- 1. 今天的漂移清单 ----------------------------------------
    def test_known_drift_is_exactly_these(self):
        """漂移清单是写死的：多一条、少一条都必须在这里显式同步。"""
        self.assertEqual(
            set(self.warnings()),
            {
                "chat.qa：菜谱声明了「历史」，但场景不消费这一段（不报错，只是不生效）",
                "label：菜谱声明了工具箱「自定义」，但场景从不把 tools 传给引擎"
                "（这段配置目前没用）",
                "memory.extract：菜谱声明了工具箱「自定义」，但场景从不把 tools 传给引擎"
                "（这段配置目前没用）",
            },
        )

    def test_chat_reply_and_image_are_clean(self):
        """这两个是标准样板，不该有任何漂移——否则说明默认值被改坏了。"""
        dirty = [w for w in self.warnings() if w.split("：")[0] in ("chat.reply", "chat.image")]
        self.assertEqual(dirty, [])

    # ---- 2. 声明驱动行为 ------------------------------------------
    def test_sections_declaration_drives_assemble(self):
        """RecipeScene 拼哪几段完全由声明决定，不存在"声明一套、代码另一套"。"""
        self.assertEqual(ChatQa.recipe_sections, ("块", "用户"))

        class OnlyBlocks(RecipeScene):
            name = "chat.reply"
            recipe_sections = ("块",)

        ctx = self.ctx()
        turn = Turn(turn_id="t-sec", text="在吗", person_id=2, conversation="direct:2")
        messages, used = OnlyBlocks().build_messages(ctx.recipe("chat.reply"), turn, ctx)

        self.assertEqual([m["role"] for m in messages], ["system"])
        self.assertFalse([u for u in used if u.startswith(("用户/", "历史/"))])

    # ---- 3. 检查本身会报 ------------------------------------------
    def test_scene_that_underdeclares_is_caught(self):
        """合成一个"少声明"的场景：三段里只认块，还说不传 tools。"""

        class Lazy(RecipeScene):
            name = "chat.reply"
            recipe_sections = ("块",)
            accepts_tools = False

        got = self.warnings({"chat.reply": Lazy()})
        self.assertTrue(any("「历史」" in w for w in got), got)
        self.assertTrue(any("「用户」" in w for w in got), got)
        self.assertTrue(any("工具箱「封装」" in w for w in got), got)

    def test_doctor_prints_the_warnings(self):
        """doctor 只提示、不拒绝：退出码必须是 0。"""
        proc = subprocess.run(
            [sys.executable, "cli.py", "doctor"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("chat.qa：菜谱声明了「历史」", proc.stdout)
        self.assertIn("自检通过", proc.stdout)


if __name__ == "__main__":
    unittest.main()
