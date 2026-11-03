"""场景基类、注册表、上下文。

一个场景 = 一个文件夹（`scene.py` + `recipe.yml`），四步：
    读菜谱 → 取块 → 拼 messages → 交回结果

加一个场景只要加一个文件夹，core 一行不用改。
"""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import constants


# ---------------------------------------------------------------- 数据结构


@dataclass
class Turn:
    """一次交互。原始层 turns.jsonl 的一行。"""

    turn_id: str
    persona_id: str = "xi"
    scene: str = ""
    chat_type: str = "private"
    chat_id: int = 0
    session_no: int = 1
    user_id: int = 0
    nickname: str = ""
    text: str = ""
    images: list[str] = field(default_factory=list)
    trigger: str = "message"
    msg_uid: int | None = None


@dataclass
class SceneResult:
    output: Any = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    tool_uses: list[dict[str, Any]] = field(default_factory=list)


class Scene:
    """所有场景的父类。子类只需要写 name 和 run。"""

    name: str = ""

    async def run(self, turn: Turn, ctx: "SceneContext") -> SceneResult:  # pragma: no cover
        raise NotImplementedError


# ---------------------------------------------------------------- 注册表


def load_scenes(scenes_dir: Path | str) -> dict[str, Scene]:
    """扫 scenes/*/scene.py，把每个 Scene 子类实例化，按 name 注册。"""
    scenes_dir = Path(scenes_dir)
    found: dict[str, Scene] = {}
    for folder in sorted(p for p in scenes_dir.iterdir() if p.is_dir()):
        scene_file = folder / "scene.py"
        if not scene_file.exists():
            continue
        mod_name = f"xi_scene_{folder.name}"
        spec = importlib.util.spec_from_file_location(mod_name, scene_file)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for obj in vars(module).values():
            if (
                isinstance(obj, type)
                and issubclass(obj, Scene)
                and obj is not Scene
                and getattr(obj, "name", "")
            ):
                instance = obj()
                if instance.name in found:
                    raise ValueError(f"场景名重复：{instance.name}")
                found[instance.name] = instance
    return found


# ---------------------------------------------------------------- 上下文


class SceneContext:
    """场景能碰的东西，全从这里拿。"""

    def __init__(
        self,
        *,
        cfg,
        scenes_dir: Path | str,
        rawlog,
        engine,
        persona_id: str = "xi",
    ):
        self.cfg = cfg
        self.scenes_dir = Path(scenes_dir)
        self.rawlog = rawlog
        self.engine = engine
        self.persona_id = persona_id
        self.admin_id = cfg.admin_id(persona_id)
        self._recipes: dict[str, dict[str, Any]] = {}

    # ---- 菜谱 -----------------------------------------------------
    def recipe_path(self, name: str) -> Path:
        return self.scenes_dir / name.replace(".", "_") / "recipe.yml"

    def recipe(self, name: str) -> dict[str, Any]:
        if name in self._recipes:
            return self._recipes[name]
        path = self.recipe_path(name)
        if not path.exists():
            raise FileNotFoundError(f"场景没有菜谱：{path}")
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if data.get("场景") != name:
            raise ValueError(
                f"菜谱里的「场景」写的是 {data.get('场景')!r}，但场景名是 {name!r}"
                f"（{path}）"
            )
        self._recipes[name] = data
        return data

    # ---- 条件块 ---------------------------------------------------
    def when(self, pred: str | None, turn: Turn) -> bool:
        if not pred:
            return True
        if pred == "非管理员私聊":
            return turn.chat_type == "private" and turn.user_id != self.admin_id
        if pred == "群聊":
            return turn.chat_type == "group"
        if pred == "带图":
            return bool(turn.images)
        if pred == "新会话":
            return turn.session_no == 1
        if pred == "记忆为空":
            return True  # M2 之前，记忆一定是空的
        if pred in ("有结论", "无结论"):
            return pred == "无结论"  # M2 之前没有事实块
        raise ValueError(f"不认识的条件：{pred}")

    # ---- 取块 -----------------------------------------------------
    def fetch(self, spec: dict[str, Any], turn: Turn) -> str:
        if "人格" in spec:
            return self._persona_file(str(spec["人格"]))
        if "此刻" in spec:
            return self._now_block(turn)
        if "状态" in spec:
            return self._state_block(str(spec["状态"]))
        if "产品" in spec:
            return self._product_block(str(spec["产品"]), spec)
        if "常量" in spec:
            key = str(spec["常量"])
            return constants.CONSTANTS.get(key, f"（未定义的常量：{key}）")
        if "当前" in spec:
            return turn.text
        raise ValueError(f"不认识的块：{spec}")

    def _persona_file(self, filename: str) -> str:
        base = self.cfg.store_root() / "processed" / "1-persona" / self.persona_id
        path = base / filename
        if not path.exists():
            return f"（缺人设文件：{filename}）"
        return path.read_text(encoding="utf-8").strip()

    def _now_block(self, turn: Turn) -> str:
        from core import clock

        now = clock.now()
        where = "私聊"
        if turn.chat_type == "group":
            where = f"群 {turn.chat_id}"
        who = "浅华本人" if turn.user_id == self.admin_id else f"QQ {turn.user_id}"
        return f"时间：{clock.stamp(now)}（{clock.time_slot(now)}）\n场合：{where}，对面是{who}"

    def _state_block(self, kind: str) -> str:
        path = self.cfg.store_root() / "processed" / "6-state" / self.persona_id / "state.json"
        if not path.exists():
            return "情绪：还没记录"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return "情绪：读不出来"
        emotion = data.get("emotion") or {}
        if not emotion:
            return "情绪：还没记录"
        line = "、".join(f"{k} {float(v):.2f}" for k, v in emotion.items())
        return f"情绪：{line}"

    def _product_block(self, name: str, spec: dict[str, Any]) -> str:
        num = name.split(".")[0]
        store = self.cfg.store_root()
        if num == "7":
            from core import clock

            path = store / "processed" / "7-inner" / self.persona_id / f"{clock.day_dir()}.md"
            if not path.exists():
                return "（今天还没想过什么）"
            lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            limit = int(spec.get("条数") or 1)
            return "\n".join(lines[-limit:])
        if num == "9":
            path = store / "processed" / "9-statuscard" / self.persona_id / "status.json"
            if not path.exists():
                return "（状态卡还没生成）"
            return path.read_text(encoding="utf-8")
        if num == "5":
            return "（记忆库还没接，M2 开工后才有）"
        if num == "4":
            return "（这轮没有查证结论）"
        if num == "3":
            return "（标注还没接，M2 开工后才有）"
        if num == "2":
            return "（历史还没接，M2 开工后才有）"
        return f"（产品 {name} 还没接）"

    # ---- 成品留档 -------------------------------------------------
    def archive(self, turn: Turn, messages: list[dict[str, Any]], result: dict[str, Any]) -> None:
        from core import clock

        try:
            day = clock.day_dir()
            out_dir = self.cfg.store_root() / "products" / self.persona_id / day
            out_dir.mkdir(parents=True, exist_ok=True)
            body = [f"# {turn.turn_id}", "", f"- 场景：{turn.scene}", f"- 时间：{clock.iso()}", ""]
            for msg in messages:
                body.append(f"## {msg.get('role')}")
                body.append("")
                body.append(str(msg.get("content", "")))
                body.append("")
            body.append("## 输出")
            body.append("")
            body.append(str(result.get("output", "")))
            (out_dir / f"{turn.turn_id}.md").write_text("\n".join(body), encoding="utf-8")
        except Exception:  # noqa: BLE001 - 留档失败不影响回复
            pass
