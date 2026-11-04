"""人用整数 id；平台只是别名，记在 bindings 里。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core import db, repo  # noqa: E402
from core.config import Config  # noqa: E402
from support.channel.bindings import Bindings  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(ROOT)
        self.cfg.override(
            "config/core.yml",
            {
                "paths": {"store": str(Path(self.tmp.name) / "store")},
                "owner": {"id": 1, "name": "浅华",
                          "bindings": [{"platform": "stub", "external": "1693395561"}]},
            },
        )
        db.ensure_all(self.cfg)

    def tearDown(self):
        self.tmp.cleanup()


class TestPeople(_Base):
    def test_upsert_creates_and_updates(self):
        repo.upsert_person(self.cfg, person_id=2, name="小明")
        repo.upsert_person(self.cfg, person_id=2, name="大明")
        person = repo.get_person(self.cfg, 2)
        self.assertEqual(person["name"], "大明")
        names = {n["name"] for n in repo.person_names(self.cfg, 2)}
        self.assertEqual(names, {"小明", "大明"}, "改名不该丢历史")

    def test_owner_flag(self):
        repo.upsert_person(self.cfg, person_id=1, name="浅华", is_owner=True)
        repo.upsert_person(self.cfg, person_id=5, name="路人")
        self.assertEqual(repo.get_person(self.cfg, 1)["is_owner"], 1)
        self.assertEqual(repo.get_person(self.cfg, 5)["is_owner"], 0)


class TestBindings(_Base):
    def test_owner_binding_from_config(self):
        b = Bindings(self.cfg)
        person = b.resolve("stub", "1693395561", "浅华")
        self.assertEqual(person["person_id"], 1)
        self.assertTrue(person["is_owner"])

    def test_unbound_account_gets_new_id(self):
        b = Bindings(self.cfg)
        person = b.resolve("qq", "3474408483", "时迁")
        self.assertNotEqual(person["person_id"], 1)
        self.assertFalse(person["is_owner"])
        # 同一个外部账号再来，还是那个人
        again = b.resolve("qq", "3474408483")
        self.assertEqual(again["person_id"], person["person_id"])

    def test_same_person_two_platforms(self):
        """主人换了平台，绑到同一个核心 id。"""
        b = Bindings(self.cfg)
        b.bind("qq", "1693395561", 1)
        on_stub = b.resolve("stub", "1693395561")
        on_qq = b.resolve("qq", "1693395561")
        self.assertEqual(on_stub["person_id"], on_qq["person_id"])

    def test_bindings_listed(self):
        b = Bindings(self.cfg)
        b.bind("qq", "123", 7)
        rows = repo.person_bindings(self.cfg, 7)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["platform"], "qq")


if __name__ == "__main__":
    unittest.main()
