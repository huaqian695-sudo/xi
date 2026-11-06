"""心跳：她自己醒着的那一刻。

跟 chat.reply 一样，流程全在 `RecipeScene` 里——取块、拼 messages、发模型、
留档。这里不写 run，是因为心跳没有特殊流程：它只要模型回一段 JSON，
解析和落盘在 `pipeline/heartbeat.py`。

这里留空是有意的：场景只负责"说一次话"，不负责"这句话意味着什么"。
"""

from __future__ import annotations

from scenes.base import RecipeScene


class Tick(RecipeScene):
    name = "tick"
