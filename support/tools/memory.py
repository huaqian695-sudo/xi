"""记忆三件：翻、记、关。

为什么先做这三个：不依赖网络，能立刻验证"工具循环"这条链通不通。
"""

from __future__ import annotations

from support.tools.base import ToolResult, ToolSpec


def _search(ctx, query: str, limit: int = 6) -> ToolResult:
    from core import repo

    hits = repo.search_memory(
        ctx.cfg, str(query), limit=int(limit), persona=ctx.persona_id,
        # 她主动查的时候不过滤最近 3 天——那是历史兜着的部分，但她要查就给
        exclude_recent_days=0,
    )
    if not hits:
        return ToolResult(True, "没有相关的记忆。", {"hits": 0})

    lines = []
    for h in hits:
        tag = h.get("tag")
        prefix = f"（{tag}）" if tag else ""
        lines.append(f"- {prefix}{h['content']}")
    return ToolResult(True, "\n".join(lines), {"hits": len(hits)})


def _add(ctx, content: str, kind: str = "fact", status: str = "observed") -> ToolResult:
    from core import repo

    text = str(content or "").strip()
    if not text:
        return ToolResult(False, "要记的内容是空的")
    if len(text) > 200:
        return ToolResult(False, "一条记忆别超过 200 字，拆成几条")

    if repo.similar_exists(ctx.cfg, text, kind=kind, persona=ctx.persona_id):
        return ToolResult(True, f"这条已经记过了：{text}", {"duplicate": True})

    mid = repo.add_memory(
        ctx.cfg,
        content=text,
        kind=kind,
        status=status,
        source="manual:tool",  # 手动记的不受"最近 3 天不查"限制
        who="她",
        persona=ctx.persona_id,
    )
    return ToolResult(True, f"记住了 #{mid}：{text}", {"memory_id": mid})


def _close(ctx, memory_id: int, why: str = "") -> ToolResult:
    from core import repo

    ok = repo.close_memory(ctx.cfg, int(memory_id), str(why or "她主动关的"), persona=ctx.persona_id)
    return ToolResult(bool(ok), f"关掉了 #{memory_id}" if ok else f"没有 #{memory_id} 这条")


TOOL = ToolSpec(
    name="memory_search",
    description="翻自己的长期记忆。记忆里没有的别编，先查。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "要查什么"},
            "limit": {"type": "integer", "description": "最多几条，默认 6"},
        },
        "required": ["query"],
    },
    toolbox="封装",
    func=_search,
)

TOOL_ADD = ToolSpec(
    name="memory_add",
    description="记住一件事。只记以后还会用到的：他的偏好、约定、重要经历。别记寒暄和天气。",
    parameters={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "一句完整的话，主语写清楚，比如「浅华不吃辣」"},
            "kind": {
                "type": "string",
                "enum": ["fact", "episode", "narrative", "inference", "knowledge", "self_action"],
                "description": "事实/经历/叙述/推断/知识/她自己做的",
            },
            "status": {
                "type": "string",
                "enum": ["verified", "observed", "subjective", "inferred", "unverified"],
                "description": "可信度，拿不准就 observed",
            },
        },
        "required": ["content"],
    },
    toolbox="封装",
    func=_add,
)

TOOL_CLOSE = ToolSpec(
    name="memory_close",
    description="关掉一条已经不对/做完的记忆。要写理由。",
    parameters={
        "type": "object",
        "properties": {
            "memory_id": {"type": "integer", "description": "记忆编号，状态卡上的 #号"},
            "why": {"type": "string", "description": "为什么关"},
        },
        "required": ["memory_id"],
    },
    toolbox="封装",
    func=_close,
)
