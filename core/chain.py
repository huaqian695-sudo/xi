"""前置链：标签 → 查证 → 出话。

四种走法：无图/有图 × 要查/不查。

M1 的标注是规则版（M2 换成真正的 label 场景，这里只换 guess_label 一个函数）。
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any

from scenes.base import Turn

# 一看就像在问事实的词
FACT_WORDS = (
    "天气", "气温", "降雨", "几点", "多少", "价格", "股价",
    "新闻", "查一下", "查查", "搜一下", "是谁", "什么时候",
    "在哪里", "多少岁", "多远", "多重", "汇率",
)
# 只是疑问语气，但可能不是事实问题
QUESTION_WORDS = ("吗", "？", "?", "呢", "怎么", "为什么", "能不能")


@dataclass
class Label:
    need_facts: str = "no"  # yes | no | uncertain
    topics: list[str] = field(default_factory=list)
    emotion_cue: str = "平静"
    has_image: bool = False
    confidence: float = 0.5
    source: str = "rule:m1"


@dataclass
class ChainResult:
    label: Label = field(default_factory=Label)
    facts: dict[str, Any] | None = None
    reply: Any = None
    chain: list[str] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    tool_uses: list[dict[str, Any]] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)


def guess_label(turn: Turn) -> Label:
    """M1 的规则版标注。宁可多查一次，也别装懂。"""
    text = turn.text or ""
    if any(w in text for w in FACT_WORDS):
        need = "yes"
        conf = 0.7
    elif any(w in text for w in QUESTION_WORDS):
        need = "uncertain"
        conf = 0.4
    else:
        need = "no"
        conf = 0.7

    cue = "平静"
    if any(w in text for w in ("哈哈", "笑死", "草", "!")):
        cue = "轻松"
    elif any(w in text for w in ("烦", "累", "难受", "崩", "气")):
        cue = "低落"
    elif any(w in text for w in ("谢谢", "厉害", "好棒")):
        cue = "愉快"

    return Label(
        need_facts=need,
        topics=[text[:20]] if text else [],
        emotion_cue=cue,
        has_image=bool(turn.images),
        confidence=conf,
        source="rule:m1",
    )


# 一眼就能判的：省掉一次模型调用（也就省 2–4 秒）
QUICK_NO = ("哈哈", "晚安", "早安", "在吗", "好的", "收到", "谢谢", "拜拜", "睡了", "吃了吗")
QUICK_YES = ("天气", "气温", "多少钱", "几点", "新闻", "汇率", "股价", "查一下", "搜一下")


def quick_judge(text: str) -> Label | None:
    """能一眼判完就别问模型。判不了返回 None。"""
    t = (text or "").strip()
    if not t:
        return Label(need_facts="no", emotion_cue="安静", confidence=0.5, source="rule:quick")
    if len(t) <= 8 and any(w in t for w in QUICK_NO):
        return Label(need_facts="no", emotion_cue="轻松", confidence=0.8, source="rule:quick")
    if any(w in t for w in QUICK_YES):
        return Label(need_facts="yes", emotion_cue="平静", confidence=0.85, source="rule:quick")
    return None


async def _label(turn: Turn, scenes: dict[str, Any], ctx) -> tuple[Label, list[dict[str, Any]]]:
    """先规则抢答，判不了再问模型；模型挂了退回规则。"""
    quick = quick_judge(turn.text)
    if quick is not None:
        return quick, []

    scene = scenes.get("label")
    if scene is None:
        return guess_label(turn), []
    try:
        result = await scene.run(turn, ctx)
        raw = result.output if isinstance(result.output, dict) else None
        if not raw:
            return guess_label(turn), result.calls
        label = Label(
            need_facts=str(raw.get("need_facts") or "uncertain"),
            topics=[str(x) for x in (raw.get("topics") or [])],
            emotion_cue=str(raw.get("emotion_cue") or "平静"),
            has_image=bool(raw.get("has_image", bool(turn.images))),
            confidence=float(raw.get("confidence") or 0.0),
            source="label",
        )
        return label, result.calls
    except Exception:  # noqa: BLE001 - 标注挂了不能挡路
        return guess_label(turn), []


def pick_scene(turn: Turn) -> str:
    return "chat.image" if turn.images else "chat.reply"


async def run(turn: Turn, scenes: dict[str, Any], ctx) -> ChainResult:
    """跑完一条消息的前置链，返回标签、结论和最后那句话。"""
    chain: list[str] = []

    # ① 标注（有 label 场景就用它，没有就退回规则）
    label, label_calls = await _label(turn, scenes, ctx)
    turn.label = dataclasses.asdict(label)
    chain.append("label")

    calls: list[dict[str, Any]] = list(label_calls)
    tool_uses: list[dict[str, Any]] = []
    blocks: list[str] = []

    # ② 要查证就先查
    facts = None
    if label.need_facts in ("yes", "uncertain") and "chat.qa" in scenes:
        qa_turn = dataclasses.replace(turn, scene="chat.qa")
        qa_result = await scenes["chat.qa"].run(qa_turn, ctx)
        calls.extend(qa_result.calls)
        tool_uses.extend(qa_result.tool_uses)
        blocks.extend((qa_result.meta or {}).get("blocks") or [])
        if isinstance(qa_result.output, dict):
            facts = qa_result.output
            turn.facts = facts
        chain.append("chat.qa")

    # ③ 出话（有图走 image，没有走 reply）
    scene_name = pick_scene(turn)
    scene = scenes.get(scene_name) or scenes["chat.reply"]
    if scene.name != scene_name:
        scene_name = scene.name
    turn.scene = scene_name
    reply = await scene.run(turn, ctx)
    chain.append(scene_name)
    calls.extend(reply.calls)
    tool_uses.extend(reply.tool_uses)
    blocks.extend((reply.meta or {}).get("blocks") or [])

    return ChainResult(
        label=label,
        facts=facts,
        reply=reply,
        chain=chain,
        calls=calls,
        tool_uses=tool_uses,
        blocks=blocks,
    )
