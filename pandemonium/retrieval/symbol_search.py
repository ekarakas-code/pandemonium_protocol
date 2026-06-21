"""Exact/prefix symbol search channel + symbol lookup for `pandemonium symbol`.

For code, exact symbol/path matches should outrank semantic similarity, so this
channel scores exact > prefix > substring and maps matched symbols to their chunks.
"""

from __future__ import annotations

import re
from typing import List, Tuple

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "where", "what", "which", "how",
    "when", "are", "was", "from", "into", "out", "not", "but", "has", "have",
    "does", "did", "use", "used", "using", "get", "set", "add", "new", "all",
    "implemented", "implement", "function", "method", "class", "code", "find",
    "fix", "after", "before", "should", "would", "could", "can", "missing",
}
_RANK_SCORE = {3: 1.0, 2: 0.7, 1: 0.4}


def _query_tokens(query: str) -> List[str]:
    seen: List[str] = []
    for tok in _TOKEN_RE.findall(query or ""):
        low = tok.lower()
        if len(tok) < 3 or low in _STOPWORDS:
            continue
        if tok not in seen:
            seen.append(tok)
    return seen


def search(sqlite, repo_id: str, query: str, top_k: int = 10) -> List[Tuple[str, float]]:
    scores: dict[str, float] = {}
    for tok in _query_tokens(query):
        for row in sqlite.symbols_by_name(repo_id, tok, limit=top_k):
            base = _RANK_SCORE.get(row["match_rank"], 0.4)
            for cid in sqlite.chunk_ids_for_symbols([row["id"]]):
                scores[cid] = max(scores.get(cid, 0.0), base)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return ranked[:top_k]


def lookup_symbol(sqlite, repo_id: str, name: str, limit: int = 10) -> List[dict]:
    """Resolve a symbol by name -> file/path, line range, signature, summary."""
    out: List[dict] = []
    for row in sqlite.symbols_by_name(repo_id, name, limit=limit):
        frow = sqlite.conn.execute(
            "SELECT path, language FROM files WHERE id=?", (row["file_id"],)).fetchone()
        out.append({
            "name": row["name"],
            "qualified_name": row["qualified_name"],
            "type": row["symbol_type"],
            "path": frow["path"] if frow else None,
            "language": frow["language"] if frow else None,
            "start_line": row["start_line"],
            "end_line": row["end_line"],
            "signature": row["signature"],
            "summary": row["summary"],
        })
    return out
