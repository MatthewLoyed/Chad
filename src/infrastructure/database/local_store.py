"""
Local-only storage backend (issue #416).

Zero-setup fallback for memory, chat history, and tool RAG when Supabase isn't
configured (no SUPABASE_URL/SUPABASE_ANON_KEY in .env). Backed by a single
sqlite file at data/local_chad.db, auto-created on first use — no migrations,
no external services, nothing for the user to run.

Vector search here is brute-force numpy cosine similarity over embeddings
stored as float32 blobs. That's the right tradeoff at this scale (one user,
hundreds of tools, at most low-thousands of memories) — an indexed ANN
structure (faiss/sqlite-vec) would only pay for itself at row counts this
app never reaches, and cosine over <10k vectors is low-single-digit-ms.
"""
import json
import os
import sqlite3
import struct
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

_DB_LOCK = threading.Lock()
_DB_PATH: Optional[str] = None


def get_local_db_path() -> str:
    """Resolve (and ensure the parent dir for) the local sqlite file.

    Anchored to the project root rather than the process's working directory: this
    used to return a bare relative ``data/local_chad.db``, so anything launched from
    a different cwd silently read and wrote a *different* database. In prod the
    Brain happens to run from the repo root and the two agree, which is exactly why
    it went unnoticed — it surfaced when persona files resolved via ``CHAD_HOME``
    while their history rows landed somewhere else entirely (#589).

    Overridable via ``CHAD_LOCAL_DB_PATH`` (absolute or relative) for tests.
    """
    global _DB_PATH
    if _DB_PATH:
        return _DB_PATH
    override = os.getenv("CHAD_LOCAL_DB_PATH")
    if override:
        path = Path(override)
    else:
        from src.shared.paths import get_project_root
        path = get_project_root() / "data" / "local_chad.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    _DB_PATH = str(path)
    return _DB_PATH


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(get_local_db_path(), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    importance REAL DEFAULT 0.5,
    created_at TEXT NOT NULL,
    last_accessed TEXT NOT NULL,
    access_count INTEGER DEFAULT 1,
    source TEXT DEFAULT 'user',
    embedding BLOB,
    metadata TEXT DEFAULT '{}',
    session_key TEXT
);
CREATE INDEX IF NOT EXISTS idx_memories_session_key ON memories (session_key);
-- Expression index on the dedup hash every ingestor writes into metadata.hash (issue
-- #601): has_memory_with_hash used to be a full `SCAN memories` computing json_extract
-- per row -- 38ms/call at ~11k rows in production and growing linearly with table
-- size, dominating bulk-ingestion sync cycles far more than embedding generation.
-- SQLite supports indexing an expression directly (3.9+), so this turns it into an
-- index SEARCH.
CREATE INDEX IF NOT EXISTS idx_memories_hash ON memories (json_extract(metadata, '$.hash'));
-- Gmail personal_index upsert identity (issue #617): SyncManager looks up by
-- metadata.message_id the same way Drive catalog uses metadata.file_id.
CREATE INDEX IF NOT EXISTS idx_memories_message_id ON memories (json_extract(metadata, '$.message_id'));
-- Domain agents: every scoped read filters on metadata.domain, so it gets the same
-- expression-index treatment as hash/message_id above. NULL (untagged) rows are the
-- global tier and stay visible everywhere, so this index is sparse by design.
CREATE INDEX IF NOT EXISTS idx_memories_domain ON memories (json_extract(metadata, '$.domain'));

CREATE TABLE IF NOT EXISTS conversation_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    session_key TEXT,
    timestamp TEXT NOT NULL,
    metadata TEXT
);
CREATE INDEX IF NOT EXISTS idx_conv_history_session_key ON conversation_history (session_key);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'New Chat',
    model TEXT,
    project_path TEXT,
    domain TEXT,
    is_pinned INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Domain agents (user-created specializations: Financial, Fitness, ...). Local-only by
-- policy -- a domain row carries the user's own persona prose and is never mirrored to
-- Supabase. `id` is the slug written onto memories/graph nodes; renaming a domain must
-- therefore change `name`, never `id`.
CREATE TABLE IF NOT EXISTS domains (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    icon TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT '',
    persona TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata TEXT,
    is_archived INTEGER NOT NULL DEFAULT 0,
    compacted_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages (conversation_id);

CREATE TABLE IF NOT EXISTS tool_embeddings (
    name TEXT PRIMARY KEY,
    description_hash TEXT NOT NULL,
    embedding BLOB NOT NULL,
    embedding_refined BLOB,
    oats_last_refined TEXT,
    oats_outcome_count INTEGER DEFAULT 0,
    updated_at TEXT NOT NULL
);
"""

# Columns added to existing tables after their initial CREATE ships. `CREATE TABLE
# IF NOT EXISTS` won't retro-fit these onto a db created by an older build, so they're
# applied idempotently at init. (col, "TYPE [DEFAULT ...]") per ALTER TABLE ADD COLUMN.
_COLUMN_MIGRATIONS = {
    "conversations": [
        ("domain", "TEXT"),                     # domain-agent scope (Financial, Fitness, ...)
    ],
    "tool_embeddings": [
        ("embedding_refined", "BLOB"),          # OATS-refined vector (issue #470)
        ("oats_last_refined", "TEXT"),
        ("oats_outcome_count", "INTEGER DEFAULT 0"),
        ("preamble", "TEXT"),                   # local-model-generated intent preamble (issue #541)
    ],
}


def _apply_column_migrations(conn: sqlite3.Connection) -> None:
    """Add any missing columns to already-created tables (see `_COLUMN_MIGRATIONS`)."""
    for table, columns in _COLUMN_MIGRATIONS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for col, coldef in columns:
            if col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coldef}")


# FTS5 index over chat transcripts (session/transcript search). Indexed columns are
# content + conversation title; message_id/conversation_id/role are UNINDEXED so
# search can join back to the messages row and return ranked snippets.
_MESSAGES_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    message_id UNINDEXED,
    conversation_id UNINDEXED,
    role UNINDEXED,
    content,
    title,
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(message_id, conversation_id, role, content, title)
    VALUES (
        new.id,
        new.conversation_id,
        new.role,
        new.content,
        COALESCE((SELECT title FROM conversations WHERE id = new.conversation_id), '')
    );
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages BEGIN
    DELETE FROM messages_fts WHERE message_id = old.id;
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_au AFTER UPDATE OF content, role, conversation_id ON messages BEGIN
    DELETE FROM messages_fts WHERE message_id = old.id;
    INSERT INTO messages_fts(message_id, conversation_id, role, content, title)
    VALUES (
        new.id,
        new.conversation_id,
        new.role,
        new.content,
        COALESCE((SELECT title FROM conversations WHERE id = new.conversation_id), '')
    );
END;

CREATE TRIGGER IF NOT EXISTS conversations_fts_au AFTER UPDATE OF title ON conversations BEGIN
    UPDATE messages_fts SET title = new.title WHERE conversation_id = new.id;
END;
"""


def _ensure_messages_fts(conn: sqlite3.Connection) -> None:
    """Create messages_fts + sync triggers, and backfill if the index is empty/stale."""
    conn.executescript(_MESSAGES_FTS_SCHEMA)
    msg_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    fts_count = conn.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0]
    if msg_count == 0:
        return
    if fts_count == msg_count:
        return
    # Rebuild from scratch when counts diverge (first upgrade, or a missed trigger path).
    conn.execute("DELETE FROM messages_fts")
    conn.execute(
        """
        INSERT INTO messages_fts(message_id, conversation_id, role, content, title)
        SELECT m.id, m.conversation_id, m.role, m.content,
               COALESCE(c.title, '')
        FROM messages m
        LEFT JOIN conversations c ON c.id = m.conversation_id
        """
    )


def init_local_db() -> None:
    """Idempotently create the local schema. Safe to call from every repository's __init__."""
    with _DB_LOCK:
        conn = _connect()
        try:
            conn.executescript(_SCHEMA)
            _apply_column_migrations(conn)
            _ensure_messages_fts(conn)
            conn.commit()
        finally:
            conn.close()


def local_db_connect() -> sqlite3.Connection:
    """Open a fresh connection with the schema guaranteed present. Caller owns closing it."""
    init_local_db()
    return _connect()


# --- embedding blob helpers -------------------------------------------------

def embedding_to_blob(embedding: List[float]) -> bytes:
    arr = np.asarray(embedding, dtype=np.float32)
    return struct.pack("<I", len(arr)) + arr.tobytes()


def blob_to_embedding(blob: bytes) -> np.ndarray:
    (n,) = struct.unpack_from("<I", blob, 0)
    return np.frombuffer(blob, dtype=np.float32, count=n, offset=4)


def cosine_topk(query_embedding: List[float], rows: List[sqlite3.Row], embedding_col: str,
                 k: int, match_threshold: float = 0.0) -> List[Dict[str, Any]]:
    """Rank `rows` (each with an `embedding_col` blob) by cosine similarity to `query_embedding`.

    Returns row dicts (embedding blob stripped) with a `similarity` key, best first,
    limited to `k` and filtered to `similarity > match_threshold` — mirrors the
    Supabase `match_memories`/`retrieve_relevant_tools` RPC semantics.
    """
    if not rows:
        return []
    query_vec = np.asarray(query_embedding, dtype=np.float32)
    query_norm = np.linalg.norm(query_vec)
    if query_norm == 0:
        return []

    scored = []
    for row in rows:
        blob = row[embedding_col]
        if blob is None:
            continue
        vec = blob_to_embedding(blob)
        vec_norm = np.linalg.norm(vec)
        if vec_norm == 0:
            continue
        similarity = float(np.dot(query_vec, vec) / (query_norm * vec_norm))
        if similarity <= match_threshold:
            continue
        d = {k2: row[k2] for k2 in row.keys() if k2 != embedding_col}
        d["similarity"] = similarity
        scored.append(d)

    scored.sort(key=lambda r: r["similarity"], reverse=True)
    return scored[:k]
