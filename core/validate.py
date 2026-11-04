"""菜谱全量校验。

启动时跑一遍：场景名对得上、档位存在、工具箱存在、产品号存在、
块类型认识、谓词认识。错一条就拒绝启动，并指出哪张菜谱。

为什么要有这个：菜谱是"引用"不是"代码"，写错了不会当场 NameError，
只会在某个场景真去取块时才炸——那时候她已经在回话了。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

KNOWN_BLOCKS = {"人格", "此刻", "状态", "产品", "常量", "当前"}
KNOWN_TOOLBOXES = {"封装", "全量", "自定义"}
KNOWN_PREDICATES = {
    "非管理员私聊",
    "群聊",
    "带图",
    "新会话",
    "记忆为空",
    "有结论",
    "无结论",
}
PRODUCTS = {str(i) for i in range(1, 12)}


def validate(cfg, scenes_dir: Path | str, scenes: dict[str, Any]) -> list[str]:
    """返回问题列表。空表示全对。"""
    from scenes import constants
    from scenes.base import SceneContext

    scenes_dir = Path(scenes_dir)
    problems: list[str] = []
    tiers = (cfg.tiers.get("tiers") or {})

    ctx = SceneContext(cfg=cfg, scenes_dir=scenes_dir, rawlog=None, engine=None)

    # 1. 每个场景都要有菜谱
    for name in scenes:
        path = ctx.recipe_path(name)
        if not path.exists():
            problems.append(f"场景 {name} 没有菜谱：{path}")
            continue
        try:
            recipe = ctx.recipe(name)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"场景 {name} 的菜谱读不了：{exc}")
            continue

        # 2. 名字要对得上
        declared = recipe.get("场景")
        if declared != name:
            problems.append(f"菜谱 {path} 里「场景」写的是 {declared!r}，应为 {name!r}")

        # 3. 档位
        tier = recipe.get("档位")
        if not tier:
            problems.append(f"{name} 没写档位")
        elif tier not in tiers:
            problems.append(f"{name} 引用了不存在的档位：{tier}")

        # 4. 工具箱
        toolbox = recipe.get("工具箱", "封装")
        if toolbox not in KNOWN_TOOLBOXES:
            problems.append(f"{name} 的工具箱不认识：{toolbox}")

        # 5. 块
        for group in ("块", "用户", "历史"):
            for spec in recipe.get(group) or []:
                if not isinstance(spec, dict):
                    problems.append(f"{name} 的 {group} 里有不是字典的项：{spec!r}")
                    continue
                kinds = KNOWN_BLOCKS & set(spec)
                if not kinds:
                    problems.append(f"{name} 的 {group} 里有不认识的块：{spec!r}")
                    continue
                if "产品" in spec:
                    num = str(spec["产品"]).split(".")[0]
                    if num not in PRODUCTS:
                        problems.append(f"{name} 引用了不存在的产品号：{spec['产品']}")
                    if group == "历史" and num != "2":
                        problems.append(
                            f"{name} 的「历史」只认产品 2（交互流），收到：{spec['产品']}"
                        )
                if "常量" in spec and spec["常量"] not in constants.CONSTANTS:
                    problems.append(f"{name} 引用了不存在的常量：{spec['常量']}")
                pred = spec.get("当")
                if pred and pred not in KNOWN_PREDICATES:
                    problems.append(f"{name} 用了不认识的条件：{pred}")

    return problems


def validate_all_recipes(cfg, scenes_dir: Path | str) -> list[str]:
    """连孤儿菜谱一起校验——没有 scene.py 的文件夹，菜谱也可能是错的。"""
    import yaml

    from scenes import constants

    scenes_dir = Path(scenes_dir)
    problems: list[str] = []
    tiers = cfg.tiers.get("tiers") or {}

    for folder in sorted(p for p in scenes_dir.iterdir() if p.is_dir()):
        recipe_file = folder / "recipe.yml"
        if not recipe_file.exists():
            continue
        label = folder.name
        try:
            recipe = yaml.safe_load(recipe_file.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{label}/recipe.yml 读不了：{exc}")
            continue

        if not (folder / "scene.py").exists():
            # 孤儿菜谱：只校验内容，不校验名字
            name = f"{label}（未注册）"
        else:
            name = label

        tier = recipe.get("档位")
        if tier and tier not in tiers:
            problems.append(f"{name} 引用了不存在的档位：{tier}")
        toolbox = recipe.get("工具箱", "封装")
        if toolbox not in KNOWN_TOOLBOXES:
            problems.append(f"{name} 的工具箱不认识：{toolbox}")

        for group in ("块", "用户", "历史"):
            for spec in recipe.get(group) or []:
                if not isinstance(spec, dict):
                    problems.append(f"{name} 的 {group} 里有不是字典的项：{spec!r}")
                    continue
                if not (KNOWN_BLOCKS & set(spec)):
                    problems.append(f"{name} 的 {group} 里有不认识的块：{spec!r}")
                    continue
                if "产品" in spec:
                    num = str(spec["产品"]).split(".")[0]
                    if num not in PRODUCTS:
                        problems.append(f"{name} 引用了不存在的产品号：{spec['产品']}")
                    if group == "历史" and num != "2":
                        problems.append(
                            f"{name} 的「历史」只认产品 2（交互流），收到：{spec['产品']}"
                        )
                if "常量" in spec and spec["常量"] not in constants.CONSTANTS:
                    problems.append(f"{name} 引用了不存在的常量：{spec['常量']}")
                if "当前" in spec and str(spec["当前"]) not in ("消息", "图片"):
                    problems.append(f"{name} 的「当前」只能是「消息」或「图片」，收到：{spec['当前']}")
                pred = spec.get("当")
                if pred and pred not in KNOWN_PREDICATES:
                    problems.append(f"{name} 用了不认识的条件：{pred}")
    return problems


def orphan_recipes(scenes_dir: Path | str, scenes: dict[str, Any]) -> list[str]:
    """有菜谱、没有 scene.py 的文件夹。不算错（M0 的 tick 就是这样），但要说一声。"""
    scenes_dir = Path(scenes_dir)
    orphans = []
    for folder in sorted(p for p in scenes_dir.iterdir() if p.is_dir()):
        recipe = folder / "recipe.yml"
        if recipe.exists() and not (folder / "scene.py").exists():
            orphans.append(folder.name)
    return orphans
