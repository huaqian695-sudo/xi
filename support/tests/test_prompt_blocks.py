"""prompt 块的拼接规则：工具箱、状态、标注、超时。

这几条都是 2026-11-05 改的，各对应一个具体问题：

- 工具箱说明以前手写在常量里，加工具没人改 → 现在从 `ToolSpec` 现生成
- 状态卡并进「状态」块 → 只读 `6-state/state.json`，里面有什么渲染什么
- 标注写的是**对方的**情绪 → prompt 里必须说清楚归属
- 预算取消 → 唯一的限制是超时，超时要真的生效
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.config import Config  # noqa: E402
from scenes import constants  # noqa: E402
from scenes.base import RecipeScene, SceneContext, Turn, load_scenes  # noqa: E402

FIXED_NOW = datetime(2026, 11, 4, 22, 16, 0)


class TestPromptBlocks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Path(self.tmp.name) / "store"
        self.cfg = Config(ROOT)
        self.cfg.override("config/core.yml", {"paths": {"store": str(self.store)}})
        patcher = mock.patch("core.clock.now", return_value=FIXED_NOW)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ctx = SceneContext(
            cfg=self.cfg, scenes_dir=ROOT / "scenes", rawlog=None, engine=None
        )
        self.turn = Turn(
            turn_id="t", text="在吗", person_id=2, conversation="direct:2"
        )

    # ---- 工具箱 ---------------------------------------------------
    def test_toolbox_block_is_generated_from_specs(self):
        text = self.ctx.fetch({"工具箱": "封装"}, self.turn)

        # 每个封装箱工具都要出现，且带自己的说明
        for name in ("memory_search", "memory_add", "memory_close", "weather",
                     "web_search", "web_fetch", "send_file"):
            self.assertIn(f"`{name}`", text, f"{name} 没进工具清单")
        # 老的手写版本漏了 send_file——这正是改成自动生成的原因
        self.assertIn("send_file", text)
        # 纪律仍然在，且不再是"工具清单"本身
        self.assertIn("查完要说清楚三件事", text)
        self.assertNotIn("工具箱说明", constants.CONSTANTS)

    def test_toolbox_block_follows_the_toolbox(self):
        full = self.ctx.fetch({"工具箱": "全量"}, self.turn)
        # 全量箱包含封装箱的全部，至少不会比它少
        self.assertGreaterEqual(full.count("- `"), 7)

    # ---- 状态 -----------------------------------------------------
    def test_state_block_renders_whole_state_file(self):
        path = self.store / "processed" / "6-state" / "xi" / "state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"emotion": {"平静": 0.6, "亲近": 0.4}, "energy": 0.75, "note": "刚睡醒"},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        text = self.ctx.fetch({"状态": "情绪"}, self.turn)

        self.assertIn("你自己的状态", text)
        self.assertIn("情绪：平静 0.60、亲近 0.40", text)
        # 状态卡并进来之后，state.json 里的其它字段也要渲染
        self.assertIn("精力：0.75", text, "energy 要显示成中文")
        self.assertIn("刚睡醒", text)

    def test_state_block_without_file_still_reports_uptime(self):
        """没有 state.json 也要有内容：至少她知道自己醒着多久。"""
        text = self.ctx.fetch({"状态": "情绪"}, self.turn)

        self.assertIn("你自己的状态", text)
        self.assertIn("已经醒着", text)
        self.assertNotIn("情绪：", text)

    def test_state_block_reports_how_long_she_has_been_awake(self):
        from datetime import timedelta

        self.ctx.started_at = FIXED_NOW - timedelta(hours=3, minutes=7)
        text = self.ctx.fetch({"状态": "情绪"}, self.turn)

        self.assertIn("这次已经醒着：3 小时 7 分", text)

    def test_human_span_formats(self):
        from datetime import timedelta

        from scenes.base import human_span

        self.assertEqual(human_span(timedelta(seconds=30)), "不到 1 分钟")
        self.assertEqual(human_span(timedelta(minutes=42)), "42 分钟")
        self.assertEqual(human_span(timedelta(hours=2)), "2 小时")
        self.assertEqual(human_span(timedelta(hours=2, minutes=5)), "2 小时 5 分")

    # ---- 标注：是对方的情绪 ---------------------------------------
    def test_label_block_says_it_is_theirs(self):
        self.turn.label = {"emotion_cue": "低落", "topics": ["加班"]}
        text = self.ctx.fetch({"产品": "3.标注", "取": "emotion_cue"}, self.turn)

        self.assertIn("对方的情绪：低落", text)
        self.assertIn("不是你自己的情绪", text)

    def test_label_block_topics_variant(self):
        self.turn.label = {"emotion_cue": "低落", "topics": ["加班", "下雨"]}
        text = self.ctx.fetch({"产品": "3.标注", "取": "topics"}, self.turn)

        self.assertIn("加班", text)
        self.assertIn("下雨", text)
        self.assertNotIn("低落", text)


class TestTimeout(unittest.TestCase):
    """预算取消后，超时是唯一的硬限制——必须真的生效。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Config(ROOT)
        self.cfg.override(
            "config/core.yml",
            {"paths": {"store": str(Path(self.tmp.name) / "store")}},
        )

    def _ctx(self, engine):
        return SceneContext(
            cfg=self.cfg, scenes_dir=ROOT / "scenes", rawlog=None, engine=engine
        )

    def test_slow_engine_times_out(self):
        class Slow:
            async def run(self, *a, **kw):
                await asyncio.sleep(0.5)
                return {"output": "太慢了", "calls": [], "tool_uses": []}

        class Dummy(RecipeScene):
            name = "chat.reply"

        turn = Turn(turn_id="t", text="在吗", person_id=2, conversation="direct:2")
        with self.assertRaises(asyncio.TimeoutError):
            asyncio.run(
                Dummy().call_engine(
                    {"档位": "chat.reply", "超时": 0.05}, [], turn, self._ctx(Slow())
                )
            )

    def test_steps_are_passed_from_recipe(self):
        seen = {}

        class Recorder:
            async def run(self, *a, **kw):
                seen.update(kw)
                return {"output": "ok", "calls": [], "tool_uses": []}

        class Dummy(RecipeScene):
            name = "chat.reply"

        turn = Turn(turn_id="t", text="在吗", person_id=2, conversation="direct:2")
        asyncio.run(
            Dummy().call_engine(
                {"档位": "chat.reply", "超时": 5, "工具步": 9}, [], turn, self._ctx(Recorder())
            )
        )
        self.assertEqual(seen["steps"], 9)


if __name__ == "__main__":
    unittest.main()
