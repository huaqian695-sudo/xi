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


def pick_scene(turn: Turn) -> str:
    return "chat.image" if turn.images else "chat.reply"


async def run(turn: Turn, scenes: dict[str, Any], ctx) -> ChainResult:
    """跑完一条消息的前置链，返回标签、结论和最后那句话。"""
    chain: list[str] = []

    # ① 标注
    label = guess_label(turn)
    turn.label = dataclasses.asdict(label)
    chain.append("label")

    calls: list[dict[str, Any]] = []
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
