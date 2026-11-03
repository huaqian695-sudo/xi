"""记忆提炼：把一段对话压成几条值得长期记住的东西。

输入是一批交互（不是一条消息），输出是 JSON 数组。
prompt 见 scenes/constants.py 的「提炼任务」，示例见大纲附录 F。
"""

from __future__ import annotations

from typing import Any

from core.parsing import extract_json
from scenes.base import Scene, SceneResult, Turn, assemble

VALID_KINDS = {"fact", "episode", "narrative", "inference", "knowledge", "self_action"}
VALID_STATUS = {"verified", "observed", "subjective", "inferred", "unverified"}


class MemoryExtract(Scene):
    name = "memory.extract"

    async def run(self, turn: Turn, ctx) -> SceneResult:
        recipe = ctx.recipe(self.name)
        messages, used = assemble(recipe, turn, ctx)

        result = await ctx.engine.run(
            messages,
            tier=recipe["档位"],
            toolbox=recipe.get("工具箱", "自定义"),
            budget=recipe.get("预算") or {},
            turn=turn,
            scene=self.name,
        )

        raw = extract_json(result.get("output") or "")
        items = self._clean(raw)

        return SceneResult(
            output=items,
            calls=result.get("calls", []),
            meta={"blocks": used, "parsed": len(items), "raw_ok": raw is not None},
        )

    @staticmethod
    def _clean(raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            return []
        out: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "").strip()
            if not content or len(content) > 200:
                continue
            kind = str(item.get("kind") or "fact")
            status = str(item.get("status") or "observed")
            out.append(
                {
                    "content": content,
                    "kind": kind if kind in VALID_KINDS else "fact",
                    "status": status if status in VALID_STATUS else "observed",
                    "source": str(item.get("source") or "")[:80] or None,
                }
            )
        return out
