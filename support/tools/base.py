"""工具的骨架：一个工具一张说明卡，注册表扫文件夹收集。

三件事：
1. `ToolSpec` 说清楚"这工具叫什么、干什么、要什么参数、属于哪个箱子"
2. 注册表扫 `support/tools/*.py`，把 `TOOL` 收集起来
3. `execute()` 统一执行并兜住异常——工具炸了不能把回复带走

加一个工具 = 加一个文件，core 一行不改。
"""

from __future__ import annotations

import importlib.util
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

TOOLBOXES = ("封装", "全量")


@dataclass
class ToolResult:
    ok: bool
    content: str
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.content if self.ok else f"（工具出错：{self.content}）"


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    toolbox: str = "封装"          # 封装 = 聊天可用；全量 = 只有心跳
    func: Callable[..., ToolResult] | None = None

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def load_tools(tools_dir: Path | str) -> dict[str, ToolSpec]:
    """扫目录，收集每个文件里的 TOOL。"""
    tools_dir = Path(tools_dir)
    found: dict[str, ToolSpec] = {}
    for path in sorted(tools_dir.glob("*.py")):
        # base.py 是框架本身，下划线开头的是私货，都不当工具
        if path.name.startswith("_") or path.stem in ("base", "__init__"):
            continue
        mod_name = f"xi_tool_{path.stem}"
        spec = importlib.util.spec_from_file_location(mod_name, path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001 - 一个工具坏了不该拖垮全部
            log.warning("工具文件加载失败 %s：%s", path.name, exc)
            continue
        # 一个文件里可以放多个工具（比如记忆三件）
        for obj in vars(module).values():
            if not isinstance(obj, ToolSpec):
                continue
            if obj.name in found:
                raise ValueError(f"工具重名：{obj.name}")
            found[obj.name] = obj
    return found


def for_toolbox(tools: dict[str, ToolSpec], toolbox: str) -> list[ToolSpec]:
    """按菜谱声明挑工具。

    「封装」= 只给封装箱；「全量」= 全给；「自定义」= 按人格白名单
    （白名单在 SceneContext 里处理，这里先返回空）。
    """
    if toolbox == "全量":
        return list(tools.values())
    if toolbox == "封装":
        return [t for t in tools.values() if t.toolbox == "封装"]
    return []


def execute(tool: ToolSpec, args: dict[str, Any], ctx) -> ToolResult:
    """执行一次工具调用。参数不对、工具炸了，都变成一句人话返回给模型。"""
    if tool.func is None:
        return ToolResult(False, f"{tool.name} 还没实现")

    # 只传模型真给了的参数，别自作主张塞默认值
    allowed = set((tool.parameters.get("properties") or {}).keys())
    unknown = set(args) - allowed
    if unknown:
        return ToolResult(
            False,
            f"{tool.name} 不认识这些参数：{', '.join(sorted(unknown))}。"
            f" 能用的是：{', '.join(sorted(allowed)) or '（无）'}",
        )

    required = tool.parameters.get("required") or []
    missing = [k for k in required if k not in args]
    if missing:
        return ToolResult(False, f"{tool.name} 缺必填参数：{', '.join(missing)}")

    try:
        return tool.func(ctx, **args)
    except TypeError as exc:
        return ToolResult(False, f"{tool.name} 参数不对劲：{exc}")
    except Exception as exc:  # noqa: BLE001 - 工具炸了不挡回复
        log.warning("工具 %s 执行失败：%s", tool.name, exc)
        return ToolResult(False, f"{type(exc).__name__}: {exc}")


def parse_args(raw: str | None) -> dict[str, Any]:
    """模型给的参数是 JSON 字符串，可能带围栏、可能坏。"""
    if not raw:
        return {}
    text = str(raw).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        from core.parsing import extract_json

        data = extract_json(text)
    return data if isinstance(data, dict) else {}
