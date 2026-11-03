"""查证：先查清楚再说话。

产出不是一句话，是一组结论（写进产品 4）。出话场景再拿着结论去说话。
M0 阶段还没有真工具，只走通装配和返回形状。
"""

from __future__ import annotations

from scenes.base import Scene, SceneResult, Turn


class ChatQa(Scene):
    name = "chat.qa"

    async def run(self, turn: Turn, ctx) -> SceneResult:
        recipe = ctx.recipe(self.name)

        blocks: list[str] = []
        for spec in recipe.get("块") or []:
            if ctx.when(spec.get("当"), turn):
                blocks.append(ctx.fetch(spec, turn))

        user_blocks: list[str] = []
        for spec in recipe.get("用户") or []:
            if ctx.when(spec.get("当"), turn):
                user_blocks.append(ctx.fetch(spec, turn))

        messages = [
            {"role": "system", "content": "\n\n".join(b for b in blocks if b).strip()},
            {"role": "user", "content": "\n\n".join(b for b in user_blocks if b).strip()},
        ]

        result = await ctx.engine.run(
            messages,
            tier=recipe["档位"],
            toolbox=recipe.get("工具箱", "封装"),
            budget=recipe.get("预算") or {},
            turn=turn,
            scene=self.name,
        )

        ctx.archive(turn, messages, result)

        # 结论的形状先定下来，M3 接真工具后往 facts 里填
        facts = {
            "question": turn.text,
            "facts": [],
            "not_found": [],
            "note": "还没有接搜索工具",
        }

        # 有结论就落库（现在恒为空，M3 接上工具后这条路自动生效）
        ctx.save_facts(turn, facts)
        turn.facts = facts

        return SceneResult(
            output=facts,
            calls=result.get("calls", []),
            tool_uses=result.get("tool_uses", []),
        )
