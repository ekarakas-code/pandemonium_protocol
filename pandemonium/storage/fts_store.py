"""SQLite FTS5 keyword index over chunks.

Shares the SAME connection as :class:`SqliteStore` (one writer, Windows-safe).
Rows are maintained manually so we can delete by chunk_id when a file changes.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Iterable, Optional

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")

CREATE_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED,
    content,
    summary,
    path,
    symbol_name,
    tokenize = 'unicode61'
);
"""


class FtsStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create_schema(self) -> None:
        self.conn.execute(CREATE_SQL)

    def index_chunk(self, chunk_id: str, content: str, summary: str,
                    path: str, symbol_name: str) -> None:
        self.conn.execute("DELETE FROM chunks_fts WHERE chunk_id=?", (chunk_id,))
        self.conn.execute(
            "INSERT INTO chunks_fts(chunk_id, content, summary, path, symbol_name) "
            "VALUES(?,?,?,?,?)",
            (chunk_id, content, summary, path, symbol_name),
        )

    def delete_chunks(self, chunk_ids: Iterable[str]) -> None:
        ids = list(chunk_ids)
        if ids:
            self.conn.executemany(
                "DELETE FROM chunks_fts WHERE chunk_id=?", [(i,) for i in ids])

    @staticmethod
    def build_match_query(text: str) -> Optional[str]:
        """Token -> prefix terms OR-joined (recall-friendly for code search).

        Only [A-Za-z0-9_] tokens survive, so the resulting MATCH string can never
        contain FTS5 syntax characters from user input.
        """
        seen: list[str] = []
        for tok in _TOKEN_RE.findall(text or ""):
            if tok not in seen:
                seen.append(tok)
        if not seen:
            return None
        return " OR ".join(f"{tok}*" for tok in seen)

    def search(self, query: str, limit: int = 20) -> list[tuple[str, float]]:
        match = self.build_match_query(query)
        if not match:
            return []
        try:
            rows = self.conn.execute(
                "SELECT chunk_id, bm25(chunks_fts) AS score "
                "FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY score LIMIT ?",
                (match, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        # bm25(): lower is better -> negate so higher = better, then normalized upstream.
        return [(r["chunk_id"], -float(r["score"])) for r in rows]
