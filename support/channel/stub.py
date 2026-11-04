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
            except UnicodeDecodeError:
                print("（这条输入里有乱码字节，跳过了，重输一下）")
                continue
            text = str(text).strip()
            if not text:
                continue
            if "\ufffd" in text:
                # 非法字节被替换成了 �，这条不要了——别把乱码记进账本
                print("（这条输入里有乱码字节，跳过了，重输一下）")
                continue
            if text in EXIT_WORDS:
                return
            images: list[str] = []
            if text.startswith("#图"):
                text = text[2:].strip()
                images = ["(假图)"]
            yield Incoming(
                channel="stub",
                chat_type="private",
                chat_id=str(self.admin_id),
                sub_type="friend",
                user_id=str(self.admin_id),
                nickname=self.nickname,
                text=text,
                images=images,
                at_me=True,  # 私聊等于一直在跟她说
            )

    async def send(self, text: str, **kwargs) -> None:
        print(f"\n汐> {text}\n")

    async def send_file(self, path: str, **kwargs) -> None:
        print(f"\n[文件] {path}\n")
