"""原始层：只追加、四本账、写失败不抛。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core import clock  # noqa: E402
from core.rawlog import RawLog  # noqa: E402


class TestRawLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.raw = RawLog(self.tmp.name)
        self.raw.start()

    def tearDown(self):
        self.raw.flush()
        self.tmp.cleanup()

    def test_ensure_day_creates_four_books(self):
        self.raw.ensure_day()
        day = Path(self.tmp.name) / "raw" / clock.day_dir()
        for kind in ("turns", "runs", "tools", "events"):
            self.assertTrue((day / f"{kind}.jsonl").exists(), kind)

    def test_append_is_append_only(self):
        self.raw.append("turns", {"turn_id": "a"})
        self.raw.append("turns", {"turn_id": "b"})
        self.raw.flush()
        day = Path(self.tmp.name) / "raw" / clock.day_dir()
        lines = (day / "turns.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["turn_id"], "a")

    def test_bad_kind_does_not_raise(self):
        self.raw.append("不存在的账", {"x": 1})  # 不抛
        self.assertEqual(self.raw.dropped, 1)

    def test_tail(self):
        self.raw.append("events", {"k": 1})
        self.raw.append("events", {"k": 2})
        self.raw.flush()
        tail = self.raw.tail("events", limit=1)
        self.assertEqual(len(tail), 1)
        self.assertEqual(tail[0]["k"], 2)


if __name__ == "__main__":
    unittest.main()


class TestRawLogGuarantees(unittest.TestCase):
    """回归：队列满时不能丢 turns/runs；启动要线程安全。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_queue_never_drops_turns(self):
        raw = RawLog(self.tmp.name, maxsize=1)
        raw.ensure_day()
        for i in range(50):
            raw.append("turns", {"turn_id": f"t{i}"})
        raw.flush()
        day = Path(self.tmp.name) / "raw" / clock.day_dir()
        lines = (day / "turns.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 50, "队列满时丢了 turns")

    def test_full_queue_drops_events_first(self):
        raw = RawLog(self.tmp.name, maxsize=1)
        raw.ensure_day()
        for i in range(50):
            raw.append("events", {"i": i})
        raw.flush()
        self.assertGreater(raw.dropped, 0)

    def test_concurrent_start_is_safe(self):
        import threading

        raw = RawLog(self.tmp.name)
        errors = []

        def worker(i):
            try:
                raw.append("turns", {"turn_id": str(i)})
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        raw.flush()
        self.assertEqual(errors, [])

    def test_flush_reports_success(self):
        raw = RawLog(self.tmp.name)
        raw.append("turns", {"a": 1})
        self.assertTrue(raw.flush(timeout=2))
