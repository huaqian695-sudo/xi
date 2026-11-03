"""出话：纯文字消息。

四步：读菜谱 → 取块 → 拼 messages → 交给引擎。
场景不直接调 LLM、不自己发消息、不写原始层、不调用别的场景。
"""

from __future__ import annotations

from scenes.base import Scene, SceneResult, Turn, assemble


class ChatReply(Scene):
    name = "chat.reply"

    async def run(self, turn: Turn, ctx) -> SceneResult:
        recipe = ctx.recipe(self.name)
        messages, used = assemble(recipe, turn, ctx)

        result = await ctx.engine.run(
            messages,
            tier=recipe["档位"],
            toolbox=recipe.get("工具箱", "封装"),
            budget=recipe.get("预算") or {},
            turn=turn,
            scene=self.name,
        )

        ctx.archive(turn, messages, result)

        return SceneResult(
            output=result.get("output", ""),
            calls=result.get("calls", []),
            tool_uses=result.get("tool_uses", []),
            meta={"blocks": used},
        )
