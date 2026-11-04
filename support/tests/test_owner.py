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


class TestMemorySpeaker(_Base):
    def test_memory_carries_speaker(self):
        mid = repo.add_memory(
            self.cfg,
            content="浅华不吃辣",
            kind="fact",
            speaker_id=1, )
        c = sqlite3.connect(db.db_path(self.cfg, "memories"))
        row = c.execute("SELECT speaker_id FROM memories WHERE id=?", (mid,)).fetchone()
        c.close()
        self.assertEqual(row, (1,))

    def test_bulk_carries_speaker(self):
        repo.add_memories_bulk(
            self.cfg,
            [
                {
                    "content": "浅华爱吃面",
                    "kind": "fact",
                    "speaker_id": 1,
                }
            ],
        )
        c = sqlite3.connect(db.db_path(self.cfg, "memories"))
        row = c.execute("SELECT speaker_id FROM memories").fetchone()
        c.close()
        self.assertEqual(row[0], 1)


if __name__ == "__main__":
    unittest.main()
