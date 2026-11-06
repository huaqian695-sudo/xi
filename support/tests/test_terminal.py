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
from support.channel.stub import StubChannel, decode_input  # noqa: E402


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


class TestEncoding(unittest.TestCase):
    """终端不是 UTF-8 时，中文会被解成代理字符——不能整行丢掉。"""

    def _broken(self, text, encoding="gbk"):
        """模拟 input() 用 UTF-8 去解别的编码时的结果。"""
        return text.encode(encoding).decode("utf-8", "surrogateescape")

    def test_gbk_input_is_recovered(self):
        want = "我在河科大.话说河科大和河南科大不是一个学校吗"
        got, status = decode_input(self._broken(want))
        self.assertEqual(status, "repaired")
        self.assertEqual(got, want)

    def test_gb18030_rare_char_is_recovered(self):
        want = "镕"   # 只有 GB18030 有
        got, status = decode_input(want.encode("gb18030").decode("utf-8", "surrogateescape"))
        self.assertEqual(status, "repaired")
        self.assertEqual(got, want)

    def test_utf8_passes_through_untouched(self):
        want = "我在河科大"
        got, status = decode_input(want)
        self.assertEqual(status, "ok")
        self.assertEqual(got, want)

    def test_truncated_tail_keeps_the_rest(self):
        """末尾少了半个汉字，不该把整句话吞掉。"""
        full = "我是来测试你的.现在在终端和你对话"
        broken = full.encode("utf-8")[:-1].decode("utf-8", "surrogateescape")

        got, status = decode_input(broken)
        self.assertEqual(status, "partial")
        self.assertEqual(got, "我是来测试你的.现在在终端和你对")

    def test_partial_line_still_goes_through(self):
        """整行不再因为末尾半个字被丢掉。"""
        full = "等我完成了你就可以在不同的平台和我对话吧"
        broken = full.encode("utf-8")[:-2].decode("utf-8", "surrogateescape")

        msgs = _drive([broken])
        self.assertEqual(len(msgs), 1, "被吞掉了")
        self.assertTrue(msgs[0].text.startswith("等我完成了你就可以"))

    def test_literal_replacement_char_is_stripped(self):
        msgs = _drive(["你好\ufffd世界"])
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].text, "你好世界")

    def test_unrecoverable_reports_failed(self):
        got, status = decode_input("\udcff\udcfe")
        self.assertEqual(status, "failed")
        self.assertEqual(got, "")


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


class TestDaemonMode(unittest.TestCase):
    """无头模式：部署时 systemd 就是这样托管的。"""

    def test_daemon_flag_is_parsed(self):
        args = main._parse_args(["--daemon"])
        self.assertTrue(args.daemon)
        self.assertFalse(main._parse_args([]).daemon)

    def test_wait_for_stop_returns_when_signalled(self):
        """收到停止信号后要能干净返回，好让 finally 收尾。"""
        import asyncio

        event = asyncio.Event()
        event.set()
        asyncio.run(main._wait_for_stop(event))  # 立刻返回，不挂住

    def test_wait_for_stop_installs_a_signal_handler(self):
        """装上了处理函数，SIGTERM 才不会直接杀掉进程（那会丢 disconnect 记录）。"""
        import asyncio
        import signal

        async def run():
            event = asyncio.Event()
            task = asyncio.create_task(main._wait_for_stop(event))
            await asyncio.sleep(0)          # 让它在 await 前把 handler 装上
            handler = signal.getsignal(signal.SIGTERM)
            event.set()
            await task
            return handler

        handler = asyncio.run(run())        # asyncio.run 退出时会还原 handler
        self.assertIsNot(handler, signal.SIG_DFL, "SIGTERM 没被接管")

    def test_process_starts_without_crashing(self):
        """真的把进程拉起来。

        单测覆盖不到启动顺序问题——比如 `run_daemons` 在赋值前被引用，
        整个进程起不来，而所有单元测试都是绿的。
        """
        import subprocess
        import time

        proc = subprocess.Popen(
            [sys.executable, "-u", "main.py", "--daemon", "--mock", "--tmp"],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            time.sleep(4)
            alive = proc.poll() is None
            out = ""
        finally:
            proc.terminate()
            try:
                out, _ = proc.communicate(timeout=15)
            except subprocess.TimeoutExpired:  # pragma: no cover
                proc.kill()
                out, _ = proc.communicate()

        self.assertTrue(alive, f"启动就退了：\n{out}")
        self.assertNotIn("Traceback", out, out)
        self.assertIn("守护模式", out)

    def test_daemons_run_in_daemon_mode(self):
        """守护模式必须起后台循环，否则就是个空转进程。"""
        args = main._parse_args(["--daemon", "--tmp"])
        self.assertTrue(main._should_run_daemons(args, tmp_store=None))

    def test_temp_store_does_not_start_daemons_by_default(self):
        args = main._parse_args(["--tmp"])
        self.assertFalse(main._should_run_daemons(args, tmp_store=Path("/tmp/x")))

    def test_explicit_daemons_flag_wins(self):
        args = main._parse_args(["--tmp", "--daemons"])
        self.assertTrue(main._should_run_daemons(args, tmp_store=Path("/tmp/x")))
