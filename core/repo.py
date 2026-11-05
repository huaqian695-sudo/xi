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


# ---------------------------------------------------------------- 人

def next_person_id(cfg, persona: str = "xi") -> int:
    """新人的 id。主人固定 1，其他人往后排。"""
    with conn(cfg, "people", persona) as c:
        row = c.execute("SELECT MAX(id) AS m FROM people").fetchone()
        return max(1, int(row["m"] or 0)) + 1


def upsert_person(
    cfg,
    *,
    person_id: int,
    name: str | None = None,
    name_kind: str = "nickname",
    is_owner: bool | None = None,
    persona: str = "xi",
) -> int:
    """见一个人：建/更新档案，记下这次的名字。

    身份是 `person_id` 这个整数。名字只是别名——改多少次都还是同一个人。
    """
    now = clock.iso()
    with conn(cfg, "people", persona) as c:
        row = c.execute("SELECT id FROM people WHERE id=?", (int(person_id),)).fetchone()
        if row:
            c.execute("UPDATE people SET last_seen=? WHERE id=?", (now, int(person_id)))
            if is_owner is not None:
                c.execute(
                    "UPDATE people SET is_owner=? WHERE id=?",
                    (1 if is_owner else 0, int(person_id)),
                )
        else:
            c.execute(
                """INSERT INTO people (id, name, is_owner, first_seen, last_seen)
                   VALUES (?,?,?,?,?)""",
                (int(person_id), str(name or f"{person_id}号"), 1 if is_owner else 0, now, now),
            )

        if name:
            hit = c.execute(
                "SELECT id FROM people_names WHERE person_id=? AND name=? AND kind=?",
                (int(person_id), str(name), name_kind),
            ).fetchone()
            if hit:
                c.execute("UPDATE people_names SET last_seen=? WHERE id=?", (now, hit["id"]))
            else:
                c.execute(
                    """INSERT INTO people_names (person_id, name, kind, first_seen, last_seen)
                       VALUES (?,?,?,?,?)""",
                    (int(person_id), str(name), name_kind, now, now),
                )
        # 名片/昵称变了，让人表上的显示名跟着走
        if name:
            c.execute("UPDATE people SET name=? WHERE id=?", (str(name), int(person_id)))
        return int(person_id)


def get_person(cfg, person_id: int, persona: str = "xi") -> dict[str, Any] | None:
    with conn(cfg, "people", persona) as c:
        row = c.execute("SELECT * FROM people WHERE id=?", (int(person_id),)).fetchone()
    return dict(row) if row else None


def person_names(cfg, person_id: int, persona: str = "xi") -> list[dict[str, Any]]:
    with conn(cfg, "people", persona) as c:
        rows = c.execute(
            "SELECT * FROM people_names WHERE person_id=? ORDER BY last_seen DESC",
            (int(person_id),),
        ).fetchall()
    return [dict(r) for r in rows]


# ---- 绑定（只有通讯层用；核心代码不碰） --------------------------

def resolve_binding(cfg, platform: str, external_id: str, persona: str = "xi") -> int | None:
    """外部账号 → 核心人员 id。没绑过返回 None。"""
    with conn(cfg, "people", persona) as c:
        row = c.execute(
            "SELECT person_id FROM bindings WHERE platform=? AND external_id=?",
            (str(platform), str(external_id)),
        ).fetchone()
    return int(row["person_id"]) if row else None


def add_binding(cfg, platform: str, external_id: str, person_id: int, persona: str = "xi") -> None:
    with conn(cfg, "people", persona) as c:
        c.execute(
            """INSERT INTO bindings (platform, external_id, person_id, first_seen)
               VALUES (?,?,?,?)
               ON CONFLICT(platform, external_id) DO UPDATE SET person_id=excluded.person_id""",
            (str(platform), str(external_id), int(person_id), clock.iso()),
        )


