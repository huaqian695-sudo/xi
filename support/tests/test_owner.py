"""跨平台认主 + 记忆归属。"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core import db, repo  # noqa: E402
from core.config import Config  # noqa: E402
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


class TestOwner(_Base):
    def test_owner_on_each_platform(self):
        self.assertTrue(self.cfg.is_owner("stub", "1693395561"))
        self.assertTrue(self.cfg.is_owner("qq", "1693395561"))
        self.assertFalse(self.cfg.is_owner("wechat", "1693395561"), "没配的平台不认")

    def test_stranger_is_not_owner(self):
        self.assertFalse(self.cfg.is_owner("qq", "999999"))

    def test_owner_name(self):
        self.assertEqual(self.cfg.owner_name(), "浅华")

    def test_scene_recognizes_owner_on_stub(self):
        ctx = SceneContext(
            cfg=self.cfg, scenes_dir=ROOT / "scenes", rawlog=None, engine=None
        )
        owner_turn = Turn(turn_id="t", channel="stub", chat_type="private",
                          user_id="1693395561", text="hi")
        stranger = Turn(turn_id="t", channel="stub", chat_type="private",
                        user_id="42", text="hi")
        self.assertFalse(ctx.when("非管理员私聊", owner_turn), "主人不该触发陌生人警告")
        self.assertTrue(ctx.when("非管理员私聊", stranger))

    def test_scene_recognizes_owner_on_qq(self):
        """换到 QQ，同一个号还是主人。"""
        ctx = SceneContext(
            cfg=self.cfg, scenes_dir=ROOT / "scenes", rawlog=None, engine=None
        )
        turn = Turn(turn_id="t", channel="qq", chat_type="private",
                    user_id="1693395561", text="hi")
        self.assertFalse(ctx.when("非管理员私聊", turn))


class TestMemorySpeaker(_Base):
    def test_memory_carries_speaker(self):
        mid = repo.add_memory(
            self.cfg,
            content="浅华不吃辣",
            kind="fact",
            speaker_channel="qq",
            speaker_user_id="1693395561",
            speaker_name="浅华",
        )
        c = sqlite3.connect(db.db_path(self.cfg, "memories"))
        row = c.execute(
            "SELECT speaker_channel, speaker_user_id, speaker_name FROM memories WHERE id=?",
            (mid,),
        ).fetchone()
        c.close()
        self.assertEqual(row, ("qq", "1693395561", "浅华"))

    def test_bulk_carries_speaker(self):
        repo.add_memories_bulk(
            self.cfg,
            [
                {
                    "content": "浅华爱吃面",
                    "kind": "fact",
                    "speaker_channel": "stub",
                    "speaker_user_id": "1693395561",
                    "speaker_name": "浅华",
                }
            ],
        )
        c = sqlite3.connect(db.db_path(self.cfg, "memories"))
        row = c.execute("SELECT speaker_name FROM memories").fetchone()
        c.close()
        self.assertEqual(row[0], "浅华")


if __name__ == "__main__":
    unittest.main()
