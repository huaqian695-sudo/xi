"""运维入口：status / think / memory / dsh / apply / doctor

M0 只有 status 和 doctor 是真的，其余先给一句"还没接"。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core import clock
from core.config import Config
from core.rawlog import RawLog, KINDS
from core.validate import orphan_recipes, unconsumed_recipe_parts, validate
from scenes.base import load_scenes

ROOT = Path(__file__).resolve().parent


def cmd_status(cfg: Config, args: argparse.Namespace) -> int:
    scenes = load_scenes(ROOT / "scenes")
    tiers = (cfg.tiers.get("tiers") or {})
    day_dir = cfg.store_root() / "raw" / clock.day_dir()

    print(f"项目：{ROOT}")
    print(f"时间：{clock.stamp()}")
    print(f"档位：{len(tiers)} 个 — {', '.join(tiers)}")
    print(f"场景：{len(scenes)} 个 — {', '.join(sorted(scenes))}")
    print(f"通道：{(cfg.core.get('channel') or {}).get('type')}")

    # 白名单里写了但没实现的工具——配置看着像允许，其实调不到
    try:
        from support.tools.base import load_tools

        registered = set(load_tools(ROOT / "support" / "tools"))
        for name in cfg.tool_allow("xi"):
            if name not in registered:
                print(f"提示：persona.yml 的 allow 里有 {name}，但还没有实现（调不到）")
    except Exception:  # noqa: BLE001
        pass

    persona_dir = cfg.persona_root() / "xi"
    files = sorted(p.name for p in persona_dir.glob("*.md")) if persona_dir.exists() else []
    print(f"人设：{len(files)} 份 — {', '.join(files) if files else '（空）'}")

    if day_dir.exists():
        counts = []
        for kind in KINDS:
            path = day_dir / f"{kind}.jsonl"
            n = len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0
            counts.append(f"{kind}={n}")
        print(f"今天账本：{day_dir.name}  {'  '.join(counts)}")

        # 异常不能只躺在 jsonl 里——没人翻就等于没记
        raw = RawLog(cfg.store_root())
        errors = [e for e in raw.tail("events", 500) if e.get("kind") == "error"]
        if errors:
            print(f"今日异常：{len(errors)} 条")
            for e in errors[-3:]:
                print(f"  - {str(e.get('detail'))[:90]}")
        else:
            print("今日异常：无")
    else:
        print("今天账本：还没开")
    return 0


def cmd_doctor(cfg: Config, args: argparse.Namespace) -> int:
    problems: list[str] = []

    for rel in ("config/core.yml", "config/providers.yml", "config/tiers.yml",
                "config/services.yml", "config/personas/xi/persona.yml"):
        if not (ROOT / rel).exists():
            problems.append(f"缺配置：{rel}")

    try:
        tiers = cfg.tiers.get("tiers") or {}
        for name, tier in tiers.items():
            provider = tier.get("provider")
            if provider and provider not in (cfg.providers.get("providers") or {}):
                problems.append(f"档位 {name} 引用了不存在的 provider：{provider}")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"读 tiers.yml 失败：{exc}")

    try:
        scenes = load_scenes(ROOT / "scenes")
        if not scenes:
            problems.append("scenes/ 里没有场景")
        else:
            problems.extend(validate(cfg, ROOT / "scenes", scenes))
    except Exception as exc:  # noqa: BLE001
        problems.append(f"加载场景失败：{exc}")

    persona_dir = cfg.persona_root() / "xi"
    for name in ("self.md", "chat.md", "thinking.md", "tick-options.md"):
        if not (persona_dir / name).exists():
            problems.append(f"缺人设：{name}")

    raw = RawLog(cfg.store_root())
    raw.ensure_day()
    if not (cfg.store_root() / "raw").exists():
        problems.append("store/raw 写不了")

    try:
        scenes = load_scenes(ROOT / "scenes")
        for name in orphan_recipes(ROOT / "scenes", scenes):
            print(f"提示：{name}/ 有菜谱但还没有 scene.py（未实现，注册表会跳过）")
        # 声明跟实现对不上：只提示，不拒绝启动（见 core/validate.py）
        for warning in unconsumed_recipe_parts(cfg, ROOT / "scenes", scenes):
            print(f"提示：{warning}")
    except Exception:  # noqa: BLE001
        pass

    if problems:
        print(f"发现 {len(problems)} 个问题：")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("自检通过。")
    return 0


def cmd_last(cfg: Config, args: argparse.Namespace) -> int:
    """验收用：刚才那一句，标签是什么、查了什么、拼了哪几块、回了什么。"""
    raw = RawLog(cfg.store_root())
    rows = raw.tail("turns", 1)
    if not rows:
        print("今天还没有记录。先跑一句 `python3 main.py`。")
        return 0
    t = rows[-1]
    print(f"时间    {t.get('ts')}")
    print(f"他说的  {t.get('text')}")
    print(f"走了    {' → '.join(t.get('scene_chain') or [])}")
    label = t.get("label") or {}
    print(f"标签    要查证={label.get('need_facts')} 语气={label.get('emotion_cue')}"
          f"（{label.get('source')}）")
    facts = t.get("facts") or {}
    print(f"查证    {facts.get('count', 0)} 条结论，没查到 {len(facts.get('not_found') or [])} 项")
    blocks = t.get("blocks") or []
    print(f"拼了    {len(blocks)} 块")
    for b in blocks:
        print(f"        - {b}")
    print(f"她回的  {t.get('reply')}")
    return 0


def cmd_not_yet(cfg: Config, args: argparse.Namespace) -> int:
    print(f"{args.command} 还没接（按清单，属于后面的里程碑）。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cli.py", description="汐的运维入口")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="看服务状态").set_defaults(func=cmd_status)
    sub.add_parser("doctor", help="自检配置/目录/人设/菜谱").set_defaults(func=cmd_doctor)
    sub.add_parser("last", help="看刚才那一句走完的全过程").set_defaults(func=cmd_last)

    for name, desc in (
        ("think", "手动触发一次心跳（M4）"),
        ("memory", "查/关记忆（M2）"),
        ("dsh", "看派活（M5）"),
        ("apply", "落地补丁（M5）"),
    ):
        sp = sub.add_parser(name, help=desc)
        sp.set_defaults(func=cmd_not_yet)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = Config(ROOT)
    return args.func(cfg, args)


if __name__ == "__main__":
    raise SystemExit(main())
