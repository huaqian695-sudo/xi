"""通道接口。

接 QQ 是 M6 的事（support/channel/qq.py），以后接微信也是实现它。
通道只做两件事：收消息、发消息。它不认识人格，也不碰存储库。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator


@dataclass
class Incoming:
    chat_type: str = "private"  # private | group
    chat_id: int = 0
    user_id: int = 0
    nickname: str = ""
    text: str = ""
    images: list[str] = field(default_factory=list)
    msg_uid: int | None = None


class Channel:
    async def receive(self) -> AsyncIterator[Incoming]:  # pragma: no cover
        raise NotImplementedError

    async def send(self, text: str, **kwargs) -> None:  # pragma: no cover
        raise NotImplementedError
