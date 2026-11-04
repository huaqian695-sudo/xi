"""身份：人认 channel+user_id，名字只是别名。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core import db, repo  # noqa: E402
from core.config import Config  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(ROOT)
        self.cfg.override(
            "config/core.yml",
            {"paths": {"store": str(Path(self.tmp.name) / "store")}},
        )
        db.ensure_all(self.cfg)

    def tearDown(self):
        self.tmp.cleanup()


class TestPeople(_Base):
    def test_same_person_across_sessions_is_one_row(self):
        """私聊和群里说话的是同一个人 → 同一条档案。"""
        pid1 = repo.touch_person(self.cfg, channel="qq", user_id=1693395561, name="浅华")
        pid2 = repo.touch_person(self.cfg, channel="qq", user_id=1693395561, name="浅华")
        self.assertEqual(pid1, pid2)

    def test_different_channel_is_different_person(self):
        """同一个号在 QQ 和微信里是两个人——id 空间不通用。"""
        a = repo.touch_person(self.cfg, channel="qq", user_id="12345")
        b = repo.touch_person(self.cfg, channel="wechat", user_id="12345")
        self.assertNotEqual(a, b)

    def test_name_history_is_kept(self):
        """改名不换人：名字进别名表，身份还是那个 user_id。"""
        pid = repo.touch_person(self.cfg, channel="qq", user_id="42", name="小明")
        repo.touch_person(self.cfg, channel="qq", user_id="42", name="大明")
        names = {n["name"] for n in repo.person_names(self.cfg, pid)}
        self.assertEqual(names, {"小明", "大明"})
        # 还是同一个人
        again = repo.touch_person(self.cfg, channel="qq", user_id="42")
        self.assertEqual(again, pid)

    def test_card_and_nickname_both_kept(self):
        pid = repo.touch_person(
            self.cfg, channel="qq", user_id="42", name="小明", name_kind="nickname"
        )
        repo.touch_person(
            self.cfg, channel="qq", user_id="42", name="老王", name_kind="card"
        )
        kinds = {n["kind"] for n in repo.person_names(self.cfg, pid)}
        self.assertEqual(kinds, {"nickname", "card"})

    def test_owner_flag(self):
        repo.touch_person(self.cfg, channel="qq", user_id="1", is_owner=True)
        repo.touch_person(self.cfg, channel="qq", user_id="2", is_owner=False)
        self.assertEqual(repo.get_person(self.cfg, channel="qq", user_id="1")["is_owner"], 1)
        self.assertEqual(repo.get_person(self.cfg, channel="qq", user_id="2")["is_owner"], 0)


class TestChannelIsolation(_Base):
    def test_same_chat_id_in_two_channels_does_not_mix(self):
        """QQ 群号 999 和微信 id 999 是两件事，历史不能串。"""
        repo.add_interaction(
            self.cfg, turn_id="t1", actor="他", kind="消息", content="QQ 这边",
            channel="qq", chat_type="group", chat_id="999",
        )
        repo.add_interaction(
            self.cfg, turn_id="t2", actor="他", kind="消息", content="微信那边",
            channel="wechat", chat_type="group", chat_id="999",
        )
        qq_rows = repo.recent_messages(self.cfg, chat_id="999", channel="qq")
        wx_rows = repo.recent_messages(self.cfg, chat_id="999", channel="wechat")
        self.assertEqual([r["content"] for r in qq_rows], ["QQ 这边"])
        self.assertEqual([r["content"] for r in wx_rows], ["微信那边"])

    def test_sessions_are_per_channel(self):
        repo.open_session(self.cfg, "999", channel="qq")
        self.assertEqual(repo.current_session(self.cfg, "999", channel="qq"), 1)
        # 另一个平台还没开过
        self.assertEqual(repo.current_session(self.cfg, "999", channel="wechat"), 1)


if __name__ == "__main__":
    unittest.main()
