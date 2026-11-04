"""工具层：注册、分箱、参数校验、异常兜底。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core import db, repo  # noqa: E402
from core.config import Config  # noqa: E402
from core.engine import ToolContext  # noqa: E402
from support.tools.base import (  # noqa: E402
    ToolResult,
    ToolSpec,
    execute,
    for_toolbox,
    load_tools,
    parse_args,
)


class TestRegistry(unittest.TestCase):
    def setUp(self):
        self.tools = load_tools(ROOT / "support" / "tools")

    def test_memory_tools_registered(self):
        for name in ("memory_search", "memory_add", "memory_close"):
            self.assertIn(name, self.tools)
            self.assertEqual(self.tools[name].toolbox, "封装")

    def test_every_tool_has_schema(self):
        for tool in self.tools.values():
            self.assertTrue(tool.description, tool.name)
            self.assertEqual(tool.parameters.get("type"), "object", tool.name)
            self.assertIsNotNone(tool.func, tool.name)

    def test_openai_shape(self):
        spec = self.tools["memory_search"].to_openai()
        self.assertEqual(spec["type"], "function")
        self.assertEqual(spec["function"]["name"], "memory_search")

    def test_for_toolbox(self):
        self.assertTrue(for_toolbox(self.tools, "封装"))
        self.assertEqual(for_toolbox(self.tools, "自定义"), [])
        self.assertEqual(
            len(for_toolbox(self.tools, "全量")), len(self.tools)
        )


class TestExecute(unittest.TestCase):
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

    def _call(self, name, **args):
        return execute(self.tools[name], args, self.ctx)

    def test_unknown_arg_rejected_with_hint(self):
        r = self._call("memory_search", query="x", wrong="y")
        self.assertFalse(r.ok)
        self.assertIn("不认识这些参数", r.content)
        self.assertIn("query", r.content)

    def test_missing_required_arg(self):
        r = self._call("memory_add")
        self.assertFalse(r.ok)
        self.assertIn("缺必填参数", r.content)

    def test_add_then_search(self):
        add = self._call("memory_add", content="浅华不吃辣")
        self.assertTrue(add.ok)
        self.assertEqual(add.meta["memory_id"], 1)
        hit = self._call("memory_search", query="吃辣")
        self.assertTrue(hit.ok)
        self.assertIn("不吃辣", hit.content)

    def test_add_is_idempotent(self):
        self._call("memory_add", content="浅华不吃辣")
        again = self._call("memory_add", content="浅华不吃辣")
        self.assertTrue(again.ok)
        self.assertTrue(again.meta.get("duplicate"))

    def test_add_rejects_empty_and_oversize(self):
        self.assertFalse(self._call("memory_add", content="   ").ok)
        self.assertFalse(self._call("memory_add", content="长" * 300).ok)

    def test_close(self):
        self._call("memory_add", content="浅华住北京")
        r = self._call("memory_close", memory_id=1, why="他搬家了")
        self.assertTrue(r.ok)
        self.assertEqual(repo.search_memory(self.cfg, "北京", exclude_recent_days=0), [])

    def test_tool_exception_becomes_message(self):
        def boom(ctx, **kw):
            raise RuntimeError("炸了")

        spec = ToolSpec(
            name="boom",
            description="x",
            parameters={"type": "object", "properties": {}},
            func=boom,
        )
        r = execute(spec, {}, self.ctx)
        self.assertFalse(r.ok)
        self.assertIn("RuntimeError", r.content)


class TestParseArgs(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(parse_args('{"a": 1}'), {"a": 1})

    def test_fenced_json(self):
        self.assertEqual(parse_args('```json\n{"a": 2}\n```'), {"a": 2})

    def test_broken_returns_empty(self):
        self.assertEqual(parse_args("not json"), {})
        self.assertEqual(parse_args(None), {})


if __name__ == "__main__":
    unittest.main()
