"""依赖方向的契约测试：core 不许认识 support。

方向只能是 support → core（适配层依赖核心），反过来就是核心被外围绑架：
换通道、换工具框架、单独跑核心测试，都得连 support 一起拖进来。

只靠自觉不够——`from support.tools.base import execute` 写进去照样能跑、
测试也照样全绿，所以这里从两个角度把闸门关上：

1. 静态：core/**.py 里模块级导入 support → 直接红
2. 静态：函数内**延迟**导入 support → 只允许豁免名单里那一处
3. 运行时：只 import core 的模块，sys.modules 里不许出现 support

豁免只有一条：`core/engine.py::default_tool_executor`，
作用是让 `Engine(cfg)` 这种老写法还能跑（正式装配在 main.py 里注入）。
哪天把它删了，就把 ALLOWED_LAZY 清空，这个测试会继续守着"一处都不许有"。
"""

from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# (文件名, 函数名) —— 唯一允许的延迟导入
ALLOWED_LAZY = {("engine.py", "default_tool_executor")}

CORE_MODULES = [
    "core.clock",
    "core.config",
    "core.chain",
    "core.db",
    "core.embed",
    "core.engine",
    "core.llm",
    "core.organize",
    "core.parsing",
    "core.rawlog",
    "core.repo",
    "core.validate",
]


def _is_support(name: str | None) -> bool:
    return bool(name) and (name == "support" or name.startswith("support."))


class _Scan(ast.NodeVisitor):
    """收集 core 里对 support 的导入，区分模块级和函数内。"""

    def __init__(self) -> None:
        self.module_level: list[int] = []
        self.lazy: list[tuple[str, int]] = []
        self._funcs: list[str] = []

    def _record(self, lineno: int, names: list[str]) -> None:
        if not any(_is_support(n) for n in names):
            return
        if self._funcs:
            self.lazy.append((self._funcs[-1], lineno))
        else:
            self.module_level.append(lineno)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._funcs.append(node.name)
        self.generic_visit(node)
        self._funcs.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_Import(self, node: ast.Import) -> None:
        self._record(node.lineno, [a.name for a in node.names])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        # 只关心绝对导入；core 内部的相对导入不可能是 support
        if node.level == 0:
            self._record(node.lineno, [node.module or ""])


class TestDependencyDirection(unittest.TestCase):
    def _scans(self):
        out = {}
        for path in sorted((ROOT / "core").rglob("*.py")):
            scan = _Scan()
            scan.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
            out[path.relative_to(ROOT).as_posix()] = scan
        return out

    def test_no_module_level_support_import(self):
        bad = [
            f"{path}:{lineno}"
            for path, scan in self._scans().items()
            for lineno in scan.module_level
        ]
        self.assertEqual(
            bad, [], f"core 在模块级导入了 support（反向依赖）：{bad}"
        )

    def test_lazy_support_import_is_whitelisted(self):
        found = {
            (Path(path).name, func)
            for path, scan in self._scans().items()
            for func, _ in scan.lazy
        }
        unexpected = sorted(found - ALLOWED_LAZY)
        self.assertEqual(
            unexpected,
            [],
            "core 里出现了新的 support 延迟导入。"
            "要么改成注入（推荐），要么把它加进 ALLOWED_LAZY 并说明理由："
            f"{unexpected}",
        )

    def test_importing_core_does_not_load_support(self):
        """运行时才成立的懒加载不算干净：import 阶段不许把 support 拖进来。"""
        code = (
            "import importlib, sys\n"
            f"mods = {CORE_MODULES!r}\n"
            "for m in mods:\n"
            "    importlib.import_module(m)\n"
            "leaked = sorted(n for n in sys.modules if n == 'support' "
            "or n.startswith('support.'))\n"
            "print('LEAK' if leaked else 'CLEAN', leaked[:5])\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(
            proc.stdout.startswith("CLEAN"),
            f"import core 时把 support 带进来了：{proc.stdout.strip()}",
        )


if __name__ == "__main__":
    unittest.main()
