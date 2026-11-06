"""跑一次心跳：叫 tick 场景 → 校验 → 落盘。

分工很清楚：

    scenes/tick/    只说一次话（拼 prompt、发模型、留档）
    这里            这句话意味着什么（解析、夹值、写三样、决定说不说）
    core/guards.py  能不能说（静默 / 上限 / 间隔）

**校验不信模型**：它可能少个字段、把情绪写成 5.0、或者干脆吐一段散文。
坏数据不许污染状态——最差情况只把原话记进内心，别的都不做。
"""

from __future__ import annotations

from typing import Any

from core import clock, guards, state
from core.parsing import extract_json
from scenes.base import Turn


def _turn(persona: str, moment) -> Turn:
    # 心跳没有"对方"，也没有对话轮次；conversation 用一个不冲突的值
    return Turn(
        turn_id=clock.new_turn_id(moment),
        persona_id=persona,
        scene="tick",
        trigger="tick",
        conversation="self",
        text="",
    )


async def run_once(
    cfg,
    ctx,
    scenes: dict[str, Any],
    raw=None,
    persona: str = "xi",
    moment=None,
) -> dict[str, Any]:
    """醒一次。返回这一轮发生了什么（给测试和日志用）。"""
    moment = moment or clock.now()
    scene = scenes.get("tick")
    if scene is None:
        return {"ok": False, "why": "没有 tick 场景"}

    result = await scene.run(_turn(persona, moment), ctx)
    output = result.output if isinstance(result.output, str) else str(result.output or "")
    parsed = extract_json(output)

    # 用了工具就要留痕：她亲自动手这件事，事后要翻得到
    tools = [
        str(t.get("name") or "")
        for t in (result.tool_uses or [])
        if str(t.get("name") or "").strip()
    ]
    for name in dict.fromkeys(tools):  # 去重但保序
        state.append_journal(cfg, f"动手：{name}", persona=persona, ts=moment)

    error = str((result.meta or {}).get("error") or "").strip()
    if error:
        # 根本没调通（超时、限流、网关挂了）：不算"她想不出东西"，别写内心
        short = error[:120]
        state.append_journal(cfg, f"心跳失败：{short}", persona=persona, ts=moment)
        if raw is not None:
            raw.append(
                "events",
                {
                    "ts": clock.iso(moment),
                    "kind": "tick",
                    "detail": {"ok": False, "why": "调用失败", "error": short, "tools": tools},
                },
            )
        return {"ok": False, "why": "调用失败", "error": error, "tools": tools}

    if not isinstance(parsed, dict) or not str(parsed.get("inner") or "").strip():
        # 解析失败：原话照记，但不动状态、不说话
        state.append_inner(cfg, output or "（这一轮什么也没想出来）", persona=persona, ts=moment)
        state.append_journal(cfg, "心跳：没想清楚，只记了原话", persona=persona, ts=moment)
        if raw is not None:
            raw.append(
                "events",
                {
                    "ts": clock.iso(moment),
                    "kind": "tick",
                    "detail": {"ok": False, "why": "解析失败或缺少 inner", "tools": tools},
                },
            )
        return {"ok": False, "why": "解析失败", "tools": tools}

    inner = str(parsed["inner"]).strip()

    # 情绪/精力：都是"变化量"，单次最多 ±0.3，夹在 0..1
    current = state.read_state(cfg, persona)
    current = state.apply_emotion(current, parsed.get("emotion"))
    current = state.apply_energy(current, parsed.get("energy"))
    state.write_state(cfg, current, persona)

    state.append_inner(cfg, inner, persona=persona, ts=moment)
    state.append_journal(cfg, f"心跳：{inner}", persona=persona, ts=moment)

    say = str(parsed.get("say") or "").strip()
    wants = bool(parsed.get("speak")) and bool(say)
    allowed, why = guards.can_speak(cfg, persona, moment) if wants else (False, "她没想说话")

    if raw is not None:
        raw.append(
            "events",
            {
                "ts": clock.iso(moment),
                "kind": "tick",
                "detail": {
                    "ok": True,
                    "inner": inner,
                    "speak": wants,
                    "speak_allowed": allowed,
                    "speak_blocked_by": None if allowed else why,
                    "tools": tools,
                },
            },
        )

    return {
        "ok": True,
        "inner": inner,
        "say": say,
        "speak": wants,
        "allowed": allowed,
        "blocked_by": None if allowed else why,
        "tools": tools,
    }
