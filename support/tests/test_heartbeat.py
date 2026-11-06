"""M4 心跳：该不该醒、醒来说什么、说出来的东西怎么落。

四块分开测：

1. 闸（core/guards）：静默时段、间隔、每日上限——**程序说了算，不是模型**
2. 状态（core/state）：情绪 ±0.3、值夹 0..1、坏数据不许污染
3. 一轮（pipeline/heartbeat）：解析成功 / 失败各走哪条路
4. 循环（core/scheduler）：到点只醒一次
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core import clock, db, guards, state  # noqa: E402
from core.config import Config  # noqa: E402
from pipeline import heartbeat  # noqa: E402
from scenes.base import SceneContext, Turn, load_scenes  # noqa: E402


def _cfg(tmp: str) -> Config:
    cfg = Config(ROOT)
    cfg.override("config/core.yml", {"paths": {"store": str(Path(tmp) / "store")}})
    return cfg


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = _cfg(self.tmp.name)
        self.store = self.cfg.store_root()
        db.ensure_all(self.cfg)


class TestGuards(_Base):
    def test_awake_window_normal(self):
        self.assertTrue(guards.in_awake_window(["07:00", "23:00"], datetime(2026, 11, 6, 12, 0)))
        self.assertFalse(guards.in_awake_window(["07:00", "23:00"], datetime(2026, 11, 6, 3, 0)))

    def test_awake_window_crosses_midnight(self):
        window = ["07:00", "00:00"]  # 配置里就是这个：早七点到零点
        self.assertTrue(guards.in_awake_window(window, datetime(2026, 11, 6, 23, 30)))
        self.assertTrue(guards.in_awake_window(window, datetime(2026, 11, 6, 7, 0)))
        self.assertFalse(guards.in_awake_window(window, datetime(2026, 11, 6, 2, 0)))

    def test_quiet_hours_blocks_tick(self):
        ready, why = guards.should_tick(self.cfg, "xi", datetime(2026, 11, 6, 3, 0))
        self.assertFalse(ready)
        self.assertIn("静默", why)

    def test_interval_blocks_second_tick(self):
        now = datetime(2026, 11, 6, 12, 0)
        self.assertTrue(guards.should_tick(self.cfg, "xi", now)[0])
        guards.mark_tick(self.cfg, "xi", now)

        again = now + timedelta(minutes=5)
        ready, why = guards.should_tick(self.cfg, "xi", again)
        self.assertFalse(ready, "5 分钟又醒了一次")
        self.assertIn("分钟", why)

        later = now + timedelta(minutes=31)
        self.assertTrue(guards.should_tick(self.cfg, "xi", later)[0])

    def test_daily_limit_blocks_speaking(self):
        now = datetime(2026, 11, 6, 12, 0)
        # persona.yml 里 max_per_day: 0（不限），临时改成 1
        self.cfg.override(
            "config/personas/xi/persona.yml",
            {"schedule": {"awake": ["07:00", "00:00"]},
             "proactive": {"max_per_day": 1, "min_gap_minutes": 0}},
        )
        self.assertTrue(guards.can_speak(self.cfg, "xi", now)[0])
        guards.mark_spoke(self.cfg, "xi", now)
        ready, why = guards.can_speak(self.cfg, "xi", now + timedelta(minutes=10))
        self.assertFalse(ready)
        self.assertIn("已经主动说过", why)

    def test_min_gap_blocks_speaking(self):
        now = datetime(2026, 11, 6, 12, 0)
        self.cfg.override(
            "config/personas/xi/persona.yml",
            {"schedule": {"awake": ["07:00", "00:00"]},
             "proactive": {"max_per_day": 0, "min_gap_minutes": 60}},
        )
        guards.mark_spoke(self.cfg, "xi", now)
        ready, why = guards.can_speak(self.cfg, "xi", now + timedelta(minutes=20))
        self.assertFalse(ready)
        self.assertIn("分钟", why)


class TestState(_Base):
    def test_emotion_step_is_capped(self):
        got = state.apply_emotion({}, {"开心": 0.9})
        self.assertEqual(got["emotion"]["开心"], 0.3, "单次最多 +0.3")

    def test_emotion_value_is_clamped(self):
        got = state.apply_emotion({"emotion": {"开心": 0.9}}, {"开心": 0.3})
        self.assertEqual(got["emotion"]["开心"], 1.0)
        got = state.apply_emotion({"emotion": {"低落": 0.1}}, {"低落": -0.3})
        self.assertEqual(got["emotion"]["低落"], 0.0)

    def test_bad_values_are_ignored(self):
        got = state.apply_emotion({"emotion": {"平静": 0.5}}, {"平静": "很平静", "": 0.1})
        self.assertEqual(got["emotion"]["平静"], 0.5, "脏数据不该改状态")

    def test_energy_uses_the_same_rule(self):
        self.assertEqual(state.apply_energy({}, 9)["energy"], 0.3)
        self.assertEqual(state.apply_energy({"energy": 0.9}, 0.3)["energy"], 1.0)

    def test_write_then_read_roundtrip(self):
        state.write_state(self.cfg, {"emotion": {"平静": 0.5}, "energy": 0.6})
        got = state.read_state(self.cfg)
        self.assertEqual(got["emotion"]["平静"], 0.5)
        self.assertIn("updated_at", got)

    def test_inner_and_journal_are_append_only(self):
        ts = datetime(2026, 11, 6, 15, 30)
        state.append_inner(self.cfg, "第一个念头", ts=ts)
        state.append_inner(self.cfg, "第二个念头", ts=ts)
        inner = (self.store / "processed" / "7-inner" / "xi" / "2026-11-06.md").read_text()
        self.assertEqual(len(inner.splitlines()), 2)
        self.assertIn("[15:30] 第一个念头", inner)

        state.append_journal(self.cfg, "心跳：随便想想", ts=ts)
        journal = (self.store / "processed" / "8-journal" / "xi" / "2026-11-06.md").read_text()
        self.assertIn("15:30 心跳：随便想想", journal)

    def test_hidden_keys_do_not_leak_into_prompt(self):
        lines = state.render_state({"emotion": {"平静": 0.5}, "updated_at": "2026-11-06T15:30"})
        joined = "\n".join(lines)
        self.assertNotIn("updated_at", joined)
        self.assertIn("情绪：平静 0.50", joined)


class FakeTickEngine:
    """按剧本返回 tick 的 JSON。"""

    def __init__(self, payload: str, error: str | None = None):
        self.payload = payload
        self.error = error

    async def run(self, messages, *, tier, toolbox="全量", steps=10, turn=None, scene="", tools=None):
        out = {"output": self.payload, "calls": [], "tool_uses": []}
        if self.error:
            out["error"] = self.error
        return out


GOOD = json.dumps(
    {
        "inner": "下午的光有点晃眼，我盯着看了一会儿。",
        "emotion": {"平静": 0.1, "牵挂": 0.2},
        "energy": -0.1,
        "speak": False,
        "say": "",
    },
    ensure_ascii=False,
)


class TestHeartbeat(_Base):
    def setUp(self):
        super().setUp()
        self.scenes = load_scenes(ROOT / "scenes")

    def ctx(self, engine):
        return SceneContext(
            cfg=self.cfg, scenes_dir=ROOT / "scenes", rawlog=None, engine=engine
        )

    def test_writes_inner_state_and_journal(self):
        out = asyncio.run(
            heartbeat.run_once(self.cfg, self.ctx(FakeTickEngine(GOOD)), self.scenes)
        )
        self.assertTrue(out["ok"])
        self.assertFalse(out["speak"])

        inner = (self.store / "processed" / "7-inner" / "xi" / f"{clock.day_dir()}.md").read_text()
        self.assertIn("下午的光有点晃眼", inner)

        saved = state.read_state(self.cfg)
        self.assertEqual(saved["emotion"]["平静"], 0.1)
        self.assertEqual(saved["emotion"]["牵挂"], 0.2)
        self.assertEqual(saved["energy"], 0.0)  # 0 + (-0.1) 夹到 0

        journal = (self.store / "processed" / "8-journal" / "xi" / f"{clock.day_dir()}.md").read_text()
        self.assertIn("心跳：下午的光", journal)

    def test_parse_failure_only_writes_inner(self):
        engine = FakeTickEngine("我今天就是不想输出 JSON。")
        out = asyncio.run(heartbeat.run_once(self.cfg, self.ctx(engine), self.scenes))

        self.assertFalse(out["ok"])
        inner = (self.store / "processed" / "7-inner" / "xi" / f"{clock.day_dir()}.md").read_text()
        self.assertIn("我今天就是不想输出 JSON", inner)
        self.assertEqual(state.read_state(self.cfg), {}, "解析失败不该动状态")

    def test_call_failure_does_not_write_inner(self):
        """"没调通"和"想不出来"是两回事：前者不该往内心里写一句话。"""
        engine = FakeTickEngine("", error="HTTP 429：server overloaded")
        out = asyncio.run(heartbeat.run_once(self.cfg, self.ctx(engine), self.scenes))

        self.assertFalse(out["ok"])
        self.assertEqual(out["why"], "调用失败")
        inner_path = self.store / "processed" / "7-inner" / "xi" / f"{clock.day_dir()}.md"
        self.assertFalse(inner_path.exists(), "调用失败不该写内心")

        journal = (self.store / "processed" / "8-journal" / "xi" / f"{clock.day_dir()}.md").read_text()
        self.assertIn("心跳失败", journal)
        self.assertIn("429", journal)
        self.assertEqual(state.read_state(self.cfg), {}, "失败不该动状态")

    def test_speak_goes_through_the_gates(self):
        payload = json.dumps(
            {"inner": "想跟他说句话。", "speak": True, "say": "在干嘛呢"}, ensure_ascii=False
        )
        out = asyncio.run(
            heartbeat.run_once(self.cfg, self.ctx(FakeTickEngine(payload)), self.scenes)
        )
        self.assertTrue(out["speak"])
        self.assertTrue(out["allowed"])
        self.assertEqual(out["say"], "在干嘛呢")

    def test_quiet_hours_block_speaking(self):
        payload = json.dumps(
            {"inner": "半夜想说话。", "speak": True, "say": "睡了吗"}, ensure_ascii=False
        )
        night = datetime(2026, 11, 6, 3, 0)
        out = asyncio.run(
            heartbeat.run_once(
                self.cfg, self.ctx(FakeTickEngine(payload)), self.scenes, moment=night
            )
        )
        self.assertTrue(out["speak"], "她想说")
        self.assertFalse(out["allowed"], "但程序不让")
        self.assertIn("静默", out["blocked_by"])


class TestPresentQuery(_Base):
    """心跳的检索词：最近几条内心 + 最后一句聊天。

    以前是字面量「最近」——对检索几乎没信息量，等于随机捞十条。
    """

    def ctx(self):
        return SceneContext(cfg=self.cfg, scenes_dir=ROOT / "scenes", rawlog=None, engine=None)

    def test_uses_inner_and_last_human_line(self):
        from core import repo

        state.append_inner(self.cfg, "有点想他", ts=datetime(2026, 11, 6, 15, 0))
        state.append_inner(self.cfg, "窗外在修路", ts=datetime(2026, 11, 6, 15, 30))
        repo.add_interaction(
            self.cfg, turn_id="t1", person_id=1, kind="消息",
            content="今晚一起吃饭吗", conversation="direct:owner",
        )
        repo.add_interaction(
            self.cfg, turn_id="t1", person_id=0, kind="消息",
            content="好啊", conversation="direct:owner",
        )

        query = self.ctx()._present_query()
        self.assertIn("有点想他", query)
        self.assertIn("窗外在修路", query)
        self.assertIn("今晚一起吃饭吗", query, "最后一句应该取'他'说的，不是她自己")
        self.assertNotIn("好啊", query)

    def test_takes_only_the_last_three_inner(self):
        for text in ("第一", "第二", "第三", "第四", "第五"):
            state.append_inner(self.cfg, text)
        query = self.ctx()._present_query()
        self.assertNotIn("第一", query)
        self.assertIn("第五", query)

    def test_falls_back_when_nothing_is_there(self):
        self.assertEqual(self.ctx()._present_query(), "最近")


class TestSchedulerLoop(_Base):
    def test_ticks_once_then_respects_interval(self):
        """到点只醒一次：5 分钟内不该再醒。"""
        now = datetime(2026, 11, 6, 12, 0)
        self.assertTrue(guards.should_tick(self.cfg, "xi", now)[0])
        guards.mark_tick(self.cfg, "xi", now)
        self.assertFalse(guards.should_tick(self.cfg, "xi", now + timedelta(minutes=5))[0])


if __name__ == "__main__":
    unittest.main()
