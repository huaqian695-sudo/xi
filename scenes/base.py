"""场景基类、注册表、上下文。

一个场景 = 一个文件夹（`scene.py` + `recipe.yml`），四步：
    读菜谱 → 取块 → 拼 messages → 交回结果

加一个场景只要加一个文件夹，core 一行不用改。
"""

from __future__ import annotations

import importlib.util
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import constants

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- 数据结构


@dataclass
class Turn:
    """一次交互。原始层 turns.jsonl 的一行。"""

    turn_id: str
    persona_id: str = "xi"
    scene: str = ""
    channel: str = "stub"
    chat_type: str = "private"
    chat_id: str = ""
    sub_type: str = ""
    session_no: int = 1
    user_id: str = ""
    nickname: str = ""
    text: str = ""
    images: list[str] = field(default_factory=list)
    trigger: str = "message"
    msg_uid: str | None = None
    at_me: bool = False
    reply_to: str | None = None
    anonymous: bool = False
    label: dict[str, Any] | None = None
    facts: dict[str, Any] | None = None


@dataclass
class SceneResult:
    output: Any = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    tool_uses: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


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


def assemble(recipe: dict[str, Any], turn: "Turn", ctx: "SceneContext") -> tuple[list[dict], list[str]]:
    """四步里的第二、三步：按菜谱取块、拼成 messages。

    产出的是**多轮对话**，不是一段文本：
        [system, 历史第1轮 user, 历史第1轮 assistant, ..., 当前 user]

    为什么：模型对 role 敏感。历史塞进一条 user 里，
    它看到的是"一段提到对话的文字"，而不是"我在跟这个人聊天"。
    cron
    返回 (messages, 用了哪些块)。场景只需要调它。
    """
    used: list[str] = []
    system_blocks: list[str] = []
    for spec in recipe.get("块") or []:
        if ctx.when(spec.get("当"), turn):
            text = ctx.fetch(spec, turn)
            if text:
                system_blocks.append(text)
            used.append(block_name(spec))

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "\n\n".join(system_blocks).strip()}
    ]

    # 历史：展开成一条条 user / assistant
    for spec in recipe.get("历史") or []:
        if ctx.when(spec.get("当"), turn):
            messages.extend(ctx.fetch_history(spec, turn))
            used.append("历史/" + block_name(spec))

    # 当前这一轮
    user_blocks: list[str] = []
    for spec in recipe.get("用户") or []:
        if ctx.when(spec.get("当"), turn):
            text = ctx.fetch(spec, turn)
            if text:
                user_blocks.append(text)
            used.append("用户/" + block_name(spec))

    messages.append({"role": "user", "content": "\n\n".join(user_blocks).strip()})
    return messages, used


def _stamp(ts: str | None, today: str) -> str:
    """[12:43] 或 [11-03 12:43]（不是今天才带日期）。"""
    raw = str(ts or "")
    if len(raw) < 16:
        return ""
    day, clock_part = raw[:10], raw[11:16]
    if day == today:
        return f"[{clock_part}] "
    return f"[{day[5:]} {clock_part}] "


