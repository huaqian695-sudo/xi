"""带图出话。

跟 chat.reply 同一套料，差别在两处：
1. 多一个"图片指令"常量块（先看图说话 / 图是分享不是证据 / 看不清直说）
2. user 里挂图片引用（M1 只挂路径，M3 接真模型时换成像素）
"""

from __future__ import annotations

from scenes.base import Scene, SceneResult, Turn, assemble


class ChatImage(Scene):
    name = "chat.image"

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
