"""发文件：长内容写成 md 发出去，正文只留一句结论。

为什么要有：QQ 一条最多 600 字，超了就刷屏、还容易触发风控。
报告、方案、清单这类东西，写成文件比刷十条消息强。

安全：只写到项目下的 `out/`，文件名不许带路径——否则等于给了"读任意文件再外发"的口子。
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from support.tools.base import ToolResult, ToolSpec

SAFE_NAME = re.compile(r"^[\w\u4e00-\u9fff][\w\u4e00-\u9fff.\-]{0,60}$")


def _send(ctx, content: str, name: str = "report") -> ToolResult:
    text = str(content or "").strip()
    if not text:
        return ToolResult(False, "内容是空的，没东西可发")
    if len(text) < 80:
        return ToolResult(
            False, "这么短直接说出来就行，不用发文件（少于 80 字）"
        )

    # 文件名只留字母数字中文和点横线，路径一律不认
    raw_name = str(name or "report").strip()
    raw_name = raw_name[:-3] if raw_name.lower().endswith(".md") else raw_name
    if not SAFE_NAME.match(raw_name):
        raw_name = re.sub(r"[^\w\u4e00-\u9fff.\-]", "_", raw_name)[:40] or "report"

    out_dir = Path(ctx.cfg.root) / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{time.strftime('%m%d-%H%M')}-{raw_name}.md"
    path = (out_dir / filename).resolve()

    # 双保险：解析后的路径必须在 out/ 里面
    if out_dir.resolve() not in path.parents:
        return ToolResult(False, "这个文件名不让用")

    path.write_text(text, encoding="utf-8")

    sent = False
    channel = getattr(ctx, "channel", None)
    if channel is not None and hasattr(channel, "send_file"):
        try:
            import asyncio

            turn = getattr(ctx, "turn", None)
            coro = channel.send_file(
                str(path),
                chat_type=getattr(turn, "chat_type", "private"),
                chat_id=getattr(turn, "chat_id", ""),
            )
            if asyncio.iscoroutine(coro):
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    asyncio.run(coro)
                else:
                    asyncio.ensure_future(coro)
            sent = True
        except Exception as exc:  # noqa: BLE001 - 发送失败也别丢文件
            return ToolResult(
                True,
                f"文件写好了：{path}（但发送没成功：{type(exc).__name__}）",
                {"path": str(path), "sent": False},
            )

    where = "发出去了" if sent else f"写好了（终端模式，文件在 {path}）"
    return ToolResult(
        True,
        f"文件{where}：{filename}，{len(text)} 字。正文里说一句结论加文件名就行。",
        {"path": str(path), "sent": sent, "chars": len(text)},
    )


TOOL = ToolSpec(
    name="send_file",
    description=(
        "把长内容写成 md 文件发过去。超过 120 字、或者要点超过 3 条的报告/方案/清单，"
        "用它；正文只留一句结论加文件名。短回复别用。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "完整正文（markdown）"},
            "name": {"type": "string", "description": "文件名，不带扩展名，比如 天气报告"},
        },
        "required": ["content"],
    },
    toolbox="封装",
    func=_send,
)
