"""超时预算：单次调用必须比整个场景先失败。

为什么要有这条：2026-11-05 实测，`chat.reply` 卡了 90 秒被场景超时砍掉，
而那次 HTTP 请求本来给了 240 秒——结果**场景先死，失败记录一条都没留下**。

规矩：
    单次 HTTP 超时 × 重试次数 + 重试间隔  <  整场景超时

这样卡住的调用会先自己失败，留下一条 `ok=false` 的 runs（带错误原因），
场景还有余量回兜底话术。

`label` 不在此列：它有自己的 4 秒软截止 + 规则兜底，不靠这个预算。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.llm import MAX_ATTEMPTS  # noqa: E402

# 重试间隔：第 0 次失败后睡 1 秒，第 1 次后睡 2 秒…（见 llm._post）
SLEEP_AFTER_FIRST = 1.0

EXEMPT = {"label"}


def _worst_case(http_timeout: float) -> float:
    return http_timeout * MAX_ATTEMPTS + SLEEP_AFTER_FIRST * (MAX_ATTEMPTS - 1)


class TestTimeoutBudget(unittest.TestCase):
    def setUp(self):
        self.tiers = yaml.safe_load(
            (ROOT / "config" / "tiers.yml").read_text(encoding="utf-8")
        )["tiers"]

    def _recipes(self):
        for path in sorted((ROOT / "scenes").glob("*/recipe.yml")):
            recipe = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            yield path.parent.name, recipe

    def test_single_call_fails_before_scene_timeout(self):
        checked = []
        for folder, recipe in self._recipes():
            name = recipe.get("场景") or folder
            scene_timeout = recipe.get("超时")
            if scene_timeout is None or name in EXEMPT:
                continue

            tier_name = recipe.get("档位")
            self.assertIn(tier_name, self.tiers, f"{name} 的档位不存在：{tier_name}")
            http_timeout = float(self.tiers[tier_name]["timeout"])

            worst = _worst_case(http_timeout)
            self.assertLess(
                worst,
                float(scene_timeout),
                f"{name}：单次超时 {http_timeout}s × {MAX_ATTEMPTS} 次 + 间隔 = "
                f"{worst}s，不比场景超时 {scene_timeout}s 小——"
                f"卡住时场景会先被砍掉，失败记录就丢了",
            )
            checked.append(name)

        self.assertTrue(checked, "一个场景都没检查到，测试本身可能坏了")

    def test_every_tier_used_by_a_recipe_exists(self):
        for folder, recipe in self._recipes():
            tier = recipe.get("档位")
            self.assertIn(tier, self.tiers, f"{folder} 引用了不存在的档位：{tier}")


if __name__ == "__main__":
    unittest.main()
