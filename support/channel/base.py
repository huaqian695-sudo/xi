"""通道接口。

接 QQ 是 M6 的事（support/channel/qq.py），以后接微信也是实现它。
通道只做两件事：收消息、发消息。它不认识人格，也不碰存储库。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator


@dataclass
class Incoming:
    channel: str = "stub"          # qq | wechat | stub —— 会话键的第一段
    chat_type: str = "private"     # private | group
    chat_id: str = ""              # 私聊=对方 id，群聊=群号（按字符串存，跨平台不撞）
    sub_type: str = ""             # friend | group | normal | anonymous
    user_id: str = ""              # 说话人的 id —— 身份键
    nickname: str = ""             # 这次显示的名字（快照，不是身份）
    text: str = ""
    images: list[str] = field(default_factory=list)
    msg_uid: str | None = None     # 幂等键
    at_me: bool = False            # 结构化判断，不靠字符串匹配
    reply_to: str | None = None    # 引用的消息 id
    anonymous: bool = False        # QQ 匿名发言，不是真人


class Channel:
    async def receive(self) -> AsyncIterator[Incoming]:  # pragma: no cover
        raise NotImplementedError

    async def send(self, text: str, **kwargs) -> None:  # pragma: no cover
        raise NotImplementedError
