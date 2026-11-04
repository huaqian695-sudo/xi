"""通道接口。

接 QQ 是 M6 的事（support/channel/qq.py），以后接微信也是实现它。
通道只做两件事：收消息、发消息。它不认识人格，也不碰存储库。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator


@dataclass
class Incoming:
    """通道交出来的东西——**已经翻译成核心的语言**。

    平台、外部账号、绑定关系都在通道内部解决了，这里只剩：
    谁（person_id）、在哪个对话（conversation）、说了什么、带了什么。
    """

    person_id: int = 1
    person_name: str = ""
    is_owner: bool = False
    conversation: str = "default"     # 通讯层自己拼的不透明字符串
    text: str = ""
    images: list[str] = field(default_factory=list)      # 图片本地路径
    files: list[dict] = field(default_factory=list)      # [{name,path,size}]
    msg_uid: str | None = None        # 幂等键
    at_me: bool = False
    reply_to: str | None = None


class Channel:
    async def receive(self) -> AsyncIterator[Incoming]:  # pragma: no cover
        raise NotImplementedError

    async def send(self, text: str, **kwargs) -> None:  # pragma: no cover
        raise NotImplementedError
