"""入口：读配置 → 校验菜谱 → 开账本 → 加载场景 → 起通道 → 一条消息走完一圈。

M1：消息走前置链（标签 → 查证 → 出话），全程留痕，prompt 全文进 runs.jsonl。
真模型的接入在 M3，真 QQ 在 M6。
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

from core import chain, clock, db, organize, repo
from support.channel.bindings import Bindings
from pipeline import extractor, weather
from core.config import Config, ProdStoreRefused, guard_production_store
from core.rawlog import RawLog
from core.validate import validate, validate_all_recipes
from scenes.base import SceneContext, Turn, load_scenes
from support.channel.stub import StubChannel
from support.mock_llm import MockEngine

ROOT = Path(__file__).resolve().parent


def _make_io_forgiving() -> None:
    """终端里偶尔会收到非法字节（粘贴、输入法、远程终端）。

    默认 utf-8/strict 会直接抛 UnicodeDecodeError，把整个进程带走——
    一条脏输入不该让服务挂。改成替换字符，继续跑。
    """
    for stream in (sys.stdin, sys.stdout):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001 - 不是所有流都支持
            pass


def _make_turn(incoming, ctx) -> Turn:
    """纯构造。这里出错是代码 bug，应该崩给我看，不要接。

    注意：这里只看 `Incoming` 已经翻译好的 `person_id`——平台、外号、
    绑定关系都在通讯层，核心不该知道。
    """
    return Turn(
        turn_id=clock.new_turn_id(),
        persona_id="xi",
        scene="",
        person_id=int(incoming.person_id),
        person_name=incoming.person_name,
        is_owner=bool(incoming.is_owner),
        conversation=incoming.conversation or "default",
        text=incoming.text,
        images=list(incoming.images),
        files=list(incoming.files),
        trigger="message",
        msg_uid=incoming.msg_uid,
        at_me=incoming.at_me,
        reply_to=incoming.reply_to,
    )


def _notice(raw: RawLog, what: str, exc: Exception) -> None:
    """记一条异常。留痕比沉默重要——藏起来的 bug 比崩掉的更危险。"""
    raw.append(
        "events",
        {"ts": clock.iso(), "kind": "error", "detail": f"{what}：{type(exc).__name__}: {exc}"},
    )


def _touch_person(cfg, turn: Turn, incoming, ctx, raw: RawLog) -> None:
    """见到一个人：更新档案的最后出现时间和名字。"""
    if not turn.person_id:
        return
    try:
        repo.upsert_person(
            cfg,
            person_id=turn.person_id,
            name=turn.person_name or None,
            is_owner=turn.is_owner,
        )
    except Exception as exc:  # noqa: BLE001 - 记人失败不挡回复
        _notice(raw, "记人失败", exc)


async def _think(turn: Turn, scenes, ctx, raw: RawLog):
    """跑前置链。这里失败要兜住——回复不能因为模型挂了就没有。"""
    try:
        return await chain.run(turn, scenes, ctx), None
    except Exception as exc:  # noqa: BLE001
        _notice(raw, "前置链失败", exc)
        return None, exc


def _output_text(result, error) -> str:
    if error is not None or result is None:
        return "（我这边出了点岔子，等下再说）"
    reply = result.reply
    return reply.output if isinstance(reply.output, str) else str(reply.output)


def _usage_summary(calls) -> dict:
    """整轮的 token 汇总。

    以前只取 calls[0] 的 in —— 工具循环跑三四轮时，那数字是错的。
    `tokens` 保留旧键名，值改成整轮输入合计。
    """
    usage = {"in": 0, "out": 0, "total": 0}
    for call in calls:
        got = call.get("usage") or {}
        for key in ("in", "out", "total"):
            usage[key] += int(got.get(key) or 0)
    if not usage["total"]:
        # 有的客户端（和假模型）只给 in/out，没有 total
        usage["total"] = usage["in"] + usage["out"]
    return {"tokens": usage["in"], "usage": usage, "calls": len(calls)}


def _record(cfg, turn: Turn, result, output: str, error, raw: RawLog,
            *, duration_ms: int | None = None) -> None:
    """记账：写 raw，再从它拆出对话稿。

    这一段全是外围动作——失败了顶多这一轮没记上，绝不该影响她回话。
    但必须留痕，不然"记账一直失败"会被当成一切正常。
    """
    try:
        calls = list(result.calls) if result else []
        facts = (result.facts if result else None) or {}
        output_text = output

        record = {
            "turn_id": turn.turn_id,
            "ts": clock.iso(),
            "persona_id": turn.persona_id,
            "scene_chain": result.chain if result else [],
            "person_id": turn.person_id,
            "person_name": turn.person_name,
            "conversation": turn.conversation,
            "at_me": turn.at_me,
            "reply_to": turn.reply_to,
            "session_no": turn.session_no,
            "trigger": turn.trigger,
            "msg_uid": turn.msg_uid,
            "text": turn.text,
            "image_refs": turn.images,
            "files": turn.files,
            "label": {
                "need_facts": getattr(result.label, "need_facts", None) if result else None,
                "emotion_cue": getattr(result.label, "emotion_cue", None) if result else None,
                "source": getattr(result.label, "source", None) if result else None,
            },
            "facts": {
                "count": len(facts.get("facts") or []),
                "not_found": facts.get("not_found") or [],
            },
            "blocks": result.blocks if result else [],
            "tools": [
                {"name": t.get("name"), "ok": t.get("ok", True), "output": str(t.get("output", ""))[:200]}
                for t in ((result.tool_uses if result else []) or [])
            ],
            "reply": output_text,
            "delivery": {
                "status": "未发送" if error is not None else "已发送",
                "reason": str(error) if error is not None else None,
            },
            "result": _usage_summary(calls),
            "duration_ms": duration_ms,
        }

        raw.append("turns", record)
        for idx, call in enumerate(calls, start=1):
            raw.append(
                "runs",
                {
                    "run_id": f"{turn.turn_id}-r{idx}",
                    "turn_id": turn.turn_id,
                    "ts": clock.iso(),
                    "scene": call.get("scene", ""),
                    "tier": call.get("tier"),
                    "provider": call.get("provider"),
                    "model": call.get("model"),
                    "duration_ms": call.get("duration_ms"),
                    "prompt": call.get("prompt"),
                    "output": call.get("output"),
                    # 以前记账时把这些挑掉了，复盘就查不到"它为什么这么答"
                    "reasoning": call.get("reasoning"),
                    "finish": call.get("finish"),
                    "note": call.get("note"),
                    "tool_calls": call.get("tool_calls", 0),
                    "usage": call.get("usage"),
                    "ok": call.get("ok", True),
                },
            )

        # 她动了什么手
        for idx, tool in enumerate((result.tool_uses if result else []) or [], start=1):
            raw.append(
                "tools",
                {
                    "call_id": f"{turn.turn_id}-t{idx}",
                    "turn_id": turn.turn_id,
                    "ts": clock.iso(),
                    "tool": tool.get("name"),
                    "args": tool.get("args"),
                    "ok": tool.get("ok", True),
                    "duration_ms": tool.get("duration_ms"),
                    "output": str(tool.get("output", ""))[:2000],
                },
            )

        # raw 是真相，interactions 是它的对话稿：写完就拆
        organize.from_turn(cfg, record, raw=raw)
    except Exception as exc:  # noqa: BLE001 - 记账失败不挡回复
        _notice(raw, "记账失败", exc)


async def handle(incoming, scenes, ctx, raw: RawLog) -> str:
    """一条消息走完。四步，各自管好自己的失败范围。"""
    cfg = ctx.cfg
    started = time.perf_counter()
    turn = _make_turn(incoming, ctx)
    _touch_person(cfg, turn, incoming, ctx, raw)

    result, error = await _think(turn, scenes, ctx, raw)
    output = _output_text(result, error)
    duration_ms = int((time.perf_counter() - started) * 1000)
    _record(cfg, turn, result, output, error, raw, duration_ms=duration_ms)
    return output


async def main() -> int:
    _make_io_forgiving()
    cfg = Config(ROOT)
    try:
        guard_production_store(cfg)
    except ProdStoreRefused as exc:
        print(f"拒绝启动：{exc}", file=sys.stderr)
        return 1
    scenes = load_scenes(ROOT / "scenes")
    if not scenes:
        print("一个场景都没有加载到，先检查 scenes/ 目录。", file=sys.stderr)
        return 1

    for warning in (p for p in validate_all_recipes(cfg, ROOT / "scenes") if "（未注册）" in p):
        print(f"警告：{warning}", file=sys.stderr)

    problems = validate(cfg, ROOT / "scenes", scenes)
    if problems:
        print(f"菜谱校验没过，拒绝启动（{len(problems)} 条）：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    raw = RawLog(cfg.store_root())
    raw.ensure_day()

    use_mock = bool((cfg.core.get("llm") or {}).get("use_mock", False))
    env_mock = os.environ.get("XI_USE_MOCK")
    if env_mock is not None:
        use_mock = env_mock.strip().lower() not in ("0", "false", "no", "")
    db.ensure_all(cfg)
    bindings = Bindings(cfg, persona="xi")
    channel = StubChannel(cfg.admin_id("xi"), bindings=bindings)

    if use_mock:
        engine = MockEngine()
    else:
        from core.engine import Engine
        from support.tools.base import execute as tool_executor

        # 组装根显式接线：core 自己不需要知道 support 的存在
        engine = Engine(
            cfg, persona="xi", channel=channel, tool_executor=tool_executor
        )

    ctx = SceneContext(
        cfg=cfg,
        scenes_dir=ROOT / "scenes",
        rawlog=raw,
        engine=engine,
        persona_id="xi",
    )

    raw.append(
        "events",
        {
            "ts": clock.iso(),
            "kind": "connect",
            "detail": {
                "channel": "stub",
                "scenes": sorted(scenes),
                "recipes_ok": True,
                "engine": "mock" if use_mock else "real",
            },
        },
    )

    print(f"汐（{'假' if use_mock else '真'}模型）—— 说点什么，回车发送。输入 /quit 退出。")
    print("带图的话用 #图 开头，例如：#图 今天的晚霞")
    print(f"已加载场景：{', '.join(sorted(scenes))}")
    print(f"今天的账本：{cfg.store_root() / 'raw' / clock.day_dir()}")
    print("-" * 48)

    daemon_cfg = cfg.core.get("daemon") or {}
    weather_loop = None
    if daemon_cfg.get("weather_daily", True):
        weather_loop = asyncio.create_task(
            weather.daily_loop(
                cfg,
                at=str(daemon_cfg.get("weather_at") or "07:00"),
                cities=list(daemon_cfg.get("weather_cities") or []),
                persona="xi",
                raw=raw,
            )
        )

    extract_loop = None
    if daemon_cfg.get("extract_daily", True):
        extract_loop = asyncio.create_task(
            extractor.daily_loop(
                cfg,
                ctx,
                scenes,
                raw=raw,
                at=str(daemon_cfg.get("extract_at") or "03:00"),
                batch=int(daemon_cfg.get("extract_batch") or 60),
                persona="xi",
            )
        )

    try:
        async for incoming in channel.receive():
            reply = await handle(incoming, scenes, ctx, raw)
            await channel.send(reply)
    finally:
        for task in (extract_loop, weather_loop):
            if task is not None:
                task.cancel()

    raw.append("events", {"ts": clock.iso(), "kind": "disconnect", "detail": {"channel": "stub"}})
    raw.flush()
    print("\n下线了。今天的账本还在。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
