"""LanceDB vector store for code chunks.

Vectors are L2-normalized upstream (bge `normalize=True`), so L2 distance ordering
matches cosine ordering; we expose ``score = -distance`` and let the hybrid layer
normalize. Single-writer discipline: the indexer writes, the server opens read-only,
and we never compact/optimize while serving (avoids Windows NTFS lock errors).
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any, List, Optional, Sequence

import lancedb
import pyarrow as pa

TABLE = "code_chunks"
_STR_FIELDS = ["id", "repo_id", "file_id", "symbol_id", "path", "language",
               "chunk_type", "symbol_name", "text", "summary",
               "ref", "scope", "qualified_name", "parent", "unit_kind", "parent_ref"]


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
            # cAST completeness (Improvements4 #7): in LanceDB so vector_search can prefilter
            # on them (e.g. is_complete_unit) before KNN.
            pa.field("unit_kind", pa.string()),
            pa.field("parent_ref", pa.string()),
            pa.field("is_complete_unit", pa.bool_()),
            pa.field("requires_parent_header", pa.bool_()),
            pa.field("requires_imports", pa.bool_()),
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

    def needs_migration(self) -> bool:
        """True when an EXISTING table predates the current schema (missing the cAST columns).
        LanceDB has no in-place column add we rely on, so the caller must drop + full-rebuild
        (the index-format bump, Improvements4 #3). False for a fresh repo (create_table uses
        the current schema) and in read-only mode."""
        if self.read_only:
            return False
        try:
            if TABLE not in self.db.table_names():
                return False
            names = {f.name for f in self.db.open_table(TABLE).schema}
            return "parent_ref" not in names
        except Exception:
            return False

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

    def drop(self) -> None:
        """Drop the whole table. Used for a ``--full`` rebuild so we start from an empty
        table instead of issuing one delete per file: N per-file deletes meant N LanceDB
        versions, and since every version rewrites a manifest listing all fragments, that
        was O(N^2) manifest I/O. Safe when the table does not exist yet."""
        try:
            if TABLE in self.db.table_names():
                self.db.drop_table(TABLE)
        except Exception:
            pass
        self._table = None

    def compact(self) -> None:
        """Best-effort end-of-run maintenance: merge the small per-batch append fragments
        into a few large ones and prune superseded version manifests. Wrapped in try/except
        because on Windows a concurrently-serving read-only reader can hold NTFS locks on the
        data files; a failed compaction is non-fatal — the index is still correct, just less
        tidy on disk."""
        tbl = self.table()
        if tbl is None:
            return
        try:
            # lancedb >= 0.21: optimize() compacts fragments and prunes old versions in one
            # call (compact_files/cleanup_old_versions are deprecated).
            tbl.optimize(cleanup_older_than=timedelta(seconds=1))
        except Exception:
            try:  # fallback for older lancedb
                tbl.compact_files()
                tbl.cleanup_old_versions(older_than=timedelta(seconds=1))
            except Exception:
                pass

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
        """Fetch a stored embedding by chunk id (for similarity without re-embedding).

        Uses an id-prefiltered KNN to read exactly ONE row. The previous primary path called
        tbl.to_arrow() — materializing the ENTIRE embeddings table (4 KB+ per row) into RAM on
        every repo_graph/edit_plan/brief call. Never scan the whole table to fetch one id."""
        tbl = self.table()
        if tbl is None:
            return None
        try:  # zero-vector KNN with an id prefilter returns just the stored row.
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
