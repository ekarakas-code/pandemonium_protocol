"""Keyword search channel (SQLite FTS5 bm25 over chunks)."""

from __future__ import annotations

from typing import List, Tuple


def search(sqlite, query: str, top_k: int = 20) -> List[Tuple[str, float]]:
    return sqlite.fts.search(query, limit=top_k)
