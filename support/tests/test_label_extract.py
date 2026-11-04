"""label 与提炼：解析、兜底、判重、游标。"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core import db, repo  # noqa: E402
from core.config import Config  # noqa: E402
from pipeline import extractor  # noqa: E402
from scenes.base import SceneContext, Turn, load_scenes  # noqa: E402
from support.tests.fakes import FakeEngine  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(ROOT)
        self.cfg.override(
            "config/core.yml",
            {"paths": {"store": str(Path(self.tmp.name) / "store")}},
        )
        db.ensure_all(self.cfg)
        self.scenes = load_scenes(ROOT / "scenes")

    def tearDown(self):
        self.tmp.cleanup()

    def ctx(self, engine):
        return SceneContext(
            cfg=self.cfg, scenes_dir=ROOT / "scenes", rawlog=None, engine=engine
        )


class TestLabel(_Base):
    def _run(self, engine, text="明天天气怎么样"):
        turn = Turn(turn_id="t1", chat_id=1, user_id=1, text=text)
        return turn, asyncio.run(self.scenes["label"].run(turn, self.ctx(engine)))

    def test_parses_model_json(self):
        engine = FakeEngine(label={"need_facts": "yes", "emotion_cue": "好奇", "confidence": 0.9})
        turn, result = self._run(engine)
        self.assertEqual(result.output["need_facts"], "yes")
        self.assertEqual(result.output["emotion_cue"], "好奇")

    def test_fenced_json_still_parsed(self):
        engine = FakeEngine(label_text='好的，结果如下：\n```json\n{"need_facts": "no", "emotion_cue": "轻松"}\n```')
        turn, result = self._run(engine)
        self.assertEqual(result.output["need_facts"], "no")

    def test_unparseable_falls_back_to_uncertain(self):
        engine = FakeEngine(label_text="我猜你想问天气吧")
        turn, result = self._run(engine)
        self.assertEqual(result.output["need_facts"], "uncertain")
        self.assertTrue(result.meta["fallback"])

    def test_engine_error_falls_back(self):
        engine = FakeEngine(raise_on="label")
        turn, result = self._run(engine)
        self.assertEqual(result.output["need_facts"], "uncertain")

    def test_label_saved_to_db(self):
        engine = FakeEngine(label={"need_facts": "yes", "emotion_cue": "好奇"})
        turn, _ = self._run(engine)
        import sqlite3

        c = sqlite3.connect(db.db_path(self.cfg, "labels"))
        row = c.execute("SELECT need_facts FROM labels WHERE message_uid=?", (turn.turn_id,)).fetchone()
        self.assertEqual(row[0], "yes")


class TestExtractor(_Base):
    def _seed(self, texts):
        for i, t in enumerate(texts):
            repo.add_interaction(
                self.cfg, turn_id=f"t{i}", actor="他", kind="消息", content=t, chat_id=1
            )

    def test_writes_memories_and_advances_cursor(self):
        self._seed(["我不吃辣", "我下周三去杭州出差"])
        engine = FakeEngine(
            extraction=[
                {"content": "浅华不吃辣", "kind": "fact", "status": "verified", "source": "batch"},
                {"content": "浅华下周三去杭州出差", "kind": "episode", "status": "observed"},
            ]
        )
        n = asyncio.run(extractor.run_once(self.cfg, self.ctx(engine), self.scenes))
        self.assertEqual(n, 2)
        self.assertGreater(extractor.read_cursor(self.cfg), 0)
        hits = repo.search_memory(self.cfg, "吃辣", exclude_recent_days=0)
        self.assertTrue(hits)

    def test_dedup_skips_similar(self):
        self._seed(["我不吃辣"])
        engine = FakeEngine(
            extraction=[{"content": "浅华不吃辣", "kind": "fact", "status": "verified"}]
        )
        self.assertEqual(asyncio.run(extractor.run_once(self.cfg, self.ctx(engine), self.scenes)), 1)
        # 同一批再提一次
        self._seed(["我不吃辣啊"])
        n = asyncio.run(extractor.run_once(self.cfg, self.ctx(engine), self.scenes))
        self.assertEqual(n, 0, "相似记忆没有被跳过")

    def test_empty_extraction_still_advances(self):
        self._seed(["哈哈"])
        engine = FakeEngine(extraction=[])
        n = asyncio.run(extractor.run_once(self.cfg, self.ctx(engine), self.scenes))
        self.assertEqual(n, 0)
        self.assertGreater(extractor.read_cursor(self.cfg), 0)

    def test_no_new_rows_does_nothing(self):
        engine = FakeEngine(extraction=[])
        self.assertEqual(asyncio.run(extractor.run_once(self.cfg, self.ctx(engine), self.scenes)), 0)
        self.assertEqual(extractor.read_cursor(self.cfg), 0)

    def test_bad_items_are_dropped(self):
        self._seed(["x"])
        engine = FakeEngine(
            extraction=[
                {"content": "", "kind": "fact"},
                {"content": "x" * 300, "kind": "fact"},
                {"content": "正常一条", "kind": "没见过", "status": "胡写"},
            ]
        )
        n = asyncio.run(extractor.run_once(self.cfg, self.ctx(engine), self.scenes))
        self.assertEqual(n, 1)
        hits = repo.search_memory(self.cfg, "正常", exclude_recent_days=0)
        self.assertEqual(hits[0]["kind"], "fact")  # 不认识的 kind 归成 fact
        self.assertEqual(hits[0]["status"], "observed")


class TestFactIntoReply(_Base):
    def test_facts_block_goes_into_prompt(self):
        from scenes.base import assemble

        ctx = self.ctx(FakeEngine())
        turn = Turn(
            turn_id="t",
            chat_id=1,
            user_id=1,
            text="明天天气",
            facts={"facts": [{"statement": "明天有雨", "source": "open-meteo"}]},
        )
        messages, used = assemble(ctx.recipe("chat.reply"), turn, ctx)
        self.assertIn("产品:4.事实块", used)
        self.assertIn("明天有雨", messages[0]["content"])


if __name__ == "__main__":
    unittest.main()
