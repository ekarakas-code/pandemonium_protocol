"""LanceDB vector store for code chunks.

Vectors are L2-normalized upstream (bge `normalize=True`), so L2 distance ordering
matches cosine ordering; we expose ``score = -distance`` and let the hybrid layer
normalize. Single-writer discipline: the indexer writes, the server opens read-only,
and we never compact/optimize while serving (avoids Windows NTFS lock errors).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional, Sequence

import lancedb
import pyarrow as pa

TABLE = "code_chunks"
_STR_FIELDS = ["id", "repo_id", "file_id", "symbol_id", "path", "language",
               "chunk_type", "symbol_name", "text", "summary",
               "ref", "scope", "qualified_name", "parent"]


class LanceStore:
    def __init__(self, path: Any, dim: int = 384, read_only: bool = False):
        self.path = Path(path)
        self.dim = dim
        self.read_only = read_only
        if not read_only:
            self.path.mkdir(parents=True, exist_ok=True)
        self.db = lancedb.connect(str(self.path))
        self._table = None

    def _schema(self) -> pa.Schema:
        return pa.schema([
            pa.field("id", pa.string()),
            pa.field("repo_id", pa.string()),
            pa.field("file_id", pa.string()),
            pa.field("symbol_id", pa.string()),
            pa.field("path", pa.string()),
            pa.field("language", pa.string()),
            pa.field("chunk_type", pa.string()),
            pa.field("symbol_name", pa.string()),
            pa.field("start_line", pa.int32()),
            pa.field("end_line", pa.int32()),
            pa.field("text", pa.string()),
            pa.field("summary", pa.string()),
            pa.field("ref", pa.string()),
            pa.field("scope", pa.string()),
            pa.field("qualified_name", pa.string()),
            pa.field("parent", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), self.dim)),
        ])

    def table(self):
        if self._table is not None:
            return self._table
        # NB: table_names() (deprecated) returns list[str]; list_tables() returns
        # Table objects, so a string membership check there silently fails.
        names = self.db.table_names()
        if TABLE in names:
            self._table = self.db.open_table(TABLE)
        elif not self.read_only:
            self._table = self.db.create_table(TABLE, schema=self._schema())
        else:
            self._table = None
        return self._table

    def add(self, rows: List[dict]) -> None:
        if not rows:
            return
        clean = []
        for r in rows:
            rr = dict(r)
            for k in _STR_FIELDS:
                if rr.get(k) is None:
                    rr[k] = ""
            clean.append(rr)
        self.table().add(clean)

    def delete_file(self, file_id: str) -> None:
        tbl = self.table()
        if tbl is None:
            return
        safe = file_id.replace("'", "''")
        tbl.delete(f"file_id = '{safe}'")

    def search(self, vector: Sequence[float], limit: int = 20,
               where: Optional[str] = None) -> list[tuple[str, float]]:
        tbl = self.table()
        if tbl is None:
            return []
        try:
            q = tbl.search(list(vector))
            if where:
                q = q.where(where, prefilter=True)  # filter BEFORE KNN, not after
            results = q.limit(limit).to_list()
        except Exception:
            return []
        out: list[tuple[str, float]] = []
        for row in results:
            dist = float(row.get("_distance", 1.0))
            out.append((row["id"], -dist))
        return out

    def vector_for(self, chunk_id: str) -> Optional[list]:
        """Fetch a stored embedding by chunk id (for similarity without re-embedding)."""
        tbl = self.table()
        if tbl is None:
            return None
        try:  # Arrow scan — no pylance needed; table is small.
            import pyarrow.compute as pc
            at = tbl.to_arrow()
            filtered = at.filter(pc.equal(at["id"], chunk_id))
            if filtered.num_rows:
                return filtered.column("vector")[0].as_py()
        except Exception:
            pass
        try:  # Fallback: zero-vector KNN with an id prefilter returns the stored row.
            safe = chunk_id.replace("'", "''")
            rows = (tbl.search([0.0] * self.dim).where(f"id = '{safe}'", prefilter=True)
                    .limit(1).to_list())
            if rows and rows[0].get("vector") is not None:
                return list(rows[0]["vector"])
        except Exception:
            pass
        return None

    def count(self) -> int:
        tbl = self.table()
        if tbl is None:
            return 0
        try:
            return tbl.count_rows()
        except Exception:
            return 0
