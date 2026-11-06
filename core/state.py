"""产品 6 / 7 / 8 的读写：状态、内心、流水。

三样都是"她自己"的东西，都放在 `store/processed/`：

    6-state/xi/state.json        情绪 + 精力（结构化，拼 prompt 用）
    7-inner/xi/2026-11-06.md     内心，一天一页，只追加
    8-journal/xi/2026-11-06.md   流水，一天一页，一行一件事

为什么单独一个模块：`_state_block` 只负责**读**，这里补上**写**。
两边都直接读文件的话，格式一改就得改两处。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import clock

# 单次变化最多这么多。模型偶尔会说"我现在特别开心 +0.9"，
# 不夹住的话一次心跳就把情绪拉满，之后全是噪音。
MAX_STEP = 0.3

# 状态里这些键不往 prompt 里拼（它们是记账用的，不是"她的状态"）
HIDDEN_KEYS = {"updated_at", "ts"}

# 存储用英文键，拼进 prompt 时换成中文——她看到的是中文
LABELS = {"energy": "精力"}


def _dir(cfg, kind: str, persona: str) -> Path:
    path = cfg.store_root() / "processed" / kind / persona
    path.mkdir(parents=True, exist_ok=True)
    return path


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


# ---------------------------------------------------------------- 状态


def state_path(cfg, persona: str = "xi") -> Path:
    return _dir(cfg, "6-state", persona) / "state.json"


def read_state(cfg, persona: str = "xi") -> dict[str, Any]:
    path = state_path(cfg, persona)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(cfg, state: dict[str, Any], persona: str = "xi") -> None:
    data = dict(state)
    data["updated_at"] = clock.iso()
    path = state_path(cfg, persona)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def apply_emotion(state: dict[str, Any], deltas: Any) -> dict[str, Any]:
    """把模型给的"情绪变化量"合进状态。返回新的 state（不改原对象）。

    - 单维单次最多 ±MAX_STEP
    - 结果夹在 0..1
    - 非数字、坏键直接忽略，不让一条脏数据毁掉整份状态
    """
    if not isinstance(deltas, dict):
        return dict(state)

    new = dict(state)
    emotion = dict(new.get("emotion") or {})
    for name, raw in deltas.items():
        try:
            step = float(raw)
        except (TypeError, ValueError):
            continue
        if not str(name).strip():
            continue
        step = _clamp(step, -MAX_STEP, MAX_STEP)
        current = emotion.get(name, 0.0)
        try:
            current = float(current)
        except (TypeError, ValueError):
            current = 0.0
        emotion[name] = round(_clamp(current + step), 3)
    new["emotion"] = emotion
    return new


def apply_energy(state: dict[str, Any], delta: Any) -> dict[str, Any]:
    """精力也按变化量处理，规则和情绪一致。"""
    new = dict(state)
    try:
        step = float(delta)
    except (TypeError, ValueError):
        return new
    step = _clamp(step, -MAX_STEP, MAX_STEP)
    try:
        current = float(new.get("energy", 0.0))
    except (TypeError, ValueError):
        current = 0.0
    new["energy"] = round(_clamp(current + step), 3)
    return new


def render_state(state: dict[str, Any]) -> list[str]:
    """把状态渲染成 prompt 里的几行。_state_block 用这个。"""
    lines: list[str] = []
    for key, value in state.items():
        if key in HIDDEN_KEYS:
            continue
        label = LABELS.get(key, key)
        if key == "emotion" and isinstance(value, dict):
            dims = []
            for name, weight in value.items():
                try:
                    dims.append(f"{name} {float(weight):.2f}")
                except (TypeError, ValueError):
                    dims.append(f"{name} {weight}")
            if dims:
                lines.append(f"- 情绪：{'、'.join(dims)}")
        elif isinstance(value, (str, int, float)) and str(value).strip():
            lines.append(f"- {label}：{value}")
        elif isinstance(value, list) and value:
            lines.append(f"- {label}：{'、'.join(str(v) for v in value)}")
    return lines


# ---------------------------------------------------------------- 内心 / 流水


def _append(day_file: Path, line: str) -> None:
    day_file.parent.mkdir(parents=True, exist_ok=True)
    with day_file.open("a", encoding="utf-8") as f:
        f.write(line.rstrip("\n") + "\n")


def append_inner(cfg, text: str, persona: str = "xi", ts=None) -> Path:
    """内心：一天一页，一行一个念头。"""
    moment = ts or clock.now()
    path = _dir(cfg, "7-inner", persona) / f"{clock.day_dir(moment)}.md"
    _append(path, f"- [{moment:%H:%M}] {str(text).strip()}")
    return path


def append_journal(cfg, text: str, persona: str = "xi", ts=None) -> Path:
    """流水：一天一页，一行一件事（`HH:MM 前缀：内容`）。"""
    moment = ts or clock.now()
    path = _dir(cfg, "8-journal", persona) / f"{clock.day_dir(moment)}.md"
    _append(path, f"{moment:%H:%M} {str(text).strip()}")
    return path


def read_tail(path: Path, limit: int = 80) -> list[str]:
    """取最后几行（内心/流水块用）。文件不在就当空。"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [ln for ln in lines if ln.strip()][-limit:]
