"""原始层写入器：四本账，只追加，永不抛错。

它是唯一写 store/raw/ 的地方。写账绝不能让回复卡住——
所以走一条后台线程 + 有界队列：主流程只管入队，落盘是它的事。

队列满了丢 events、保 turns（事件可以少，账不能丢）。
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Any

from . import clock

KINDS = ("turns", "runs", "tools", "events")

# 队列满时的取舍：先丢这些
DROP_FIRST = ("events", "tools")

log = logging.getLogger(__name__)


class RawLog:
    def __init__(self, store_root: Path | str, maxsize: int = 10000):
        self.dir = Path(store_root) / "raw"
        self._q: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue(maxsize=maxsize)
        self._lock = threading.Lock()
        self._dropped = 0
        self._write_errors = 0
        self._worker = threading.Thread(target=self._run, name="rawlog", daemon=True)
        self._started = False
        self._start_lock = threading.Lock()

    # ---- 对外 -----------------------------------------------------

    @property
    def dropped(self) -> int:
        return self._dropped

    @property
    def write_errors(self) -> int:
        return self._write_errors

    def start(self) -> None:
        if self._started:
            return
        with self._start_lock:
            if self._started:
                return
            self._worker.start()
            self._started = True

    def append(self, kind: str, record: dict[str, Any]) -> None:
        """入队。永不抛，永不阻塞。"""
        if kind not in KINDS:
            self._dropped += 1
            return
        if not self._started:
            self.start()
        try:
            self._q.put_nowait((kind, record))
        except queue.Full:
            if kind in DROP_FIRST:
                # events/tools 可以丢
                self._dropped += 1
                return
            # turns/runs 绝不能丢：绕开队列，在调用线程里直接写
            self._write(kind, record)

    def flush(self, timeout: float = 5.0) -> bool:
        """等队列真的写完（不只是被取走）。超时返回 False。"""
        deadline = time.time() + timeout
        while self._q.unfinished_tasks and time.time() < deadline:
            time.sleep(0.005)
        ok = self._q.unfinished_tasks == 0
        if not ok:
            log.warning("flush 超时，还有 %d 条没写完", self._q.unfinished_tasks)
        return ok

    def ensure_day(self) -> None:
        """把今天的四个账本先开出来（空着也算开了账）。"""
        try:
            day_dir = self.dir / clock.day_dir()
            day_dir.mkdir(parents=True, exist_ok=True)
            for kind in KINDS:
                (day_dir / f"{kind}.jsonl").touch(exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            self._write_errors += 1
            log.warning("开账本失败：%s", exc)

    def tail(self, kind: str, limit: int = 50) -> list[dict[str, Any]]:
        self.flush()
        path = self.dir / clock.day_dir() / f"{kind}.jsonl"
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    # ---- 后台线程 -------------------------------------------------

    def _run(self) -> None:
        while True:
            item = self._q.get()
            try:
                if item is None:
                    return
                kind, record = item
                self._write(kind, record)
            finally:
                self._q.task_done()

    def _write(self, kind: str, record: dict[str, Any]) -> None:
        try:
            day_dir = self.dir / clock.day_dir()
            line = json.dumps(record, ensure_ascii=False, default=str)
            with self._lock:
                day_dir.mkdir(parents=True, exist_ok=True)
                with (day_dir / f"{kind}.jsonl").open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception as exc:  # noqa: BLE001 - 写账失败不能影响主流程
            self._write_errors += 1
            if self._write_errors == 1:
                log.warning("写原始层失败（之后同类错误只计数）：%s", exc)
