"""从模型输出里把 JSON 挖出来。

模型不会永远听话：会包 ```json、会先寒暄两句、会多一个逗号。
这里只做一件事——尽量挖出第一个能解析的 JSON 值，挖不到就返回 None。
"""

from __future__ import annotations

import json
import re
from typing import Any

FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> Any | None:
    if not text:
        return None
    raw = text.strip()

    # 1. 直接能解析
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 2. 去掉 ```json 围栏
    for block in FENCE.findall(raw):
        try:
            return json.loads(block.strip())
        except json.JSONDecodeError:
            continue

    # 3. 抓第一个 [ 或 { ，配平括号再解析
    for opener, closer in (("[", "]"), ("{", "}")):
        start = raw.find(opener)
        if start < 0:
            continue
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(raw)):
            ch = raw[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[start : i + 1])
                    except json.JSONDecodeError:
                        break
    return None


def parse_args(raw: str | None) -> dict[str, Any]:
    """模型给的工具参数：JSON 字符串，可能带围栏、可能坏。

    放在 core 而不是工具层：这是"从模型输出里拿结构"的同一件事，
    工具层只是它的使用者。方向是 support → core，不是反过来。
    """
    if not raw:
        return {}
    text = str(raw).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = extract_json(text)
    return data if isinstance(data, dict) else {}
