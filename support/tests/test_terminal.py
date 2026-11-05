"""终端前台：本地聊天 + 一次性临时库。

两件事要钉住：

1. `/help`、`/as`、`#图` 这些命令走的是**通道**，不该变成消息发出去
2. `--tmp` 用的是一次性库，不碰正式库也不碰试验库
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import main  # noqa: E402
from core.config import Config  # noqa: E402
from support.channel.stub import StubChannel  # noqa: E402


def _drive(lines: list[str]) -> list:
    """把若干行喂给通道，收回它吐出来的所有 Incoming。"""
    channel = StubChannel(admin_id=1, nickname="浅华")
    remaining = list(lines)

    def fake_input(prompt=""):
        if not remaining:
            raise EOFError
        return remaining.pop(0)

    async def collect():
        out = []
        with mock.patch("builtins.input", fake_input):
            async for msg in channel.receive():
                out.append(msg)
        return out

    with contextlib.redirect_stdout(io.StringIO()):
        return asyncio.run(collect())


class TestTerminalChannel(unittest.TestCase):
    def test_plain_line_becomes_a_message(self):
        msgs = _drive(["在吗"])
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].text, "在吗")
        self.assertEqual(msgs[0].person_id, 1)
        self.assertTrue(msgs[0].is_owner)
        self.assertEqual(msgs[0].conversation, "direct:owner")

    def test_commands_do_not_leak_as_messages(self):
        msgs = _drive(["/help", "/?", "/帮助", "", "   ", "在吗"])
        self.assertEqual(len(msgs), 1, "命令和空行都不该变成消息")
        self.assertEqual(msgs[0].text, "在吗")

    def test_quit_stops_before_yielding(self):
        msgs = _drive(["第一句", "/quit", "不该到这里"])
        self.assertEqual([m.text for m in msgs], ["第一句"])

    def test_as_switches_the_speaker(self):
        msgs = _drive(["/as 2 张三", "我不认识你"])
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].person_id, 2)
        self.assertEqual(msgs[0].person_name, "张三")
        self.assertFalse(msgs[0].is_owner)
        # 每个人一个对话，历史不会串到主人那边
        self.assertEqual(msgs[0].conversation, "direct:2")

    def test_as_1_returns_to_owner(self):
        msgs = _drive(["/as 2 张三", "/as 1", "我回来了"])
        self.assertEqual(msgs[0].person_id, 1)
        self.assertTrue(msgs[0].is_owner)

    def test_bad_as_is_rejected(self):
        msgs = _drive(["/as abc", "在吗"])
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].text, "在吗", "写错的 /as 不该被当成消息")

    def test_as_prefixed_words_are_not_swallowed(self):
        """`/asdf` 是消息，不是命令——只有 `/as` 或 `/as ` 才算命令。"""
        msgs = _drive(["/asdf 你好", "/as"])
        self.assertEqual([m.text for m in msgs], ["/asdf 你好"])

    def test_hash_image_requires_a_space(self):
        msgs = _drive(["#图片好看"])
        self.assertEqual(msgs[0].text, "#图片好看", "#图 只在带空格时才算标记")
        self.assertEqual(msgs[0].images, [])

    def test_hash_image_marks_image(self):
        msgs = _drive(["#图 今天的晚霞"])
        self.assertEqual(msgs[0].text, "今天的晚霞")
        self.assertEqual(msgs[0].images, ["(假图)"])


class TestTempStore(unittest.TestCase):
    def _args(self, **kw):
        base = dict(tmp=False, keep=False, mock=False, daemons=False)
        base.update(kw)
        return argparse.Namespace(**base)

    def test_no_tmp_keeps_default_store(self):
        cfg = Config(ROOT)
        self.assertIsNone(main._setup_temp_store(cfg, self._args()))
        self.assertEqual(cfg.store_root(), cfg.root / Config.DEV_STORE)

    def test_tmp_points_store_at_a_fresh_dir(self):
        cfg = Config(ROOT)
        path = main._setup_temp_store(cfg, self._args(tmp=True))
        self.addCleanup(shutil.rmtree, path, True)
        try:
            self.assertTrue(path.exists())
            self.assertEqual(cfg.store_root(), path)
            # 和正式库、试验库都不是同一个地方
            self.assertNotEqual(path, ROOT / "store")
            self.assertNotEqual(path, ROOT / Config.DEV_STORE)
        finally:
            # atexit 里也会删，先手动删掉不留垃圾
            shutil.rmtree(path, ignore_errors=True)

    def test_use_store_beats_env_and_config(self):
        cfg = Config(ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XI_STORE": "store"}, clear=False):
                cfg.use_store(tmp)
                self.assertEqual(cfg.store_root(), Path(tmp).resolve())

    def test_production_guard_still_applies(self):
        """--tmp 不应该顺带把正式库的禁令打开。"""
        cfg = Config(ROOT)
        with mock.patch.dict(os.environ, {"XI_STORE": "store"}, clear=False):
            os.environ.pop("XI_CONFIRM_PROD", None)
            with self.assertRaises(Exception):
                main.guard_production_store(cfg)


if __name__ == "__main__":
    unittest.main()
