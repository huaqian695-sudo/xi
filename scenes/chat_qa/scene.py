"""查证：先查清楚再说话。

产出不是一句话，是一组结论（写进产品 4）。出话场景再拿着结论去说话。
"""

from __future__ import annotations

from scenes.base import RecipeScene, SceneResult, Turn


def build_facts(result: dict, turn: Turn) -> dict:
    """把查证结果变成结构化的 facts。

    优先按约定解析 JSON（prompt 里要求了）。模型不听话时兜底：
    有工具结果就把工具输出当一条结论，没有就记 not_found——
    宁可说"没查到"，也不拿猜的顶。
    """
    from core.parsing import extract_json

    parsed = extract_json(result.get("output") or "")
    if isinstance(parsed, dict) and isinstance(parsed.get("facts"), list):
        items = []
        for raw in parsed["facts"]:
            if not isinstance(raw, dict):
                continue
            statement = str(raw.get("statement") or "").strip()
            if not statement:
                continue
            items.append(
                {
                    "statement": statement,
                    "source": str(raw.get("source") or "").strip() or None,
                    "confidence": raw.get("confidence"),
                }
            )
        if items:
            return {
                "question": turn.text,
                "facts": items,
                "not_found": [str(x) for x in (parsed.get("not_found") or [])],
            }

    # 兜底一：有工具结果，就把工具输出当结论（至少别丢已经查到的东西）
    tools = result.get("tool_uses") or []
    if tools:
        items = []
        for t in tools:
            if not t.get("ok"):
                continue
            text = str(t.get("output") or "").strip().replace("\n", " ")
            if not text:
                continue
            items.append(
                {
                    "statement": text[:200],
                    "source": str(t.get("name") or "工具"),
                    "confidence": 0.7,
                }
            )
        if items:
            return {"question": turn.text, "facts": items, "not_found": []}

    # 兜底二：什么也没查到
    return {
        "question": turn.text,
        "facts": [],
        "not_found": [turn.text],
        "note": "没调到工具，或者工具没给出结果",
    }


class ChatQa(RecipeScene):
    name = "chat.qa"

    # 与改造前一致：查证只拼「块」和「用户」，不拼历史。
    # 注意 recipe 里写了「历史: 产品 2.交互流」，但旧代码从来没拼过。
    # 这份声明就是 doctor 报"菜谱声明了、场景不消费"的依据；
    # 要不要真的拼历史是产品决定，先保持行为不变。
    recipe_sections = ("块", "用户")

    def build_result(self, recipe, turn, ctx, messages, result, used):
        facts = build_facts(result, turn)
        # 有结论就落库，出话场景再从 turn.facts 拿
        ctx.save_facts(turn, facts)
        turn.facts = facts
        # meta 留空：与改造前一致（qa 目前不产物块清单，要不要补是另一件事）
        return SceneResult(
            output=facts,
            calls=result.get("calls", []),
            tool_uses=result.get("tool_uses", []),
        )
