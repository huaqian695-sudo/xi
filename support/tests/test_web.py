"""上网工具：切词、相关性、解析、地址校验。都不联网。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from support.tools.web import _bing, _h3, _relevant, _tokens  # noqa: E402

BING_HTML = """
<ul>
<li class="b_algo"><h2><a href="https://example.com/a">北京天气预报_一周</a></h2>
<p>北京今天多云转晴，明天有雨。</p></li>
<li class="b_algo"><h2><a href="https://example.com/b">周（汉语汉字）_百科</a></h2>
<p>周是时间单位。</p></li>
</ul>
"""

H3_HTML = """
<div><h3><a href="https://example.com/c">上海天气_15天</a></h3>
<div class="c-abstract">上海明天小雨。</div></div>
"""


class TestTokens(unittest.TestCase):
    def test_chinese_is_split(self):
        words = _tokens("明天北京天气")
        self.assertIn("北京", words)
        self.assertIn("天气", words)
        self.assertNotIn("明天北京天气", words, "整串不该当一个词")

    def test_english_kept(self):
        self.assertIn("deepseek", _tokens("DeepSeek 新模型"))

    def test_single_char_still_searches(self):
        self.assertEqual(_tokens("周"), ["周"])


class TestRelevance(unittest.TestCase):
    def test_related_passes(self):
        self.assertTrue(_relevant("明天北京天气", "北京天气预报", "明天有雨"))

    def test_unrelated_rejected(self):
        self.assertFalse(_relevant("明天北京天气", "周（汉语汉字）_百科", "周是时间单位"))

    def test_empty_query(self):
        self.assertFalse(_relevant("", "任意标题", ""))


class TestParse(unittest.TestCase):
    def test_bing_items(self):
        items = _bing(BING_HTML, 10)
        self.assertEqual(len(items), 2)
        self.assertTrue(items[0][0].startswith("北京天气"))
        self.assertEqual(items[0][1], "https://example.com/a")

    def test_bing_respects_limit(self):
        self.assertEqual(len(_bing(BING_HTML, 1)), 1)

    def test_h3_items(self):
        items = _h3(H3_HTML, 10)
        self.assertEqual(len(items), 1)
        self.assertIn("上海", items[0][0])
        self.assertIn("小雨", items[0][2])


class TestFetchGuard(unittest.TestCase):
    def test_rejects_non_http(self):
        from core.config import Config
        from core.engine import ToolContext
        from support.tools.base import execute, load_tools

        tools = load_tools(ROOT / "support" / "tools")
        ctx = ToolContext(Config(ROOT))
        for bad in ("file:///etc/passwd", "ftp://x", "不是地址"):
            r = execute(tools["web_fetch"], {"url": bad}, ctx)
            self.assertFalse(r.ok, bad)
            self.assertIn("http", r.content)


if __name__ == "__main__":
    unittest.main()
