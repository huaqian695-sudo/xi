"""Stage 0 基线：钉住「菜谱 → messages」的成品形状。

只锁四样东西，不存大 golden 文件（改一个字就红、又没人愿意看的那种测试没有价值）：

1. 消息结构：role 序列（历史有没有展开成轮次）
2. 块顺序：这一轮拼了哪些块、按什么顺序（`meta.blocks`）
3. 关键文本：人格、时间场合、当前消息、事实块
4. chat.qa 的 facts 传递：模型输出 → turn.facts → 出话场景的事实块

时间固定、store 指向临时目录，所以结果与运行时刻、本机残留数据无关。
这个文件在 Stage 1 改 Engine / 抽基类前后都必须逐字通过——它就是为了证明"行为没动"。
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.config import Config  # noqa: E402
from scenes.base import SceneContext, Turn, load_scenes  # noqa: E402

FIXED_NOW = datetime(2026, 11, 4, 22, 16, 0)

# 改造前实测的块顺序（探针抓的真值，不是手抄的）
REPLY_BLOCKS = [
    "人格:self.md",
    "人格:chat.md",
    "人格:thinking.md",
    "此刻:时间场合",
    "常量:陌生人警告",
    "动作:上一轮",
    "状态:情绪",
    "产品:7.内心",
    "产品:9.状态卡",
    "产品:5.记忆库",
    "常量:工具箱说明",
    "常量:QQ操作清单",
    "常量:标记与表情",
    "常量:输出规则",
    "历史/产品:2.交互流",
    "用户/当前:消息",
    "用户/产品:3.标注",
]
IMAGE_BLOCKS = [
    "人格:self.md",
    "人格:chat.md",
    "人格:thinking.md",
    "此刻:时间场合",
    "常量:陌生人警告",
    "常量:图片指令",
    "动作:上一轮",
    "状态:情绪",
    "产品:7.内心",
    "产品:9.状态卡",
    "产品:5.记忆库",
    "常量:工具箱说明",
    "常量:QQ操作清单",
    "常量:标记与表情",
    "常量:输出规则",
    "历史/产品:2.交互流",
    "用户/当前:消息",
    "用户/当前:图片",
    "用户/产品:3.标注",
]

FACT_LINE = "- 明天 09 点有小雨（来源：open-meteo）"
FACTS_JSON = (
    '{"facts": [{"statement": "明天 09 点有小雨", "source": "open-meteo",'
    ' "confidence": 0.8}], "not_found": []}'
)


class RecordingEngine:
    """记录发出去的东西，不联网、不花钱。"""

    def __init__(self, output: str = "嗯，我在。"):
        self.output = output
        self.seen: list[dict] = []

    async def run(
        self, messages, *, tier, toolbox="封装", budget=None, turn=None, scene="", tools=None
    ):
        self.seen.append(
            {
                "scene": scene,
                "messages": messages,
                "tier": tier,
                "toolbox": toolbox,
                "budget": budget or {},
                "tools": tools or [],
            }
        )
        return {"output": self.output, "calls": [], "tool_uses": []}


class TestRenderSnapshot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Config(ROOT)
        # store 指到临时目录：历史/记忆/状态都是空库，渲染走兜底分支，可复现
        self.cfg.override(
            "config/core.yml",
            {"paths": {"store": str(Path(self.tmp.name) / "store")}},
        )
        self.scenes = load_scenes(ROOT / "scenes")
        patcher = mock.patch("core.clock.now", return_value=FIXED_NOW)
        patcher.start()
        self.addCleanup(patcher.stop)

    # ---- 小工具 ---------------------------------------------------
    def make_ctx(self, engine) -> SceneContext:
        return SceneContext(
            cfg=self.cfg, scenes_dir=ROOT / "scenes", rawlog=None, engine=engine
        )

    def turn(self, **kw) -> Turn:
        base = dict(
            turn_id="t-snap",
            text="在吗",
            person_id=2,
            conversation="direct:2",
            session_no=1,
        )
        base.update(kw)
        return Turn(**base)

    def run_scene(self, name, turn, engine):
        ctx = self.make_ctx(engine)
        result = asyncio.run(self.scenes[name].run(turn, ctx))
        return ctx, result, engine.seen[-1]

    # ---- 1. 消息结构 ----------------------------------------------
    def test_reply_message_shape(self):
        engine = RecordingEngine()
        _, result, sent = self.run_scene("chat.reply", self.turn(), engine)

        self.assertEqual([m["role"] for m in sent["messages"]], ["system", "user"])
        self.assertEqual(sent["tier"], "chat.reply")
        self.assertEqual(sent["toolbox"], "封装")
        self.assertEqual(sent["budget"].get("工具步"), 6)
        self.assertTrue(sent["tools"], "封装箱不该是空的")

        system = sent["messages"][0]["content"]
        user = sent["messages"][-1]["content"]
        self.assertTrue(system.startswith("# 汐"), "system 必须以人格开头")
        self.assertEqual(user, "在吗")

    # ---- 2. 块顺序 ------------------------------------------------
    def test_reply_block_order(self):
        _, result, _ = self.run_scene("chat.reply", self.turn(), RecordingEngine())
        self.assertEqual(result.meta["blocks"], REPLY_BLOCKS)
        self.assertNotIn("产品:4.事实块", result.meta["blocks"], "没结论时不该拼事实块")

    def test_image_block_order(self):
        engine = RecordingEngine()
        _, result, sent = self.run_scene("chat.image", self.turn(), engine)
        self.assertEqual(sent["tier"], "chat.image")
        self.assertEqual(result.meta["blocks"], IMAGE_BLOCKS)
        self.assertIn("常量:图片指令", result.meta["blocks"])
        # 没图时「当前:图片」渲染成空，不该在 user 里留空行
        self.assertEqual(sent["messages"][-1]["content"], "在吗")

    def test_image_block_carries_paths(self):
        engine = RecordingEngine()
        turn = self.turn(images=["/tmp/a.jpg", "/tmp/b.jpg"])
        _, _, sent = self.run_scene("chat.image", turn, engine)
        user = sent["messages"][-1]["content"]
        self.assertIn("（对方发了图：", user)
        self.assertIn("- /tmp/a.jpg", user)
        self.assertIn("- /tmp/b.jpg", user)

    # ---- 3. 关键文本 ----------------------------------------------
    def test_time_and_place_text(self):
        engine = RecordingEngine()
        _, _, sent = self.run_scene("chat.reply", self.turn(), engine)
        system = sent["messages"][0]["content"]
        self.assertIn("时间：11-04 22:16 周三（晚上）", system)
        self.assertIn("场合：私聊，对面是2号", system)

    def test_owner_is_named_as_owner(self):
        engine = RecordingEngine()
        turn = self.turn(person_id=1, is_owner=True, conversation="direct:owner")
        _, result, sent = self.run_scene("chat.reply", turn, engine)
        system = sent["messages"][0]["content"]
        self.assertIn("（你的主人）", system)
        # 主人是私聊，陌生人警告不该出现
        self.assertNotIn("常量:陌生人警告", result.meta["blocks"])

    # ---- 4. facts 传递 --------------------------------------------
    def test_qa_turns_model_output_into_facts(self):
        engine = RecordingEngine(output=FACTS_JSON)
        turn = self.turn(text="明天几点下雨")
        _, result, sent = self.run_scene("chat.qa", turn, engine)

        self.assertEqual([m["role"] for m in sent["messages"]], ["system", "user"])
        self.assertEqual(sent["messages"][-1]["content"], "明天几点下雨")
        self.assertEqual(result.output["question"], "明天几点下雨")
        self.assertEqual(
            [f["statement"] for f in result.output["facts"]], ["明天 09 点有小雨"]
        )
        self.assertEqual(result.output["facts"][0]["source"], "open-meteo")
        self.assertEqual(turn.facts["facts"][0]["statement"], "明天 09 点有小雨")

    def test_qa_facts_reach_reply_prompt(self):
        _, qa_result, _ = self.run_scene(
            "chat.qa", self.turn(text="明天几点下雨"), RecordingEngine(FACTS_JSON)
        )

        engine = RecordingEngine()
        turn = self.turn(turn_id="t-reply", text="明天几点下雨")
        turn.facts = qa_result.output
        _, result, sent = self.run_scene("chat.reply", turn, engine)

        system = sent["messages"][0]["content"]
        self.assertIn(FACT_LINE, system)
        self.assertIn("常量:事实纪律", result.meta["blocks"])
        self.assertIn("产品:4.事实块", result.meta["blocks"])


if __name__ == "__main__":
    unittest.main()
