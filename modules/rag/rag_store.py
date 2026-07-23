"""
SQLite-backed vector store for the RAG layer.

Chunks and their embeddings live in the `rag_chunks` table (see
database/database_models.py). The applicant's background is tiny — a few dozen
chunks — so search is an honest full scan with cosine similarity rather than an
approximate index. That is the right call at this scale and keeps the store
dependency-free.
"""

from __future__ import annotations

import json
import os
import sqlite3

from modules.rag.rag_embed import cosine

# Resolve the DB path the same way database/database_db.py does, but without
# importing Flask, so the store is usable from scripts and tests too.
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DEFAULT_DB = os.environ.get("JOB_BOT_DB", os.path.join(_BASE_DIR, "job_bot.db"))

_CREATE = """
CREATE TABLE IF NOT EXISTS rag_chunks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT    NOT NULL,
    ref_id     INTEGER,
    chunk      TEXT    NOT NULL,
    dim        INTEGER NOT NULL,
    vector     TEXT    NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_source ON rag_chunks(source);
"""


class VectorStore:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or _DEFAULT_DB

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        # Defensive: create the table if the app's init_db() hasn't run yet.
        conn.executescript(_CREATE)
        return conn

    def clear(self, source: str) -> int:
        """Drop all chunks for a source so re-indexing never leaves stale rows."""
        conn = self._connect()
        try:
            cur = conn.execute("DELETE FROM rag_chunks WHERE source = ?", (source,))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def add(self, source: str, chunk: str, vector: list[float], ref_id: int | None = None) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO rag_chunks (source, ref_id, chunk, dim, vector) VALUES (?,?,?,?,?)",
                (source, ref_id, chunk, len(vector), json.dumps(vector)),
            )
            conn.commit()
        finally:
            conn.close()

    def add_many(self, source: str, items: list[tuple[str, list[float]]]) -> int:
        conn = self._connect()
        try:
            conn.executemany(
                "INSERT INTO rag_chunks (source, ref_id, chunk, dim, vector) VALUES (?,?,?,?,?)",
                [(source, None, chunk, len(vec), json.dumps(vec)) for chunk, vec in items],
            )
            conn.commit()
            return len(items)
        finally:
            conn.close()

    def count(self, source: str | None = None) -> int:
        conn = self._connect()
        try:
            if source:
                row = conn.execute("SELECT COUNT(*) FROM rag_chunks WHERE source = ?", (source,)).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM rag_chunks").fetchone()
            return int(row[0])
        finally:
            conn.close()

    def search(self, query_vector: list[float], k: int = 3, source: str | None = None) -> list[dict]:
        """Return the top-k chunks by cosine similarity, most similar first."""
        conn = self._connect()
        try:
            if source:
                rows = conn.execute(
                    "SELECT chunk, vector FROM rag_chunks WHERE source = ?", (source,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT chunk, vector FROM rag_chunks").fetchall()
        finally:
            conn.close()

        scored = []
        for row in rows:
            vec = json.loads(row["vector"])
            scored.append({"chunk": row["chunk"], "score": cosine(query_vector, vec)})
        scored.sort(key=lambda r: r["score"], reverse=True)
        return scored[:k]
