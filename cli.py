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

    persona_dir = cfg.store_root() / "processed" / "1-persona" / "xi"
    files = sorted(p.name for p in persona_dir.glob("*.md")) if persona_dir.exists() else []
    print(f"人设：{len(files)} 份 — {', '.join(files) if files else '（空）'}")

    if day_dir.exists():
        counts = []
        for kind in KINDS:
            path = day_dir / f"{kind}.jsonl"
            n = len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0
            counts.append(f"{kind}={n}")
        print(f"今天账本：{day_dir.name}  {'  '.join(counts)}")
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
        if not tiers:
            problems.append("tiers.yml 里没有档位")
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
        for name, scene in scenes.items():
            try:
                cfg_recipe = scene and None
                from scenes.base import SceneContext

                ctx = SceneContext(cfg=cfg, scenes_dir=ROOT / "scenes", rawlog=None, engine=None)
                recipe = ctx.recipe(name)
                tier = recipe.get("档位")
                if tier and tier not in (cfg.tiers.get("tiers") or {}):
                    problems.append(f"菜谱 {name} 引用了不存在的档位：{tier}")
            except Exception as exc:  # noqa: BLE001
                problems.append(f"菜谱 {name} 有问题：{exc}")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"加载场景失败：{exc}")

    persona_dir = cfg.store_root() / "processed" / "1-persona" / "xi"
    for name in ("self.md", "chat.md", "thinking.md", "tick-options.md"):
        if not (persona_dir / name).exists():
            problems.append(f"缺人设：{name}")

    raw = RawLog(cfg.store_root())
    raw.ensure_day()
    if not (cfg.store_root() / "raw").exists():
        problems.append("store/raw 写不了")

    if problems:
        print(f"发现 {len(problems)} 个问题：")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("自检通过。")
    return 0


def cmd_not_yet(cfg: Config, args: argparse.Namespace) -> int:
    print(f"{args.command} 还没接（按清单，属于后面的里程碑）。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cli.py", description="汐的运维入口")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="看服务状态").set_defaults(func=cmd_status)
    sub.add_parser("doctor", help="自检配置/目录/人设").set_defaults(func=cmd_doctor)

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
