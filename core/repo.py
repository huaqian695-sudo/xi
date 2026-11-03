"""数据访问：交互流、事实块、记忆。

每个方法自己开连接、自己关。M2 的量级下这样最省事，
以后真要性能再换连接池。
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any, Iterator

from . import clock, db

TZ = timezone(timedelta(hours=8))


@contextmanager
def conn(cfg, kind: str, persona: str = "xi") -> Iterator[sqlite3.Connection]:
    c = db.connect(db.db_path(cfg, kind, persona))
    try:
        yield c
        c.commit()
    finally:
        c.close()


# ---------------------------------------------------------------- 交互流


def add_interaction(
    cfg,
    *,
    turn_id: str,
    actor: str,
    kind: str,
    content: str,
    chat_type: str = "private",
    chat_id: int = 0,
    session_no: int = 1,
    user_id: int | None = None,
    nickname: str | None = None,
    ref: str | None = None,
    image_refs: list[str] | None = None,
    delivery: str | None = None,
    ts: str | None = None,
    persona: str = "xi",
) -> int:
    with conn(cfg, "interactions", persona) as c:
        cur = c.execute(
            """INSERT INTO interactions
               (turn_id, ts, chat_type, chat_id, session_no, actor, kind,
                user_id, nickname, content, ref, image_refs, delivery)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                turn_id,
                ts or clock.iso(),
                chat_type,
                chat_id,
                session_no,
                actor,
                kind,
                user_id,
                nickname,
                content,
                ref,
                json.dumps(image_refs or [], ensure_ascii=False),
                delivery,
            ),
        )
        return int(cur.lastrowid or 0)


def recent_messages(
    cfg,
    *,
    chat_id: int,
    session_no: int = 1,
    limit: int = 100,
    before_id: int | None = None,
    persona: str = "xi",
) -> list[dict[str, Any]]:
    """当前会话段最近的对话，按时间正序。"""
    sql = """SELECT * FROM interactions
             WHERE chat_id=? AND session_no=? AND kind='消息'"""
    args: list[Any] = [chat_id, session_no]
    if before_id is not None:
        sql += " AND id < ?"
        args.append(before_id)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)

    with conn(cfg, "interactions", persona) as c:
        rows = c.execute(sql, args).fetchall()
    return [dict(r) for r in reversed(rows)]


def since_id(cfg, last_id: int, persona: str = "xi", limit: int = 200) -> list[dict[str, Any]]:
    """游标之后的所有交互（提炼器用）。

    用自增 id 而不是时间戳：同一秒里可能有两条，按 ts 比会漏。
    """
    with conn(cfg, "interactions", persona) as c:
        rows = c.execute(
            "SELECT * FROM interactions WHERE id > ? ORDER BY id LIMIT ?",
            (int(last_id or 0), limit),
        ).fetchall()
    return [dict(r) for r in rows]


