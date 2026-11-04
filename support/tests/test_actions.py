"""她记不记得自己动过手。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core import db, organize, repo  # noqa: E402
from core.config import Config  # noqa: E402
from core.engine import ToolContext  # noqa: E402
from scenes.base import SceneContext, Turn  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(ROOT)
        self.cfg.override(
            "config/core.yml", {"paths": {"store": str(Path(self.tmp.name) / "store")}}
        )
        db.ensure_all(self.cfg)

    def tearDown(self):
        self.tmp.cleanup()

    def _record(self, turn_id="t1", tools=None, reply="好", text="查天气"):
        return {
            "turn_id": turn_id,
            "ts": "2026-11-04T21:00:00+08:00",
            "channel": "stub",
            "chat_type": "private",
            "chat_id": "1",
            "session_no": 1,
            "participants": [{"user_id": "1", "nickname": "浅华", "text": text}],
            "text": text,
            "reply": reply,
            "image_refs": [],
            "tools": tools or [],
        }


class TestActionRecording(_Base):
    def test_tool_calls_become_interactions(self):
        rec = self._record(
            tools=[{"name": "weather", "ok": True, "output": "洛阳 阵雨"}]
        )
        organize.from_turn(self.cfg, rec)
        rows = repo.recent_actions(self.cfg, channel="stub")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "工具")
        self.assertIn("weather", rows[0]["content"])

    def test_tools_do_not_pollute_history(self):
        rec = self._record(tools=[{"name": "weather", "ok": True, "output": "阵雨"}])
        organize.from_turn(self.cfg, rec)
        msgs = repo.recent_messages(self.cfg, chat_id="1", channel="stub")
        self.assertEqual(len(msgs), 2, "对话轮次里只该有他一句、她一句")
        self.assertNotIn("weather", str([m["content"] for m in msgs]))


class TestActionBlock(_Base):
    def _ctx(self):
        return SceneContext(
            cfg=self.cfg, scenes_dir=ROOT / "scenes", rawlog=None, engine=None
        )

    def test_block_shows_last_turn_only(self):
        organize.from_turn(
            self.cfg, self._record("t1", tools=[{"name": "weather", "ok": True, "output": "洛阳 阵雨"}])
        )
        organize.from_turn(
            self.cfg, self._record("t2", tools=[{"name": "web_search", "ok": True, "output": "搜到了"}])
        )
        ctx = self._ctx()
        turn = Turn(turn_id="t3", chat_id="1", channel="stub")
        text = ctx.fetch({"动作": "上一轮"}, turn)
        self.assertIn("web_search", text)
        self.assertNotIn("weather", text, "只拼最近一轮")

    def test_empty_when_no_actions(self):
        ctx = self._ctx()
        turn = Turn(turn_id="t3", chat_id="1", channel="stub")
        self.assertEqual(ctx.fetch({"动作": "上一轮"}, turn), "")

    def test_recipe_uses_action_block(self):
        ctx = self._ctx()
        recipe = ctx.recipe("chat.reply")
        kinds = [list(s)[0] for s in recipe["块"]]
        self.assertIn("动作", kinds)


if __name__ == "__main__":
    unittest.main()
