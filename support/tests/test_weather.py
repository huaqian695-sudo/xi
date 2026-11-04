"""天气：预取、读库优先、现查兜底。不联网。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core import clock, db, repo  # noqa: E402
from core.config import Config  # noqa: E402
from core.engine import ToolContext  # noqa: E402
from pipeline import weather as box  # noqa: E402
from support.tools.base import execute, load_tools  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(ROOT)
        self.cfg.override(
            "config/core.yml", {"paths": {"store": str(Path(self.tmp.name) / "store")}}
        )
        db.ensure_all(self.cfg)
        self.ctx = ToolContext(self.cfg, "xi")
        self.tools = load_tools(ROOT / "support" / "tools")

    def tearDown(self):
        self.tmp.cleanup()


class TestStatement(unittest.TestCase):
    def test_format(self):
        s = box.statement(
            {"city": "北京", "date": "2026-11-05", "weather": "晴", "high": 16, "low": 5, "rain_prob": 0}
        )
        self.assertEqual(s, "北京 2026-11-05 晴，5~16℃，降水概率 0%")

    def test_missing_numbers_ok(self):
        s = box.statement({"city": "北京", "date": "2026-11-05", "weather": "晴"})
        self.assertIn("晴", s)
        self.assertNotIn("None", s)


class TestTargetDate(unittest.TestCase):
    def test_offsets(self):
        from support.tools.weather import _target_date

        today = clock.now().strftime("%Y-%m-%d")
        tomorrow = (clock.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        self.assertEqual(_target_date("今天"), today)
        self.assertEqual(_target_date("明天"), tomorrow)

    def test_unknown_defaults_to_today(self):
        from support.tools.weather import _target_date

        self.assertEqual(_target_date("下周"), clock.now().strftime("%Y-%m-%d"))


class TestReadFromDb(_Base):
    def _seed(self, city="北京", date=None, statement="北京 晴，5~16℃"):
        date = date or clock.now().strftime("%Y-%m-%d")
        repo.put_facts(
            self.cfg,
            question=f"天气:{city}:{date}",
            conversation_key=f"weather:{city}",
            statements=[{"statement": statement, "source": "open-meteo"}],
            ttl_hours=24,
        )
        return date

    def test_hit_returns_db_source(self):
        self._seed()
        r = execute(self.tools["weather"], {"city": "北京", "day": "今天"}, self.ctx)
        self.assertTrue(r.ok)
        self.assertEqual(r.meta["source"], "db")
        self.assertIn("预取", r.content)

    def test_miss_goes_online(self):
        """库里没有这个城市 → 会去现查（这里只验证它没返回 db 来源）。"""
        from unittest import mock

        fake = [
            {
                "city": "上海",
                "date": clock.now().strftime("%Y-%m-%d"),
                "weather": "阵雨",
                "high": 20,
                "low": 16,
                "rain_prob": 60,
            }
        ]
        with mock.patch.object(box, "fetch_forecast", return_value=fake):
            r = execute(self.tools["weather"], {"city": "上海", "day": "今天"}, self.ctx)
        self.assertTrue(r.ok)
        self.assertEqual(r.meta["source"], "online")
        self.assertIn("刚查的", r.content)
        # 查完顺手存库
        self.assertTrue(repo.active_facts(self.cfg))

    def test_unknown_city_message(self):
        from unittest import mock

        with mock.patch.object(box, "fetch_forecast", return_value=[]):
            r = execute(self.tools["weather"], {"city": "不存在的城市"}, self.ctx)
        self.assertFalse(r.ok)
        self.assertIn("查不到", r.content)


class TestMemoryTimestamp(_Base):
    def test_memory_block_shows_date(self):
        from scenes.base import SceneContext, Turn

        mid = repo.add_memory(self.cfg, content="浅华不吃辣", kind="fact")
        import sqlite3

        c = sqlite3.connect(db.db_path(self.cfg, "memories"))
        c.execute("UPDATE memories SET created_at='2026-10-01T10:00:00+08:00' WHERE id=?", (mid,))
        c.commit()
        c.close()

        ctx = SceneContext(
            cfg=self.cfg, scenes_dir=ROOT / "scenes", rawlog=None, engine=None
        )
        turn = Turn(turn_id="t", person_id=1, is_owner=True, conversation="direct:owner", text="吃辣")
        text = ctx.fetch({"产品": "5.记忆库", "查询": "吃辣"}, turn)
        self.assertIn("[2026-10-01]", text, "记忆块应该带日期")


if __name__ == "__main__":
    unittest.main()
