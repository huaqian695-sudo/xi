"""数据层：交互流、事实块、记忆。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core import clock, db, repo  # noqa: E402
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


class TestInteractions(_Base):
    def test_append_and_read_back_in_order(self):
        for i in range(3):
            repo.add_interaction(
                self.cfg, turn_id=f"t{i}", actor="他", kind="消息",
                content=f"第{i}句", chat_id=1, user_id=1,
            )
        rows = repo.recent_messages(self.cfg, chat_id=1)
        self.assertEqual([r["content"] for r in rows], ["第0句", "第1句", "第2句"])

    def test_sessions_are_separate(self):
        repo.add_interaction(
            self.cfg, turn_id="t1", actor="他", kind="消息", content="第一段", chat_id=1
        )
        n = repo.open_session(self.cfg, 1)
        self.assertEqual(n, 1)  # 没有 sessions 行时，第一次开的是 1？
        self.assertEqual(repo.current_session(self.cfg, 1), 1)
        repo.add_interaction(
            self.cfg, turn_id="t2", actor="他", kind="消息", content="第二段",
            chat_id=1, session_no=2,
        )
        self.assertEqual(len(repo.recent_messages(self.cfg, chat_id=1, session_no=1)), 1)
        self.assertEqual(len(repo.recent_messages(self.cfg, chat_id=1, session_no=2)), 1)

    def test_since_cursor_uses_id(self):
        first = repo.add_interaction(
            self.cfg, turn_id="t1", actor="他", kind="消息", content="a", chat_id=1
        )
        repo.add_interaction(self.cfg, turn_id="t2", actor="他", kind="消息", content="b", chat_id=1)
        rows = repo.since_id(self.cfg, first)
        self.assertEqual([r["content"] for r in rows], ["b"])


class TestFacts(_Base):
    def test_ttl_and_supersede(self):
        ids = repo.put_facts(
            self.cfg,
            question="明天天气",
            conversation_key="private:1",
            statements=[{"statement": "明天下午有阵雨", "source": "open-meteo"}],
            ttl_hours=2,
        )
        self.assertEqual(len(ids), 1)
        active = repo.active_facts(self.cfg)
        self.assertEqual([f["statement"] for f in active], ["明天下午有阵雨"])
        self.assertIsNotNone(active[0]["expired_at"])

        repo.put_facts(
            self.cfg,
            question="明天天气",
            conversation_key="private:1",
            statements=[{"statement": "明天转晴", "source": "另一家"}],
        )
        active = repo.active_facts(self.cfg)
        self.assertEqual([f["statement"] for f in active], ["明天转晴"])

    def test_expired_not_returned(self):
        repo.put_facts(
            self.cfg,
            question="昨天的天气",
            conversation_key="private:1",
            statements=[{"statement": "晴", "source": "x"}],
            ttl_hours=-1,  # 已经过期
        )
        self.assertEqual(repo.active_facts(self.cfg), [])

    def test_conflict_mark(self):
        ids = repo.put_facts(
            self.cfg,
            question="他多大",
            conversation_key="private:1",
            statements=[{"statement": "25", "source": "a"}],
        )
        repo.mark_conflict(self.cfg, ids)
        self.assertEqual(repo.active_facts(self.cfg)[0]["conflict"], 1)


class TestMemories(_Base):
    def test_add_search_and_dedup(self):
        repo.add_memory(self.cfg, content="浅华不吃辣", kind="fact", status="verified")
        self.assertTrue(repo.similar_exists(self.cfg, "浅华不吃辣"))
        self.assertFalse(repo.similar_exists(self.cfg, "浅华喜欢下雨天"))

        hits = repo.search_memory(self.cfg, "他吃辣吗")
        self.assertTrue(hits)
        self.assertIn("不吃辣", hits[0]["content"])

    def test_self_action_tagged(self):
        repo.add_memory(self.cfg, content="给他发过一份报告", kind="self_action", status="verified")
        hits = repo.search_memory(self.cfg, "报告")
        self.assertEqual(hits[0].get("tag"), "你自己做的")

    def test_unverified_tagged(self):
        repo.add_memory(self.cfg, content="他好像不喜欢榴莲", kind="fact", status="subjective")
        hits = repo.search_memory(self.cfg, "榴莲")
        self.assertEqual(hits[0].get("tag"), "非事实")

    def test_closed_not_searchable(self):
        mid = repo.add_memory(self.cfg, content="他住在北京", kind="fact", status="observed")
        repo.close_memory(self.cfg, mid, "他搬家了")
        self.assertEqual(repo.search_memory(self.cfg, "北京"), [])


if __name__ == "__main__":
    unittest.main()