def turn_rows(cfg, turn_id: str, persona: str = "xi") -> list[dict[str, Any]]:
    with conn(cfg, "interactions", persona) as c:
        rows = c.execute(
            "SELECT * FROM interactions WHERE turn_id=? ORDER BY id", (turn_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def open_session(cfg, chat_id: int, persona: str = "xi") -> int:
    """开一段新会话，返回新的 session_no（/new 用）。"""
    with conn(cfg, "interactions", persona) as c:
        row = c.execute(
            "SELECT MAX(session_no) AS n FROM sessions WHERE chat_id=?", (chat_id,)
        ).fetchone()
        n = int((row["n"] if row and row["n"] else 0) or 0) + 1
        c.execute(
            "INSERT INTO sessions (chat_id, session_no, started_at, reason) VALUES (?,?,?,?)",
            (chat_id, n, clock.iso(), "/new"),
        )
        return n


def current_session(cfg, chat_id: int, persona: str = "xi") -> int:
    with conn(cfg, "interactions", persona) as c:
        row = c.execute(
            "SELECT MAX(session_no) AS n FROM sessions WHERE chat_id=?", (chat_id,)
        ).fetchone()
    return int((row["n"] if row and row["n"] else 0) or 0) or 1


def upsert_label(cfg, *, message_uid: str, label: dict[str, Any], persona: str = "xi") -> None:
    with conn(cfg, "labels", persona) as c:
        c.execute(
            """INSERT INTO labels
               (message_uid, ts, need_facts, registers, topics, emotion_cue,
                suggested_tools, has_image, confidence, label_version, labeled_by)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(message_uid) DO UPDATE SET
                 ts=excluded.ts, need_facts=excluded.need_facts,
                 registers=excluded.registers, topics=excluded.topics,
                 emotion_cue=excluded.emotion_cue,
                 suggested_tools=excluded.suggested_tools,
                 has_image=excluded.has_image, confidence=excluded.confidence,
                 label_version=excluded.label_version, labeled_by=excluded.labeled_by""",
            (
                message_uid,
                clock.iso(),
                label.get("need_facts", "uncertain"),
                json.dumps(label.get("registers") or [], ensure_ascii=False),
                json.dumps(label.get("topics") or [], ensure_ascii=False),
                label.get("emotion_cue"),
                json.dumps(label.get("suggested_tools") or [], ensure_ascii=False),
                1 if label.get("has_image") else 0,
                label.get("confidence"),
                "label-v1",
                persona,
            ),
        )


# ---------------------------------------------------------------- 事实块


def question_key(text: str) -> str:
    """把问题归一化：去标点、压空白，取前 60 字。"""
    t = re.sub(r"[\s，。！？、,.!?：:；;\-\"'“”‘’]+", "", text or "")
    return t[:60]


def put_facts(
    cfg,
    *,
    question: str,
    conversation_key: str,
    statements: list[dict[str, Any]],
    ttl_hours: float = 2,
    persona: str = "xi",
) -> list[int]:
    """写一批结论。同问题下的旧结论标 superseded，不删。"""
    qk = question_key(question)
    now = clock.now()
    expired = (now + timedelta(hours=ttl_hours)).isoformat(timespec="seconds")

    ids: list[int] = []
    with conn(cfg, "facts", persona) as c:
        old = c.execute(
            "SELECT fact_id FROM facts WHERE question_key=? AND superseded_by IS NULL",
            (qk,),
        ).fetchall()

        for item in statements:
            cur = c.execute(
                """INSERT INTO facts
                   (question_key, conversation_key, statement, source, confidence,
                    fetched_at, expired_at, superseded_by, conflict)
                   VALUES (?,?,?,?,?,?,?,NULL,?)""",
                (
                    qk,
                    conversation_key,
                    str(item.get("statement", "")),
                    item.get("source"),
                    item.get("confidence"),
                    clock.iso(now),
                    expired,
                    1 if item.get("conflict") else 0,
                ),
            )
            ids.append(int(cur.lastrowid or 0))

        for row in old:
            if ids:
                c.execute(
                    "UPDATE facts SET superseded_by=? WHERE fact_id=?",
                    (ids[0], row["fact_id"]),
                )
    return ids


def active_facts(
    cfg, *, conversation_key: str | None = None, limit: int = 20, persona: str = "xi"
) -> list[dict[str, Any]]:
    """没作废、没过期、没被替换的结论。"""
    sql = """SELECT * FROM facts
             WHERE superseded_by IS NULL
               AND (expired_at IS NULL OR expired_at > ?)"""
    args: list[Any] = [clock.iso()]
    if conversation_key:
        sql += " AND conversation_key=?"
        args.append(conversation_key)
    sql += " ORDER BY fact_id DESC LIMIT ?"
    args.append(limit)

    with conn(cfg, "facts", persona) as c:
        rows = c.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def mark_conflict(cfg, fact_ids: list[int], persona: str = "xi") -> None:
    """同一问题的多个来源说法不一：都留着，都打上标记。"""
    if not fact_ids:
        return
    marks = ",".join("?" for _ in fact_ids)
    with conn(cfg, "facts", persona) as c:
        c.execute(f"UPDATE facts SET conflict=1 WHERE fact_id IN ({marks})", fact_ids)


# ---------------------------------------------------------------- 记忆

VALID_KINDS = {
    "fact",
    "episode",
    "narrative",
    "inference",
    "knowledge",
    "hypothesis",
    "self_action",
}
VALID_STATUS = {"verified", "observed", "subjective", "inferred", "unverified"}


def add_memory(
    cfg,
    *,
    content: str,
    kind: str = "fact",
    status: str = "observed",
    source: str | None = None,
    speaker_qq: int | None = None,
    who: str | None = None,
    persona: str = "xi",
) -> int:
    kind = kind if kind in VALID_KINDS else "fact"
    status = status if status in VALID_STATUS else "observed"
    now = clock.iso()
    with conn(cfg, "memories", persona) as c:
        cur = c.execute(
            """INSERT INTO memories
               (ts, persona_id, kind, status, lifecycle, content, source,
                speaker_qq, who, created_at)
               VALUES (?,?,?,?, 'active', ?,?,?,?,?)""",
            (now, persona, kind, status, content.strip(), source, speaker_qq, who, now),
        )
        return int(cur.lastrowid or 0)


def close_memory(cfg, memory_id: int, why: str, persona: str = "xi") -> bool:
    with conn(cfg, "memories", persona) as c:
        cur = c.execute(
            "UPDATE memories SET lifecycle='invalidated', updated_at=?, closed_reason=? WHERE id=?",
            (clock.iso(), why, memory_id),
        )
        return cur.rowcount > 0


def similar_exists(
    cfg, content: str, *, kind: str | None = None, threshold: float = 0.75, recent: int = 200,
    persona: str = "xi",
) -> bool:
    """跟最近 N 条比字符相似度，超过阈值就算重复。"""
    sql = "SELECT content FROM memories WHERE lifecycle='active'"
    args: list[Any] = []
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(recent)

    target = _norm(content)
    with conn(cfg, "memories", persona) as c:
        rows = c.execute(sql, args).fetchall()
    for row in rows:
        if SequenceMatcher(None, target, _norm(row["content"])).ratio() >= threshold:
            return True
    return False


def save_embedding(cfg, memory_id: int, vector, model: str = "bge-m3", persona: str = "xi") -> None:
    from . import embed as _embed

    with conn(cfg, "memories", persona) as c:
        c.execute(
            "INSERT OR REPLACE INTO embeddings (memory_id, vector, model) VALUES (?,?,?)",
            (memory_id, _embed.pack(vector), model),
        )


def load_embeddings(cfg, persona: str = "xi") -> dict[int, list[float]]:
    from . import embed as _embed

    with conn(cfg, "memories", persona) as c:
        rows = c.execute("SELECT memory_id, vector FROM embeddings").fetchall()
    return {int(r["memory_id"]): _embed.unpack(r["vector"]) for r in rows if r["vector"]}


def search_memory(
    cfg,
    query: str,
    *,
    limit: int = 10,
    persona: str = "xi",
    min_score: float = 0.0,
    embedder=None,
    keyword_weight: float = 0.5,
) -> list[dict[str, Any]]:
    """混合检索：关键词（字面对得上）+ 向量（意思相近）。

    embedder 可以传一个 query→vector 的函数；传 None 就用默认的 bge-m3。
    向量算不出来（没网、没 key）就自动退回纯关键词——检索不能因为一个服务挂了就停。
    """
    grams = _grams(query)

    with conn(cfg, "memories", persona) as c:
        rows = c.execute(
            "SELECT * FROM memories WHERE lifecycle='active' ORDER BY id DESC LIMIT 2000"
        ).fetchall()
    if not rows:
        return []

    if embedder is None:
        from . import embed as _embed

        embedder = lambda q: _embed.embed_one(cfg, q)  # noqa: E731

    query_vec = None
    vectors: dict[int, list[float]] = {}
    try:
        query_vec = embedder(query)
        if query_vec:
            vectors = load_embeddings(cfg, persona)
    except Exception:  # noqa: BLE001
        query_vec = None

    scored: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        text = _norm(row["content"])
        kw = (sum(1 for g in grams if g in text) / len(grams)) if grams else 0.0

        vec = 0.0
        if query_vec and vectors.get(int(row["id"])):
            from . import embed as _embed

            vec = max(0.0, _embed.cosine(query_vec, vectors[int(row["id"])]))

        if kw == 0.0 and vec < 0.5:
            continue

        if query_vec:
            score = (1 - keyword_weight) * vec + keyword_weight * kw
        else:
            score = kw
        if score < min_score:
            continue

        item = dict(row)
        item["score"] = round(score, 3)
        item["kw"] = round(kw, 3)
        item["vec"] = round(vec, 3)
        if item.get("kind") == "self_action":
            item["tag"] = "你自己做的"
        elif item.get("status") in ("subjective", "inferred", "unverified"):
            item["tag"] = "非事实"
        scored.append((score, item))

    scored.sort(key=lambda x: (-x[0], -x[1]["id"]))
    return [item for _, item in scored[:limit]]


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").lower())


def _grams(text: str) -> list[str]:
    """中文整串 LIKE 基本搜不到，所以切 2–3 字片段。"""
    t = _norm(text)
    t = re.sub(r"[^\w\u4e00-\u9fff]+", "", t)
    if not t:
        return []
    out: list[str] = []
    for size in (2, 3):
        for i in range(0, max(0, len(t) - size + 1)):
            g = t[i : i + size]
            if g not in out:
                out.append(g)
    return out[:24]
