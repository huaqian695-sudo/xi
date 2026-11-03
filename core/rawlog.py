"""原始层写入器：四本账，只追加，永不抛错。

它是唯一写 store/raw/ 的地方。写失败不能让她闭嘴——
所以这里的异常一律吞掉，只记一个 dropped 计数。
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from . import clock

KINDS = ("turns", "runs", "tools", "events")

log = logging.getLogger(__name__)


class RawLog:
    def __init__(self, store_root: Path | str):
        self.dir = Path(store_root) / "raw"
        self._lock = threading.Lock()
        self.dropped = 0

    def append(self, kind: str, record: dict[str, Any]) -> None:
        """写一行。kind 只能是四本账之一；失败不抛。"""
        try:
            if kind not in KINDS:
                raise ValueError(f"没有这本账：{kind}")
            day_dir = self.dir / clock.day_dir()
            line = json.dumps(record, ensure_ascii=False, default=str)
            with self._lock:
                day_dir.mkdir(parents=True, exist_ok=True)
                with (day_dir / f"{kind}.jsonl").open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception as exc:  # noqa: BLE001 - 写账失败不能影响主流程
            self.dropped += 1
            if self.dropped == 1:
                # 只喊一次，免得刷屏；之后靠 dropped 计数
                log.warning("写原始层失败（之后同类错误只计数不打印）：%s", exc)

    def ensure_day(self) -> None:
        """把今天的四个账本先开出来（空的也算开了账）。"""
        try:
            day_dir = self.dir / clock.day_dir()
            day_dir.mkdir(parents=True, exist_ok=True)
            for kind in KINDS:
                (day_dir / f"{kind}.jsonl").touch(exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            self.dropped += 1
            if self.dropped == 1:
                log.warning("开账本失败（之后同类错误只计数不打印）：%s", exc)

    def tail(self, kind: str, limit: int = 50) -> list[dict[str, Any]]:
        """读最近几行（调试和 /debug 用）。"""
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
