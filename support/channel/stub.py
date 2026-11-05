"""终端前台：在终端里直接跟她聊天，用来测链路。

不是 QQ，是本地假前台——一个人、一个对话，输入即消息。
接真 QQ 是 M6 的事（`qq.py`），上层一行不用改。

命令：
    /help            看这些命令
    /quit            退出（也可以用 /exit、/退出、Ctrl-D）
    #图 今天的晚霞    模拟一条带图消息（图片是占位字符串，不是真像素）
    /as 2 张三        切换成"另一个人"说话，测陌生人分支；/as 1 回到主人
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from .base import Channel, Incoming

EXIT_WORDS = {"/quit", "/exit", "/退出"}
HELP_WORDS = {"/help", "/?", "/帮助"}

HELP = """\
  /help          看这些命令
  /quit          退出
  #图 文字        模拟一条带图消息
  /as 2 张三      切换成另一个人说话（测陌生人分支）
  /as 1          切回主人
"""


class StubChannel(Channel):
    def __init__(
        self,
        admin_id: int,
        bindings=None,
        nickname: str = "浅华",
        prompt: str = "你> ",
    ):
        self.admin_id = admin_id
        self.bindings = bindings
        self.nickname = nickname
        self.prompt = prompt
        self._as: tuple[int, str] | None = None

    # ---- 说话人 ---------------------------------------------------
    def _person(self) -> dict:
        if self._as is not None:
            pid, name = self._as
            return {"person_id": pid, "name": name, "is_owner": pid == 1}
        if self.bindings:
            return self.bindings.resolve("stub", str(self.admin_id), self.nickname)
        return {"person_id": 1, "name": self.nickname, "is_owner": True}

    def _switch(self, text: str) -> bool:
        """`/as 2 张三` 切换；返回是否处理成功。"""
        parts = text.split(maxsplit=2)
        if len(parts) < 2:
            print("用法：/as 2 张三（/as 1 回到主人）")
            return False
        try:
            pid = int(parts[1])
        except ValueError:
            print("id 要是数字，例如 /as 2 张三")
            return False
        name = parts[2].strip() if len(parts) > 2 else f"{pid}号"
        self._as = None if pid == 1 else (pid, name)
        who = "主人" if pid == 1 else f"{name}（{pid} 号）"
        print(f"（接下来以「{who}」的身份说话）")
        return True

    # ---- 收 -------------------------------------------------------
    async def receive(self) -> AsyncIterator[Incoming]:
        while True:
            try:
                line = await asyncio.to_thread(input, self.prompt)
            except (EOFError, KeyboardInterrupt):
                return
            except UnicodeDecodeError:
                print("（这条输入里有乱码字节，跳过了，重输一下）")
                continue

            text = str(line).strip()
            if not text:
                continue
            if "\ufffd" in text:
                # 非法字节被替换成了 �，这条不要了——别把乱码记进账本
                print("（这条输入里有乱码字节，跳过了，重输一下）")
                continue
            if text in EXIT_WORDS:
                return
            if text in HELP_WORDS:
                print(HELP, end="")
                continue
            if text.startswith("/as"):
                self._switch(text)
                continue

            images: list[str] = []
            if text.startswith("#图"):
                text = text[2:].strip()
                images = ["(假图)"]

            person = self._person()
            pid = int(person["person_id"])
            yield Incoming(
                person_id=pid,
                person_name=str(person["name"]),
                is_owner=bool(person["is_owner"]),
                # 每个人一个对话：测多人的时候历史不会串
                conversation="direct:owner" if pid == 1 else f"direct:{pid}",
                text=text,
                images=images,
                at_me=True,
            )

    # ---- 发 -------------------------------------------------------
    async def send(self, text: str, **kwargs) -> None:
        print(f"\n汐> {text}\n")

    async def send_file(self, path: str, **kwargs) -> None:
        print(f"\n[文件] {path}\n")