def person_bindings(cfg, person_id: int, persona: str = "xi") -> list[dict[str, Any]]:
    with conn(cfg, "people", persona) as c:
        rows = c.execute(
            "SELECT * FROM bindings WHERE person_id=?", (int(person_id),)
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- 交互流

def add_interaction(
    cfg,
    *,
    turn_id: str,
    person_id: int,
    kind: str,
    content: str,
    conversation: str = "",
    session_no: int = 1,
    ref: str | None = None,
    image_refs: list[str] | None = None,
    files: list[dict] | None = None,
    delivery: str | None = None,
    ts: str | None = None,
    persona: str = "xi",
) -> int:
    with conn(cfg, "interactions", persona) as c:
        cur = c.execute(
            """INSERT INTO interactions
               (turn_id, ts, person_id, conversation, session_no, kind,
                content, ref, image_refs, files, delivery)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                turn_id,
                ts or clock.iso(),
                int(person_id or 0),
                str(conversation or ""),
                session_no,
                kind,
                content,
                ref,
                json.dumps(image_refs or [], ensure_ascii=False),
                json.dumps(files or [], ensure_ascii=False),
                delivery,
            ),
        )
        return int(cur.lastrowid or 0)


def recent_messages(
    cfg,
    *,
    conversation: str,
    session_no: int = 1,
    limit: int = 100,
    before_id: int | None = None,
    persona: str = "xi",
) -> list[dict[str, Any]]:
    """当前会话段最近的对话，按时间正序。"""
    sql = """SELECT * FROM interactions
             WHERE conversation=? AND session_no=? AND kind='消息'"""
    args: list[Any] = [str(conversation), session_no]
    if before_id is not None:
        sql += " AND id < ?"
        args.append(before_id)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)

    with conn(cfg, "interactions", persona) as c:
        rows = c.execute(sql, args).fetchall()
    return [dict(r) for r in reversed(rows)]


def recent_all(cfg, *, limit: int = 40, persona: str = "xi") -> list[dict[str, Any]]:
    """不分会话的最近消息（心跳看"摆在我面前的东西"用）。"""
    with conn(cfg, "interactions", persona) as c:
        rows = c.execute(
            "SELECT * FROM interactions WHERE kind='消息' ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def recent_actions(cfg, *, limit: int = 20, persona: str = "xi") -> list[dict[str, Any]]:
    """最近几轮里她动过的手（kind='工具'）。"""
    with conn(cfg, "interactions", persona) as c:
        rows = c.execute(
            "SELECT * FROM interactions WHERE kind='工具' ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def since_id(cfg, last_id: int, persona: str = "xi", limit: int = 200) -> list[dict[str, Any]]:
    """游标之后的所有交互（提炼器用）。用自增 id，别用时间戳。"""
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


def open_session(cfg, conversation: str, persona: str = "xi") -> int:
    """开一段新会话，返回新的 session_no（/new 用）。"""
    with conn(cfg, "interactions", persona) as c:
        row = c.execute(
            "SELECT MAX(session_no) AS n FROM sessions WHERE conversation=?",
            (str(conversation),),
        ).fetchone()
        n = int((row["n"] if row and row["n"] else 0) or 0) + 1
        c.execute(
            "INSERT INTO sessions (conversation, session_no, started_at, reason)"
            " VALUES (?,?,?,?)",
            (str(conversation), n, clock.iso(), "/new"),
        )
        return n


def current_session(cfg, conversation: str, persona: str = "xi") -> int:
    with conn(cfg, "interactions", persona) as c:
        row = c.execute(
            "SELECT MAX(session_no) AS n FROM sessions WHERE conversation=?",
            (str(conversation),),
        ).fetchone()
    return int((row["n"] if row and row["n"] else 0) or 0) or 1


# ---------------------------------------------------------------- 标注

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
                str(message_uid),
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
    """写一批结论。

    两种关系要分开：
    - **同一个来源**又说了 → 新的替换旧的（superseded）；
    - **不同来源**说法不一样 → 两条都留，都标 conflict=1，不替她选。

    这就是"两个来源对不上时不替她选"在数据层的落点。
    """
    qk = question_key(question)
    now = clock.now()
    expired = (now + timedelta(hours=ttl_hours)).isoformat(timespec="seconds")

    ids: list[int] = []
    with conn(cfg, "facts", persona) as c:
        existing = c.execute(
            """SELECT fact_id, source, statement FROM facts
               WHERE question_key=? AND superseded_by IS NULL""",
            (qk,),
        ).fetchall()

        for item in statements:
            src = item.get("source")
            cur = c.execute(
                """INSERT INTO facts
                   (question_key, conversation_key, statement, source, confidence,
                    fetched_at, expired_at, superseded_by, conflict)
                   VALUES (?,?,?,?,?,?,?,NULL,?)""",
                (
                    qk,
                    conversation_key,
                    str(item.get("statement", "")),
                    src,
                    item.get("confidence"),
                    clock.iso(now),
                    expired,
                    1 if item.get("conflict") else 0,
                ),
            )
            new_id = int(cur.lastrowid or 0)
            ids.append(new_id)

            for row in existing:
                same_source = (row["source"] or "") == (src or "")
                if same_source:
                    # 同源更新：旧的让位
                    c.execute(
                        "UPDATE facts SET superseded_by=? WHERE fact_id=?",
                        (new_id, row["fact_id"]),
                    )
                elif _differs(row["statement"], str(item.get("statement", ""))):
                    # 异源不同说法：两条都留，都标"说法不一"
                    c.execute(
                        "UPDATE facts SET conflict=1 WHERE fact_id IN (?, ?)",
                        (row["fact_id"], new_id),
                    )
    return ids


_NUM = re.compile(r"\d+(?:\.\d+)?")


def _differs(a: str, b: str) -> bool:
    """两句话是不是在说不一样的事。

    光比字面不够：「25 岁」和「26 岁」字面几乎一样，但那正是要标冲突的情况。
    所以先看数字——数字不同就是不同。
    """
    na, nb = set(_NUM.findall(a or "")), set(_NUM.findall(b or ""))
    if na and nb and na != nb:
        return True
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio() < 0.6


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
    speaker_id: int = 0,
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
                speaker_id, who, created_at)
               VALUES (?,?,?,?, 'active', ?,?,?,?,?)""",
            (now, persona, kind, status, content.strip(), source, int(speaker_id or 0), who, now),
        )
        return int(cur.lastrowid or 0)


def add_memories_bulk(
    cfg,
    items: list[dict[str, Any]],
    *,
    persona: str = "xi",
) -> list[int]:
    """一批记忆一次性写。任一条失败整批回滚——游标不推进才有意义。"""
    if not items:
        return []
    now = clock.iso()
    ids: list[int] = []
    with conn(cfg, "memories", persona) as c:
        for it in items:
            kind = str(it.get("kind") or "fact")
            status = str(it.get("status") or "observed")
            cur = c.execute(
                """INSERT INTO memories
                   (ts, persona_id, kind, status, lifecycle, content, source,
                    speaker_id, who, created_at)
                   VALUES (?,?,?,?, 'active', ?,?,?,?,?)""",
                (
                    now,
                    persona,
                    kind if kind in VALID_KINDS else "fact",
                    status if status in VALID_STATUS else "observed",
                    str(it.get("content") or "").strip(),
                    it.get("source"),
                    int(it.get("speaker_id") or 0),
                    it.get("who"),
                    now,
                ),
            )
            ids.append(int(cur.lastrowid or 0))
    return ids


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
    exclude_recent_days: float | None = 3.0,
) -> list[dict[str, Any]]:
    """混合检索：关键词（字面对得上）+ 向量（意思相近）。

    **最近 3 天的不查**：那些内容还在对话历史里，查出来就是重复喂给模型。
    历史覆盖"最近"，记忆覆盖"更早"——两本账错开，才不重。
    例外：她主动记的（source 以 manual 开头）立刻生效，不等三天。

    embedder 可以传一个 query→vector 的函数；传 None 就用默认的 bge-m3。
    向量算不出来（没网、没 key）就自动退回纯关键词。
    """
    from datetime import timedelta

    grams = _grams(query)

    sql = "SELECT * FROM memories WHERE lifecycle='active'"
    args: list[Any] = []
    if exclude_recent_days and exclude_recent_days > 0:
        cutoff = (clock.now() - timedelta(days=float(exclude_recent_days))).isoformat(
            timespec="seconds"
        )
        sql += " AND (created_at <= ? OR source LIKE 'manual%')"
        args.append(cutoff)
    sql += " ORDER BY id DESC LIMIT 2000"

    with conn(cfg, "memories", persona) as c:
        rows = c.execute(sql, args).fetchall()
    if not rows:
        # "一条记忆都没有"也要留痕：不然和"没查过"分不开
        record_memory_hits(cfg, query, [], persona=persona)
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
    hits = [item for _, item in scored[:limit]]
    record_memory_hits(cfg, query, hits, persona=persona)
    return hits


def record_memory_hits(
    cfg, query: str, hits: list[dict[str, Any]], persona: str = "xi"
) -> None:
    """把这次检索的痕迹写进 memory_hits。

    排查"她为什么想起这条 / 为什么想不起"全靠它：查了什么、命中哪些、得分多少。
    没命中也要留一行（memory_id 为空）——"查了但没找到"和"根本没查"是两回事。

    写失败不影响检索：记忆已经查出来了，不能因为记账把结果弄丢。
    """
    if not str(query or "").strip():
        return
    try:
        now = clock.iso()
        rows = [
            (now, None, str(query)[:200], int(h["id"]), float(h.get("score") or 0))
            for h in hits
        ] or [(now, None, str(query)[:200], None, 0.0)]
        with conn(cfg, "memories", persona) as c:
            c.executemany(
                "INSERT INTO memory_hits (ts, channel, query, memory_id, score)"
                " VALUES (?,?,?,?,?)",
                rows,
            )
    except Exception:  # noqa: BLE001 - 记账不能挡检索
        pass


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
