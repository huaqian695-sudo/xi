"""交互流的身份：只认 person_id，不认文字标签。

规则（2026-11-05 定）：

- `0` = 汐（她本人），`1` = 主人，`2+` = 别人
- 表里**没有** `actor` 列，"他说/她说"靠号判断，不靠字符串
- 名字统一从 `people` 表现查，代码里不写死"浅华"

以前 `actor='她'` 那几行的 `person_id` 存的是**对方**的号——身份是错的。
这里把它钉住。
"""

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
from core.db import SELF_ID  # noqa: E402
from core.rawlog import RawLog  # noqa: E402
from pipeline.extractor import format_batch  # noqa: E402
from scenes.base import SceneContext, Turn, load_scenes  # noqa: E402
from support.channel.base import Incoming  # noqa: E402
from support.channel.bindings import Bindings  # noqa: E402
from support.tests.fakes import FakeEngine  # noqa: E402


class TestIdentity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Config(ROOT)
        self.cfg.override(
            "config/core.yml",
            {"paths": {"store": str(Path(self.tmp.name) / "store")}},
        )
        db.ensure_all(self.cfg)
        Bindings(self.cfg)  # 落 0 号（汐）和主人
        self.raw = RawLog(self.cfg.store_root())
        self.raw.ensure_day()
        self.scenes = load_scenes(ROOT / "scenes")
        self.ctx = SceneContext(
            cfg=self.cfg,
            scenes_dir=ROOT / "scenes",
            rawlog=self.raw,
            engine=FakeEngine(reply="嗯，我在。"),
        )

    def tearDown(self):
        self.raw.flush()

    def _run_one(self, text="你在吗", person_id=1, name="浅华", owner=True):
        import main

        msg = Incoming(
            person_id=person_id,
            person_name=name,
            is_owner=owner,
            conversation=f"direct:{person_id}",
            text=text,
        )
        asyncio.run(main.handle(msg, self.scenes, self.ctx, self.raw))
        self.raw.flush()
        return repo.since_id(self.cfg, 0)

    # ---- 规则本身 -------------------------------------------------
    def test_self_is_zero_and_owner_is_one(self):
        rows = self._run_one()
        by_her = [r for r in rows if r["content"] == "嗯，我在。"]
        by_him = [r for r in rows if r["content"] == "你在吗"]

        self.assertTrue(by_her, "她没有落一条交互")
        self.assertTrue(by_him, "他没有落一条交互")
        for row in by_her:
            self.assertEqual(row["person_id"], SELF_ID, "她说的话必须记 0 号")
        for row in by_him:
            self.assertEqual(row["person_id"], 1, "主人说的话必须记 1 号")

    def test_schema_has_no_actor_column(self):
        conn = db.connect(db.db_path(self.cfg, "interactions"))
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(interactions)")}
        finally:
            conn.close()
        self.assertNotIn("actor", cols)
        self.assertIn("person_id", cols)

    def test_self_has_a_people_row(self):
        person = repo.get_person(self.cfg, SELF_ID)
        self.assertIsNotNone(person)
        self.assertEqual(person["name"], "汐")
        self.assertEqual(int(person["is_owner"]), 0)

    # ---- 下游怎么用它 ---------------------------------------------
    def test_history_roles_come_from_person_id(self):
        self._run_one()
        turn = Turn(
            turn_id="t-next",
            text="下一句",
            person_id=1,
            conversation="direct:1",
            session_no=1,
        )
        history = self.ctx.fetch_history({"产品": "2.交互流", "上限": 50}, turn)

        self.assertTrue(history)
        roles = {h["role"] for h in history}
        self.assertEqual(roles, {"user", "assistant"})
        # 她的那句是 assistant，他那句是 user
        self.assertIn("嗯，我在。", [h["content"] for h in history if h["role"] == "assistant"])

    def test_extract_batch_names_speakers_by_id(self):
        rows = self._run_one()
        text = format_batch(rows, self.cfg)

        self.assertIn("汐: 嗯，我在。", text)
        self.assertIn("浅华: 你在吗", text)


if __name__ == "__main__":
    unittest.main()
