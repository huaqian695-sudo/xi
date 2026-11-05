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


# 终端发来的字节不是 UTF-8 时会解成这些"代理字符"，拿它们能还原原始字节
_SURR_MIN, _SURR_MAX = 0xDC80, 0xDCFF
# 试的顺序：UTF-8 已经失败过了，所以先试最常见的两个中文编码
_FALLBACK_ENCODINGS = ("gb18030", "gbk", "big5")


def _has_raw_bytes(text: str) -> bool:
    return any(_SURR_MIN <= ord(ch) <= _SURR_MAX for ch in text)


def decode_input(text: str) -> tuple[str, str]:
    """把 `input()` 解坏的一行尽量救回来。

    返回 (文本, 状态)，状态是 ok / repaired / partial / failed：

    - ok        本来就是好的
    - repaired  终端不是 UTF-8（比如 GBK），按别的编码解回来了
    - partial   有字节解不出来（常见：末尾半个汉字），保住能解的，丢掉坏的那截
    - failed    一个字都救不回来

    为什么要"partial"：以前只要有一个坏字节就整行丢弃，
    结果**末尾少一个字节**就把整句话吞了——这是真实踩过的坑。
    """
    if not _has_raw_bytes(text):
        return text, "ok"
    try:
        raw = text.encode("utf-8", "surrogateescape")
    except UnicodeEncodeError:
        return text, "ok"

    try:
        return raw.decode("utf-8"), "ok"
    except UnicodeDecodeError as exc:
        # 只有"尾巴少了半个字"这一种情况，可以直接砍掉尾巴——
        # 不能先去试 GBK：被截断的 UTF-8 用 GBK 往往"能解"，解出来是乱码。
        if exc.reason == "unexpected end of data":
            return raw[: exc.start].decode("utf-8", "ignore").strip(), "partial"

    for encoding in _FALLBACK_ENCODINGS:
        try:
            return raw.decode(encoding), "repaired"
        except UnicodeDecodeError:
            continue

    # 都不行：保留能解的部分，把解不出的字符去掉（而不是整行丢掉）
    salvaged = raw.decode("utf-8", "replace").replace("\ufffd", "").strip()
    return (salvaged, "partial") if salvaged else ("", "failed")


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
        self._warned_encoding = False

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

            text, status = decode_input(str(line))
            if "\ufffd" in text and status == "ok":
                # 粘贴进来的内容里本来就有替换字符：去掉它，别塞进 prompt
                text = text.replace("\ufffd", "")
                status = "partial"
            text = text.strip()
            if status == "failed" or not text:
                if status == "failed":
                    # 一个字都救不回来：打字节，不然只能靠猜
                    raw = str(line).encode("utf-8", "surrogateescape")
                    print(f"（这条输入解不出来：{len(raw)} 字节")
                    print(f"  原始字节：{raw[:60].hex(' ')}" + (" …" if len(raw) > 60 else ""))
                    print("  把这两行发出来就能定位是哪种编码）")
                continue
            if status == "repaired" and not self._warned_encoding:
                self._warned_encoding = True
                print("（你的终端不是 UTF-8，这条已按其他编码解出来。以后不提示了）")
            elif status == "partial":
                print("（这句末尾有半个字解不出来，已经去掉了，其余照常发出去）")
            if text in EXIT_WORDS:
                return
            if text in HELP_WORDS:
                print(HELP, end="")
                continue
            if text == "/as" or text.startswith("/as "):
                self._switch(text)
                continue

            images: list[str] = []
            if text == "#图" or text.startswith("#图 "):
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
