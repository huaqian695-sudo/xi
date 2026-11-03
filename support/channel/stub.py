"""假前台：终端里打字当成 QQ 消息。

M0 用它验证链路。接真 QQ 是 M6，届时换成 qq.py，上层一行不用改。
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from .base import Channel, Incoming

EXIT_WORDS = {"/quit", "/exit", "/退出"}


class StubChannel(Channel):
    def __init__(self, admin_id: int, nickname: str = "浅华"):
        self.admin_id = admin_id
        self.nickname = nickname

    async def receive(self) -> AsyncIterator[Incoming]:
        while True:
            try:
                text = await asyncio.to_thread(input, "")
            except (EOFError, KeyboardInterrupt):
                return
            text = str(text).strip()
            if not text:
                continue
            if text in EXIT_WORDS:
                return
            yield Incoming(
                chat_type="private",
                chat_id=self.admin_id,
                user_id=self.admin_id,
                nickname=self.nickname,
                text=text,
            )

    async def send(self, text: str, **kwargs) -> None:
        print(f"\n汐> {text}\n")