def block_name(spec: dict[str, Any]) -> str:
    """给块起个短名字，成品留档里用（"人格:self.md"、"产品:5.记忆库"）。"""
    for kind in ("人格", "此刻", "状态", "产品", "常量", "当前", "动作"):
        if kind in spec:
            return f"{kind}:{spec[kind]}"
    return str(spec)


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
        self.owner_ids = cfg.owner_accounts(persona_id)
        self.owner_name = cfg.owner_name(persona_id)
        self.admin_id = str(cfg.admin_id(persona_id))  # 兼容老路径
        self._recipes: dict[str, dict[str, Any]] = {}
        self._tools: dict[str, Any] | None = None

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

    # ---- 工具 -----------------------------------------------------
    def tools_for(self, toolbox: str) -> list[Any]:
        """按菜谱声明的工具箱挑工具。聊天路径永远拿不到全量箱。

        三层叠加：
        1. 菜谱说用哪个箱（封装 / 全量 / 自定义）
        2. `persona.yml` 的 allow——**封装箱也过一遍**，这样"某个号更保守"才落得下去
        3. 全量箱不受 allow 限制（心跳要用她全部的动手能力）
        """
        from support.tools.base import for_toolbox, load_tools

        if self._tools is None:
            self._tools = load_tools(self.cfg.root / "support" / "tools")

        allow = set(self.cfg.tool_allow(self.persona_id))

        if toolbox == "自定义":
            return [t for t in self._tools.values() if t.name in allow]

        picked = for_toolbox(self._tools, toolbox)
        if toolbox == "封装" and allow:
            picked = [t for t in picked if t.name in allow]
        return picked

    # ---- 条件块 ---------------------------------------------------
    def when(self, pred: str | None, turn: Turn) -> bool:
        if not pred:
            return True
        if pred == "非管理员私聊":
            return turn.chat_type == "private" and not self._is_owner(turn)
        if pred == "群聊":
            return turn.chat_type == "group"
        if pred == "带图":
            return bool(turn.images)
        if pred == "新会话":
            return turn.session_no == 1
        if pred == "记忆为空":
            return True  # M2 之前，记忆一定是空的
        if pred in ("有结论", "无结论"):
            has = bool((turn.facts or {}).get("facts"))
            return has if pred == "有结论" else not has
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
            return self._product_block(str(spec["产品"]), spec, turn)
        if "常量" in spec:
            key = str(spec["常量"])
            return constants.CONSTANTS.get(key, f"（未定义的常量：{key}）")
        if "动作" in spec:
            return self._actions_block(turn)
        if "当前" in spec:
            what = str(spec["当前"])
            if what not in ("消息", "图片"):
                raise ValueError(f"「当前」只认「消息」和「图片」，收到：{what}")
            if what == "图片":
                if not turn.images:
                    return ""
                return "（对方发了图：\n" + "\n".join(f"- {p}" for p in turn.images) + "）"
            return turn.text
        raise ValueError(f"不认识的块：{spec}")

    def _persona_file(self, filename: str) -> str:
        base = self.cfg.persona_root() / self.persona_id
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
        who = f"{self.owner_name}本人" if self._is_owner(turn) else f"用户 {turn.user_id}"
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

    def _is_owner(self, turn: Turn) -> bool:
        """按平台判断：同一个真人在不同平台的号都算主人。"""
        want = self.owner_ids.get(str(turn.channel))
        return bool(want) and want == str(turn.user_id)

    def save_facts(self, turn: Turn, facts: dict[str, Any]) -> list[int]:
        """把 qa 的结论写进事实库。有结论才写——没查到就不留空记录。"""
        from core import repo

        statements = facts.get("facts") or []
        if not statements:
            return []
        try:
            return repo.put_facts(
                self.cfg,
                question=str(facts.get("question") or turn.text),
                conversation_key=f"{turn.chat_type}:{turn.chat_id}",
                statements=statements,
                persona=self.persona_id,
            )
        except Exception:  # noqa: BLE001 - 写事实失败不挡回复
            return []

    def save_label(self, turn: Turn, label: dict[str, Any]) -> None:
        try:
            from core import repo

            repo.upsert_label(
                self.cfg,
                message_uid=str(turn.msg_uid or turn.turn_id),
                label=label,
                persona=self.persona_id,
            )
        except Exception:  # noqa: BLE001 - 标注存不下也不挡路
            pass

    def _product_block(self, name: str, spec: dict[str, Any], turn: Turn) -> str:
        num = name.split(".")[0]
        store = self.cfg.store_root()

        from core import repo

        if num == "2":
            return self._history_block(turn, spec)
        if num == "5":
            return self._memory_block(turn, spec)
        if num == "8":
            return self._journal_block(spec)
        if num == "11":
            return self._digest_block(spec)
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

        if num == "4":
            facts = turn.facts
            if not facts or not facts.get("facts"):
                return "（这轮没有查证结论）"
            lines = []
            for item in facts["facts"]:
                src = item.get("source") or "未注明"
                lines.append(f"- {item.get('statement')}（来源：{src}）")
            return "\n".join(lines)
        if num == "3":
            label = turn.label or {}
            cue = label.get("emotion_cue") or ""
            return f"对方语气：{cue}" if cue else ""
        return f"（产品 {name} 还没接）"

    def fetch_history(self, spec: dict[str, Any], turn: Turn) -> list[dict[str, Any]]:
        """把历史展开成 messages 轮次。

        私聊：不加前缀，role 已经说明是谁。
        群聊：加"名字："，否则所有人都变成同一个"user"。
        """
        from core import clock, repo

        limit = int(spec.get("上限") or 100)
        scope = str(spec.get("范围") or "当前段")

        try:
            if scope == "跨会话":
                rows = repo.recent_all(
                    self.cfg, channel=turn.channel, limit=limit, persona=self.persona_id
                )
            else:
                rows = repo.recent_messages(
                    self.cfg,
                    chat_id=turn.chat_id,
                    channel=turn.channel,
                    session_no=turn.session_no,
                    limit=limit,
                    persona=self.persona_id,
                )
        except Exception:  # noqa: BLE001 - 库还没建时不该挡住出话
            return []

        today = clock.day_dir()
        out: list[dict[str, Any]] = []
        for row in rows:
            if str(row.get("turn_id") or "") == turn.turn_id:
                continue  # 这一轮还没入账，跳过
            text = str(row.get("content") or "").strip()
            if not text:
                continue

            if row.get("actor") == "他":
                prefix = _stamp(row.get("ts"), today)
                if turn.chat_type == "group":
                    name = row.get("nickname") or row.get("user_id") or "某人"
                    prefix += f"{name}："
                out.append({"role": "user", "content": prefix + text})
            else:
                out.append({"role": "assistant", "content": text})
        return out

    def _history_block(self, turn: Turn, spec: dict[str, Any]) -> str:
        from core import repo

        try:
            return self._history_block_inner(turn, spec)
        except Exception:  # noqa: BLE001
            return "（历史还没建）"

    def _history_block_inner(self, turn: Turn, spec: dict[str, Any]) -> str:
        from core import repo

        rows = repo.recent_messages(
            self.cfg,
            chat_id=turn.chat_id,
            channel=turn.channel,
            session_no=turn.session_no,
            limit=int(spec.get("上限") or 100),
            persona=self.persona_id,
        )
        rows = [r for r in rows if r.get("turn_id") != turn.turn_id]
        if not rows:
            return "（这是这个会话的第一句）"
        lines = []
        for r in rows:
            who = "他" if r.get("actor") == "他" else "我"
            lines.append(f"{who}：{r.get('content') or ''}")
        return "\n".join(lines)

    def _memory_block(self, turn: Turn, spec: dict[str, Any]) -> str:
        from core import repo

        try:
            return self._memory_block_inner(turn, spec)
        except Exception:  # noqa: BLE001 - 库还没建时不该挡住出话
            return "（记忆库还没建）"

    def _memory_block_inner(self, turn: Turn, spec: dict[str, Any]) -> str:
        from core import repo

        query = str(spec.get("查询") or "")
        if not query or query == "当前消息":
            query = turn.text
        elif query == "眼前的事":
            query = turn.text or "最近"
        hits = repo.search_memory(
            self.cfg,
            query,
            limit=int(spec.get("top") or 10),
            persona=self.persona_id,
            exclude_recent_days=float(spec.get("排除最近") or 3),
        )
        if not hits:
            return "（没有相关记忆）"
        lines = []
        for h in hits:
            tag = h.get("tag")
            prefix = f"（{tag}）" if tag else ""
            # 时间是记忆的一部分：「不吃辣」是今天说的还是上个月说的，分量不一样
            day = str(h.get("created_at") or "")[:10]
            stamp = f"[{day}] " if day else ""
            lines.append(f"- {stamp}{prefix}{h['content']}")
        return "\n".join(lines)

    def _actions_block(self, turn: Turn) -> str:
        """上一轮我动过什么手。

        为什么要这个：她看不到自己的工具调用记录，被问"你刚才查了什么"
        会以为是编的。这块补的就是这个。
        """
        from core import repo

        try:
            rows = repo.recent_actions(self.cfg, channel=turn.channel, limit=20)
        except Exception:  # noqa: BLE001
            return ""
        if not rows:
            return ""
        latest = str(rows[0].get("turn_id") or "")
        same = [r for r in rows if str(r.get("turn_id")) == latest]
        if not same:
            return ""
        lines = [f"- {r.get('content') or r.get('ref')}" for r in reversed(same)]
        return (
            "# 上一轮我做了什么（这是真的执行记录，不是你编的）\n"
            + "\n".join(lines)
        )

    def _journal_block(self, spec: dict[str, Any]) -> str:
        from core import clock

        path = self.cfg.store_root() / "processed" / "8-journal" / self.persona_id / f"{clock.day_dir()}.md"
        if not path.exists():
            return "（今天还没有流水）"
        lines = path.read_text(encoding="utf-8").splitlines()
        return "\n".join(lines[-int(spec.get("行数") or 80):])

    def _digest_block(self, spec: dict[str, Any]) -> str:
        from core import clock
        from datetime import timedelta

        day = (clock.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        path = self.cfg.store_root() / "processed" / "11-digest" / self.persona_id / f"{day}.md"
        if not path.exists():
            return "（昨天没有日结）"
        return path.read_text(encoding="utf-8")

    # ---- 成品留档 -------------------------------------------------
    def archive(self, turn: Turn, messages: list[dict[str, Any]], result: dict[str, Any]) -> None:
        from core import clock

        try:
            day = clock.day_dir()
            out_dir = self.cfg.store_root() / "products" / self.persona_id / day
            out_dir.mkdir(parents=True, exist_ok=True)
            body = [f"# {turn.turn_id}", "", f"- 场景：{turn.scene}", f"- 时间：{clock.iso()}"]
            blocks = (result.get("meta") or {}).get("blocks")
            if blocks:
                body.append("- 拼了这些块：" + " → ".join(blocks))
            body.append("")
            for msg in messages:
                body.append(f"## {msg.get('role')}")
                body.append("")
                body.append(str(msg.get("content", "")))
                body.append("")
            body.append("## 输出")
            body.append("")
            body.append(str(result.get("output", "")))
            (out_dir / f"{turn.turn_id}.md").write_text("\n".join(body), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - 留档失败不影响回复
            if not getattr(self, "_archive_warned", False):
                self._archive_warned = True
                log.warning("成品留档失败（之后同类错误不再打印）：%s", exc)
