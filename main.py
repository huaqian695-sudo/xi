"""入口：读配置 → 校验菜谱 → 开账本 → 加载场景 → 起通道 → 一条消息走完一圈。

M1：消息走前置链（标签 → 查证 → 出话），全程留痕，prompt 全文进 runs.jsonl。
真模型的接入在 M3，真 QQ 在 M6。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from core import chain, clock
from core.config import Config
from core.rawlog import RawLog
from core.validate import validate
from scenes.base import SceneContext, Turn, load_scenes
from support.channel.stub import StubChannel
from support.mock_llm import MockEngine

ROOT = Path(__file__).resolve().parent


async def handle(incoming, scenes, ctx, raw: RawLog) -> str:
    """一条消息走完：建 turn → 前置链 → 记账 → 返回要发的话。"""
    turn = Turn(
        turn_id=clock.new_turn_id(),
        persona_id="xi",
        scene="",
        chat_type=incoming.chat_type,
        chat_id=incoming.chat_id,
        user_id=incoming.user_id,
        nickname=incoming.nickname,
        text=incoming.text,
        images=list(incoming.images),
        trigger="message",
        msg_uid=incoming.msg_uid,
    )

    started = clock.now()
    try:
        result = await chain.run(turn, scenes, ctx)
    except Exception as exc:  # noqa: BLE001 - 单轮失败不能拖垮进程
        raw.append(
            "events",
            {"ts": clock.iso(), "kind": "error", "detail": f"{type(exc).__name__}: {exc}"},
        )
        return "（我这边出了点岔子，等下再说）"
    duration_ms = int((clock.now() - started).total_seconds() * 1000)

    reply = result.reply
    calls = list(result.calls)
    tool_uses = list(result.tool_uses)

    # ---- 做菜记录（含 prompt 全文） --------------------------------
    for idx, call in enumerate(calls, start=1):
        raw.append(
            "runs",
            {
                "run_id": f"{turn.turn_id}-r{idx}",
                "turn_id": turn.turn_id,
                "scene": call.get("scene", turn.scene),
                "tier": call.get("tier"),
                "provider": "mock",
                "model": call.get("model"),
                "duration_ms": duration_ms,
                "prompt": call.get("prompt"),
                "output": call.get("output"),
                "usage": call.get("usage"),
                "ok": call.get("ok", True),
            },
        )

    # ---- 厨具记录 --------------------------------------------------
    for idx, tool in enumerate(tool_uses, start=1):
        raw.append(
            "tools",
            {
                "call_id": f"{turn.turn_id}-t{idx}",
                "turn_id": turn.turn_id,
                "ts": clock.iso(),
                "tool": tool.get("name"),
                "args": tool.get("args"),
                "ok": tool.get("ok", True),
                "output": str(tool.get("output", ""))[:2000],
            },
        )

    # ---- 流水账 ----------------------------------------------------
    output_text = reply.output if isinstance(reply.output, str) else str(reply.output)
    facts = result.facts or {}
    raw.append(
        "turns",
        {
            "turn_id": turn.turn_id,
            "ts": clock.iso(),
            "persona_id": turn.persona_id,
            "scene_chain": result.chain,
            "chat_type": turn.chat_type,
            "chat_id": turn.chat_id,
            "session_no": turn.session_no,
            "trigger": turn.trigger,
            "msg_uid": turn.msg_uid,
            "participants": [
                {"user_id": turn.user_id, "nickname": turn.nickname, "text": turn.text}
            ],
            "text": turn.text,
            "image_refs": turn.images,
            "label": {
                "need_facts": result.label.need_facts,
                "emotion_cue": result.label.emotion_cue,
                "source": result.label.source,
            },
            "facts": {
                "count": len(facts.get("facts") or []),
                "not_found": facts.get("not_found") or [],
            },
            "blocks": result.blocks,
            "reply": output_text,
            "delivery": {"status": "已发送", "reason": None},
            "result": {"tokens": (calls[0].get("usage") or {}).get("in") if calls else 0},
            "duration_ms": duration_ms,
        },
    )

    return output_text


async def main() -> int:
    cfg = Config(ROOT)
    scenes = load_scenes(ROOT / "scenes")
    if not scenes:
        print("一个场景都没有加载到，先检查 scenes/ 目录。", file=sys.stderr)
        return 1

    problems = validate(cfg, ROOT / "scenes", scenes)
    if problems:
        print(f"菜谱校验没过，拒绝启动（{len(problems)} 条）：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    raw = RawLog(cfg.store_root())
    raw.ensure_day()

    engine = MockEngine()
    ctx = SceneContext(
        cfg=cfg,
        scenes_dir=ROOT / "scenes",
        rawlog=raw,
        engine=engine,
        persona_id="xi",
    )

    channel = StubChannel(cfg.admin_id("xi"))

    raw.append(
        "events",
        {
            "ts": clock.iso(),
            "kind": "connect",
            "detail": {"channel": "stub", "scenes": sorted(scenes), "recipes_ok": True},
        },
    )

    print("汐（M1 假模型）—— 说点什么，回车发送。输入 /quit 退出。")
    print("带图的话用 #图 开头，例如：#图 今天的晚霞")
    print(f"已加载场景：{', '.join(sorted(scenes))}")
    print(f"今天的账本：{cfg.store_root() / 'raw' / clock.day_dir()}")
    print("-" * 48)

    async for incoming in channel.receive():
        reply = await handle(incoming, scenes, ctx, raw)
        await channel.send(reply)

    raw.append("events", {"ts": clock.iso(), "kind": "disconnect", "detail": {"channel": "stub"}})
    raw.flush()
    print("\n下线了。今天的账本还在。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
