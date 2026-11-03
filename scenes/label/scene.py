"""消息标注：这句话要不要查证、什么话题、对方什么语气。

3 秒软截止——到点没回来就标"拿不准"，继续往下走。
宁可多查一次，也不让整条消息卡在标注上。
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.parsing import extract_json
from scenes.base import Scene, SceneResult, Turn, assemble

VALID_NEED = {"yes", "no", "uncertain"}
DEFAULT_TIMEOUT = 3.0


def fallback_label(turn: Turn, why: str = "超时") -> dict[str, Any]:
    return {
        "need_facts": "uncertain",
        "registers": ["闲聊"],
        "topics": [],
        "emotion_cue": "说不准",
        "suggested_tools": [],
        "has_image": bool(turn.images),
        "confidence": 0.0,
        "note": f"标注没回来（{why}），按拿不准处理",
    }


def normalize(raw: Any, turn: Turn) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return fallback_label(turn, "输出不是对象")
    need = str(raw.get("need_facts") or "").lower()
    if need not in VALID_NEED:
        need = "uncertain"
    return {
        "need_facts": need,
        "registers": [str(x) for x in (raw.get("registers") or [])][:4],
        "topics": [str(x) for x in (raw.get("topics") or [])][:8],
        "emotion_cue": str(raw.get("emotion_cue") or "平静")[:40],
        "suggested_tools": [str(x) for x in (raw.get("suggested_tools") or [])][:5],
        "has_image": bool(raw.get("has_image", bool(turn.images))),
        "confidence": float(raw.get("confidence") or 0.5),
    }


class Label(Scene):
    name = "label"

    async def run(self, turn: Turn, ctx) -> SceneResult:
        recipe = ctx.recipe(self.name)
        messages, used = assemble(recipe, turn, ctx)
        timeout = float((recipe.get("预算") or {}).get("超时") or DEFAULT_TIMEOUT)

        calls: list[dict[str, Any]] = []
        label: dict[str, Any]
        try:
            result = await asyncio.wait_for(
                ctx.engine.run(
                    messages,
                    tier=recipe["档位"],
                    toolbox=recipe.get("工具箱", "自定义"),
                    budget=recipe.get("预算") or {},
                    turn=turn,
                    scene=self.name,
                ),
                timeout=timeout,
            )
            calls = result.get("calls") or []
            parsed = extract_json(result.get("output") or "")
            label = normalize(parsed, turn) if parsed is not None else fallback_label(
                turn, "输出解析不了"
            )
        except asyncio.TimeoutError:
            label = fallback_label(turn, f"{timeout:.0f} 秒没回来")
        except Exception as exc:  # noqa: BLE001 - 标注失败不能挡路
            label = fallback_label(turn, f"{type(exc).__name__}")

        ctx.save_label(turn, label)

        return SceneResult(
            output=label,
            calls=calls,
            meta={"blocks": used, "fallback": label.get("note") is not None},
        )
