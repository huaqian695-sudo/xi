"""出话：纯文字消息。

四步：读菜谱 → 取块 → 拼 messages → 交给引擎。
场景不直接调 LLM、不自己发消息、不写原始层、不调用别的场景。

流程在 `RecipeScene` 里，这里只声明"我是哪个场景"。
"""

from __future__ import annotations

from scenes.base import RecipeScene


class ChatReply(RecipeScene):
    name = "chat.reply"
