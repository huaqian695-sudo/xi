"""测试用的假引擎：按场景返回不同的假结果，不联网、不花钱。

为什么不用 MockEngine：M2 的 label 和提炼都要能解析 JSON，
MockEngine 回的是人话，永远解析失败，等于没测到解析分支。
"""

from __future__ import annotations

import json
from typing import Any


class FakeEngine:
    def __init__(
        self,
        *,
        label: dict[str, Any] | None = None,
        extraction: list[dict[str, Any]] | None = None,
        reply: str = "嗯，我在。",
        label_text: str | None = None,
        raise_on: str | None = None,
    ):
        self.label = label or {
            "need_facts": "no",
            "registers": ["闲聊"],
            "topics": ["测试"],
            "emotion_cue": "平静",
            "suggested_tools": [],
            "has_image": False,
            "confidence": 0.9,
        }
        self.extraction = extraction if extraction is not None else []
        self.reply = reply
        self.label_text = label_text
        self.raise_on = raise_on
        self.calls: list[str] = []

    async def run(self, messages, *, tier, toolbox, budget, turn, scene=""):
        self.calls.append(scene)
        if self.raise_on == scene:
            raise RuntimeError(f"{scene} 炸了")

        if scene == "label":
            output = self.label_text if self.label_text is not None else json.dumps(
                self.label, ensure_ascii=False
            )
        elif scene == "memory.extract":
            output = json.dumps(self.extraction, ensure_ascii=False)
        else:
            output = self.reply

        return {
            "output": output,
            "calls": [
                {
                    "tier": tier,
                    "model": "fake",
                    "provider": "fake",
                    "scene": scene,
                    "prompt": messages,
                    "output": output,
                    "usage": {"in": 10, "out": 5},
                    "ok": True,
                }
            ],
            "tool_uses": [],
        }
