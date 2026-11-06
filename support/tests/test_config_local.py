"""配置的本地覆盖层：真号不进仓库。

规则（2026-11-06）：

    config/core.yml           进 git，只有占位符
    config/core.local.yml     不进 git，放真号
    config/personas/xi/persona.yml / persona.local.yml  同理

覆盖是深合并：字典逐层合，**列表整个替换**（绑定列表就该整条换，不该拼接）。
"""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.config import Config  # noqa: E402


class TestLocalOverlay(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()

    def _write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_local_file_overrides_only_what_it_mentions(self):
        self._write(
            "config/core.yml",
            "owner:\n  id: 1\n  bindings:\n    - {platform: stub, external: '10000'}\n"
            "paths:\n  store: store/_dev\n",
        )
        self._write(
            "config/core.local.yml",
            "owner:\n  bindings:\n    - {platform: stub, external: '999'}\n",
        )
        cfg = Config(self.root)

        self.assertEqual(cfg.core["owner"]["bindings"][0]["external"], "999", "列表要整个替换")
        self.assertEqual(cfg.core["owner"]["id"], 1, "没提到的键要保留")
        self.assertEqual(cfg.core["paths"]["store"], "store/_dev")

    def test_nested_dicts_merge(self):
        self._write(
            "config/personas/xi/persona.yml",
            "owner:\n  name: 浅华\n  accounts:\n    stub: '10000'\n    qq: '10000'\n",
        )
        self._write(
            "config/personas/xi/persona.local.yml",
            "owner:\n  accounts:\n    qq: '123456789'\n",
        )
        cfg = Config(self.root)

        accounts = cfg.persona("xi")["owner"]["accounts"]
        self.assertEqual(accounts["qq"], "123456789")
        self.assertEqual(accounts["stub"], "10000", "没覆盖的账号要保留")
        self.assertEqual(cfg.persona("xi")["owner"]["name"], "浅华")

    def test_local_file_alone_is_enough(self):
        self._write("config/core.local.yml", "owner:\n  id: 7\n")
        self.assertEqual(Config(self.root).core["owner"]["id"], 7)

    def test_no_files_at_all_is_empty_not_crash(self):
        self.assertEqual(Config(self.root).core, {})

    def test_override_still_wins_over_local(self):
        """测试用的 cfg.override() 优先级最高，本地文件不该盖过它。"""
        self._write("config/core.yml", "owner:\n  id: 1\n")
        self._write("config/core.local.yml", "owner:\n  id: 9\n")
        cfg = Config(self.root)
        cfg.override("config/core.yml", {"owner": {"id": 42}})
        self.assertEqual(cfg.core["owner"]["id"], 42)


class TestNoSecretsInRepo(unittest.TestCase):
    """真号不许出现在进 git 的配置里。"""

    TRACKED = ("config/core.yml", "config/personas/xi/persona.yml")

    def test_placeholders_only(self):
        for rel in self.TRACKED:
            text = (ROOT / rel).read_text(encoding="utf-8")
            for hit in re.finditer(r'"(\d{5,})"', text):
                self.assertEqual(
                    hit.group(1), "10000",
                    f"{rel} 里出现了像真号的数字 {hit.group(1)}——真值请写进 *.local.yml",
                )

    def test_local_overlays_are_gitignored(self):
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("*.local.yml", text)


if __name__ == "__main__":
    unittest.main()
