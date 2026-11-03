"""五个库：建表、连接、路径。

    store/processed/2-interactions/xi.db   交互流（每人格）
    store/processed/3-labels/labels.db     消息标注（全局）
    store/processed/4-facts/facts.db       事实块（全局）
    store/processed/5-memories/xi.db       记忆库（每人格）
    store/processed/10-tasks/xi.db         任务流水（每人格）

都是缓存：能从原始层重建。所以改表结构不用怕，删了重来。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_INTERACTIONS = """
CREATE TABLE IF NOT EXISTS interactions (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  turn_id     TEXT NOT NULL,
  ts          TEXT NOT NULL,
  chat_type   TEXT NOT NULL,
  chat_id     INTEGER NOT NULL,
  session_no  INTEGER NOT NULL DEFAULT 1,
  actor       TEXT NOT NULL,          -- 他 | 她 | 系统
  kind        TEXT NOT NULL,          -- 消息 | 工具 | DSH | 外发 | 状态
  user_id     INTEGER,
  nickname    TEXT,
  content     TEXT,
  ref         TEXT,
  image_refs  TEXT,
  delivery    TEXT
);
CREATE INDEX IF NOT EXISTS idx_int_chat ON interactions(chat_id, id);
CREATE INDEX IF NOT EXISTS idx_int_turn ON interactions(turn_id);
CREATE INDEX IF NOT EXISTS idx_int_ts   ON interactions(ts);

CREATE TABLE IF NOT EXISTS sessions (
  chat_id    INTEGER NOT NULL,
  session_no INTEGER NOT NULL,
  started_at TEXT NOT NULL,
  reason     TEXT,
  PRIMARY KEY (chat_id, session_no)
);
"""

SCHEMA_LABELS = """
CREATE TABLE IF NOT EXISTS labels (
  message_uid     TEXT PRIMARY KEY,
  ts              TEXT NOT NULL,
  need_facts      TEXT NOT NULL,
  registers       TEXT,
  topics          TEXT,
  emotion_cue     TEXT,
  suggested_tools TEXT,
  has_image       INTEGER DEFAULT 0,
  confidence      REAL,
  label_version   TEXT,
  labeled_by      TEXT
);
CREATE INDEX IF NOT EXISTS idx_labels_ts ON labels(ts);
"""

SCHEMA_FACTS = """
CREATE TABLE IF NOT EXISTS facts (
  fact_id          INTEGER PRIMARY KEY AUTOINCREMENT,
  question_key     TEXT NOT NULL,
  conversation_key TEXT NOT NULL,
  statement        TEXT NOT NULL,
  source           TEXT,
  confidence       REAL,
  fetched_at       TEXT NOT NULL,
  expired_at       TEXT,
  superseded_by    INTEGER,
  conflict         INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_facts_q    ON facts(question_key, expired_at);
CREATE INDEX IF NOT EXISTS idx_facts_conv ON facts(conversation_key, fetched_at);
"""

SCHEMA_MEMORIES = """
CREATE TABLE IF NOT EXISTS memories (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  ts            TEXT NOT NULL,
  persona_id    TEXT NOT NULL,
  kind          TEXT NOT NULL,
  status        TEXT NOT NULL,
  lifecycle     TEXT NOT NULL DEFAULT 'active',
  content       TEXT NOT NULL,
  source        TEXT,
  speaker_qq    INTEGER,
  who           TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT,
  closed_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_mem_life ON memories(lifecycle);
CREATE INDEX IF NOT EXISTS idx_mem_kind ON memories(kind);

CREATE TABLE IF NOT EXISTS embeddings (
  memory_id INTEGER PRIMARY KEY,
  vector    BLOB,
  model     TEXT
);

CREATE TABLE IF NOT EXISTS memory_hits (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  ts        TEXT NOT NULL,
  channel   TEXT,
  query     TEXT,
  memory_id INTEGER,
  score     REAL
);
"""

SCHEMA_TASKS = """
CREATE TABLE IF NOT EXISTS tasks (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ts         TEXT NOT NULL,
  persona_id TEXT NOT NULL,
  goal       TEXT,
  checks     TEXT,
  state      TEXT NOT NULL,
  session_id TEXT,
  sandbox    TEXT,
  started_at TEXT,
  ended_at   TEXT,
  tokens     INTEGER,
  verdict    TEXT,
  report     TEXT
);

CREATE TABLE IF NOT EXISTS task_turns (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER NOT NULL,
  seq     INTEGER NOT NULL,
  ts      TEXT NOT NULL,
  ask     TEXT,
  result  TEXT
);
"""

LAYOUT: dict[str, tuple[str, str]] = {
    "interactions": ("2-interactions/{persona}.db", SCHEMA_INTERACTIONS),
    "labels": ("3-labels/labels.db", SCHEMA_LABELS),
    "facts": ("4-facts/facts.db", SCHEMA_FACTS),
    "memories": ("5-memories/{persona}.db", SCHEMA_MEMORIES),
    "tasks": ("10-tasks/{persona}.db", SCHEMA_TASKS),
}


def db_path(cfg, kind: str, persona: str = "xi") -> Path:
    rel = LAYOUT[kind][0].format(persona=persona)
    return cfg.store_root() / "processed" / rel


def connect(path: Path | str) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


def ensure(cfg, kind: str, persona: str = "xi") -> Path:
    """建库建表，返回路径。启动时调一次。"""
    path = db_path(cfg, kind, persona)
    conn = connect(path)
    try:
        conn.executescript(LAYOUT[kind][1])
        conn.commit()
    finally:
        conn.close()
    return path


def ensure_all(cfg, persona: str = "xi") -> list[Path]:
    return [ensure(cfg, kind, persona) for kind in LAYOUT]
