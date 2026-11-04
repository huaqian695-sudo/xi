"""发文件：长度门槛、文件名净化、只写 out/。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.config import Config  # noqa: E402
from core.engine import ToolContext  # noqa: E402
from support.tools.base import execute, load_tools  # noqa: E402

LONG = "报告正文。" * 40  # 200 字


class TestSendFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(ROOT)
        self.cfg.override(
            "config/core.yml", {"paths": {"store": str(Path(self.tmp.name) / "store")}}
        )
        self.cfg.root = Path(self.tmp.name)  # out/ 写到临时目录
        self.ctx = ToolContext(self.cfg, "xi")
        self.tools = load_tools(ROOT / "support" / "tools")

    def tearDown(self):
        self.tmp.cleanup()

    def _send(self, **kw):
        return execute(self.tools["send_file"], kw, self.ctx)

    def test_short_content_rejected(self):
        r = self._send(content="太短了")
        self.assertFalse(r.ok)
        self.assertIn("直接说出来", r.content)

    def test_long_content_written(self):
        r = self._send(content=LONG, name="天气报告")
        self.assertTrue(r.ok)
        path = Path(r.meta["path"])
        self.assertTrue(path.exists())
        self.assertEqual(path.read_text(encoding="utf-8"), LONG)
        self.assertIn("out", str(path))
        self.assertTrue(path.name.endswith("天气报告.md"))

    def test_path_in_name_is_sanitized(self):
        r = self._send(content=LONG, name="../../etc/passwd")
        self.assertTrue(r.ok)
        path = Path(r.meta["path"])
        self.assertIn("out", path.parts, "必须落在 out/ 里")
        self.assertNotIn("..", path.parts)
        self.assertTrue(path.exists())

    def test_name_with_slash_becomes_underscore(self):
        r = self._send(content=LONG, name="a/b/c")
        self.assertTrue(r.ok)
        self.assertNotIn("/", Path(r.meta["path"]).name)

    def test_default_name(self):
        r = self._send(content=LONG)
        self.assertTrue(Path(r.meta["path"]).name.endswith("report.md"))


if __name__ == "__main__":
    unittest.main()
