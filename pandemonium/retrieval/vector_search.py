"""Semantic vector search channel (LanceDB).

The query is embedded with the bge retrieval prefix (handled by ``embed_query``);
documents were embedded without a prefix at index time.
"""

from __future__ import annotations

from typing import List, Optional, Tuple


def search(lance, embedder, query: str, top_k: int = 20,
           where: Optional[str] = None) -> List[Tuple[str, float]]:
    try:
        vec = embedder.embed_query(query)
    except Exception:
        return []
    return lance.search(vec, limit=top_k, where=where)
