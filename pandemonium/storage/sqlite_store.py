"""SQLite metadata store.

Owns the single SQLite connection (WAL + busy_timeout — Windows single-writer
discipline) and emits the **full Part 2 schema** up front so later phases need no
migrations. The MVP only populates repositories / files / symbols / chunks (+ the
FTS5 table, created via :class:`FtsStore` on the same connection).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional

from pandemonium.models import Chunk, FileRecord, Symbol
from pandemonium.storage.fts_store import FtsStore

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS repositories (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    root_path TEXT NOT NULL,
    tracking_mode TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    path TEXT NOT NULL,
    language TEXT,
    content_hash TEXT NOT NULL,
    size_bytes INTEGER,
    last_indexed_at TEXT NOT NULL,
    summary TEXT,
    importance INTEGER DEFAULT 0,
    mtime REAL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_files_repo_path ON files(repo_id, path);

CREATE TABLE IF NOT EXISTS tree_nodes (
    id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    path TEXT NOT NULL,
    parent_path TEXT,
    node_type TEXT NOT NULL,
    name TEXT NOT NULL,
    language TEXT,
    summary TEXT,
    importance INTEGER DEFAULT 0,
    file_count INTEGER DEFAULT 0,
    symbol_count INTEGER DEFAULT 0,
    content_hash TEXT,
    last_indexed_at TEXT
);

CREATE TABLE IF NOT EXISTS symbols (
    id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    file_id TEXT NOT NULL,
    symbol_type TEXT NOT NULL,
    name TEXT NOT NULL,
    qualified_name TEXT,
    signature TEXT,
    start_line INTEGER,
    end_line INTEGER,
    summary TEXT,
    content_hash TEXT,
    decl_ref TEXT
);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(repo_id, name);
CREATE INDEX IF NOT EXISTS idx_symbols_qname ON symbols(repo_id, qualified_name);
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_id);

CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    file_id TEXT NOT NULL,
    symbol_id TEXT,
    chunk_type TEXT NOT NULL,
    language TEXT,
    path TEXT NOT NULL,
    start_line INTEGER,
    end_line INTEGER,
    content TEXT NOT NULL,
    summary TEXT,
    content_hash TEXT NOT NULL,
    ref TEXT,
    scope TEXT,
    qualified_name TEXT,
    parent TEXT,
    tags TEXT,
    decl_ref TEXT,
    is_complete_unit INTEGER DEFAULT 1,
    unit_kind TEXT,
    parent_ref TEXT,
    requires_parent_header INTEGER DEFAULT 0,
    requires_imports INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file_id);
CREATE INDEX IF NOT EXISTS idx_chunks_repo ON chunks(repo_id);
CREATE INDEX IF NOT EXISTS idx_chunks_symbol ON chunks(symbol_id);

CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT,
    target_path TEXT,
    note_type TEXT NOT NULL,
    title TEXT,
    content TEXT NOT NULL,
    created_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    importance INTEGER DEFAULT 0,
    tags TEXT,
    content_hash TEXT
);

CREATE TABLE IF NOT EXISTS note_links (
    id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    note_id TEXT NOT NULL,
    linked_target_type TEXT NOT NULL,
    linked_target_id TEXT,
    linked_target_path TEXT,
    relationship_type TEXT
);

CREATE TABLE IF NOT EXISTS relationships (
    id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    target_name TEXT,
    confidence REAL DEFAULT 1.0
);

CREATE TABLE IF NOT EXISTS index_snapshots (
    id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    trigger_type TEXT,
    changed_files_count INTEGER,
    deleted_files_count INTEGER,
    summary TEXT
);

CREATE TABLE IF NOT EXISTS file_snapshots (
    id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    size_bytes INTEGER,
    modified_time TEXT,
    indexed_at TEXT
);
"""


class SqliteStore:
    def __init__(self, path: Any):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._configure()
        self.fts = FtsStore(self.conn)
        # Memoized symbols_by_name results (read path); cleared on any symbol write below.
        self._symname_cache: dict = {}

    def _configure(self) -> None:
        cur = self.conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA busy_timeout=5000;")
        cur.execute("PRAGMA synchronous=NORMAL;")
        cur.execute("PRAGMA foreign_keys=ON;")
        self.conn.commit()

    def create_schema(self) -> None:
        self.conn.executescript(SCHEMA_SQL)
        self._migrate_columns()
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chunks_scope ON chunks(repo_id, scope)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_ref ON chunks(ref)")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rel_file ON relationships(file_id)")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rel_source ON relationships(source_id)")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rel_target ON relationships(repo_id, target_name)")
        self.fts.create_schema()
        self.conn.commit()

    def _migrate_columns(self) -> None:
        """Add later-phase columns to pre-existing tables (no-op if already present)."""
        file_cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(files)")}
        if "mtime" not in file_cols:
            self.conn.execute("ALTER TABLE files ADD COLUMN mtime REAL DEFAULT 0")
        chunk_cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(chunks)")}
        for col in ("ref", "scope", "qualified_name", "parent", "tags",
                    "signature_hash", "fingerprint", "symbol_content_hash", "decl_ref",
                    "unit_kind", "parent_ref"):
            if col not in chunk_cols:
                self.conn.execute(f"ALTER TABLE chunks ADD COLUMN {col} TEXT")
        # cAST completeness flags (Improvements4 #7): INTEGER booleans with defaults so rows
        # written before the feature read back as complete / self-contained.
        for col, default in (("is_complete_unit", 1), ("requires_parent_header", 0),
                             ("requires_imports", 0)):
            if col not in chunk_cols:
                self.conn.execute(
                    f"ALTER TABLE chunks ADD COLUMN {col} INTEGER DEFAULT {default}")
        # Reliability: durable identity discriminants on symbols. decl_ref (Step 8) points an
        # out-of-line C++ definition at its header declaration site.
        sym_cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(symbols)")}
        for col in ("signature_hash", "fingerprint", "decl_ref"):
            if col not in sym_cols:
                self.conn.execute(f"ALTER TABLE symbols ADD COLUMN {col} TEXT")
        # Phase 9: per-file ownership + origin for the relationship graph.
        # Reliability: evidence_hash + created_at let us flag stale LLM 'affects' edges.
        rel_cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(relationships)")}
        for col in ("file_id", "origin", "receiver", "evidence", "evidence_hash",
                    "created_at"):
            if col not in rel_cols:
                self.conn.execute(f"ALTER TABLE relationships ADD COLUMN {col} TEXT")

    # -- repositories -------------------------------------------------------
    def upsert_repository(self, repo_id: str, name: str, root_path: str,
                          tracking_mode: str, now: str) -> None:
        self.conn.execute(
            """INSERT INTO repositories(id, name, root_path, tracking_mode, created_at, updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                   root_path=excluded.root_path, tracking_mode=excluded.tracking_mode,
                   updated_at=excluded.updated_at""",
            (repo_id, name, root_path, tracking_mode, now, now),
        )
        self.conn.commit()

    # -- files --------------------------------------------------------------
    def get_file(self, repo_id: str, path: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM files WHERE repo_id=? AND path=?", (repo_id, path)
        ).fetchone()

    def all_files(self, repo_id: str) -> dict[str, sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT * FROM files WHERE repo_id=?", (repo_id,)
        ).fetchall()
        return {r["path"]: r for r in rows}

    def upsert_file(self, f: FileRecord) -> None:
        self.conn.execute(
            """INSERT INTO files(id, repo_id, path, language, content_hash, size_bytes,
                                 last_indexed_at, summary, importance, mtime)
               VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET language=excluded.language,
                   content_hash=excluded.content_hash, size_bytes=excluded.size_bytes,
                   last_indexed_at=excluded.last_indexed_at, summary=excluded.summary,
                   importance=excluded.importance, mtime=excluded.mtime""",
            (f.id, f.repo_id, f.path, f.language, f.content_hash, f.size_bytes,
             f.last_indexed_at, f.summary, f.importance, f.mtime),
        )

    def chunk_ids_for_file(self, file_id: str) -> list[str]:
        return [r["id"] for r in self.conn.execute(
            "SELECT id FROM chunks WHERE file_id=?", (file_id,))]

    def clear_file_derived(self, file_id: str) -> list[str]:
        """Delete a file's symbols + chunks (+ FTS rows). Returns removed chunk ids
        so the caller can also purge their vectors from LanceDB."""
        chunk_ids = self.chunk_ids_for_file(file_id)
        self.fts.delete_chunks(chunk_ids)
        self.conn.execute("DELETE FROM chunks WHERE file_id=?", (file_id,))
        self.conn.execute("DELETE FROM symbols WHERE file_id=?", (file_id,))
        self.conn.execute("DELETE FROM relationships WHERE file_id=?", (file_id,))
        self._symname_cache.clear()
        return chunk_ids

    def delete_file(self, file_id: str) -> list[str]:
        """Fully remove a file (used when it disappears from disk)."""
        chunk_ids = self.clear_file_derived(file_id)
        self.conn.execute("DELETE FROM files WHERE id=?", (file_id,))
        return chunk_ids

    def clear_repo_derived(self, repo_id: str) -> None:
        """Full-rebuild fast path: wipe ALL derived rows for the repo in one shot. The per-file
        clear_file_derived() path deletes FTS rows per chunk (each a full scan of the UNINDEXED
        chunk_id) -> O(N^2) across a --full rebuild; this is O(rows) once. Leaves the `files`
        table intact (it is upserted per file; truly-gone files are pruned separately)."""
        self.conn.execute("DELETE FROM chunks WHERE repo_id=?", (repo_id,))
        self.conn.execute("DELETE FROM symbols WHERE repo_id=?", (repo_id,))
        self.conn.execute("DELETE FROM relationships WHERE repo_id=?", (repo_id,))
        self.fts.clear_all()
        self._symname_cache.clear()

    # -- symbols / chunks ---------------------------------------------------
    def insert_symbols(self, symbols: Iterable[Symbol]) -> None:
        self.conn.executemany(
            """INSERT OR REPLACE INTO symbols(id, repo_id, file_id, symbol_type, name,
                   qualified_name, signature, start_line, end_line, summary, content_hash,
                   signature_hash, fingerprint, decl_ref)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(s.id, s.repo_id, s.file_id, s.symbol_type, s.name, s.qualified_name,
              s.signature, s.start_line, s.end_line, s.summary, s.content_hash,
              s.signature_hash, s.fingerprint, s.decl_ref)
             for s in symbols],
        )
        self._symname_cache.clear()

    def insert_chunks(self, chunks: Iterable[Chunk],
                      symbol_names: Optional[dict] = None) -> None:
        chunk_list = list(chunks)
        self.conn.executemany(
            """INSERT OR REPLACE INTO chunks(id, repo_id, file_id, symbol_id, chunk_type,
                   language, path, start_line, end_line, content, summary, content_hash,
                   ref, scope, qualified_name, parent, tags, signature_hash, fingerprint,
                   symbol_content_hash, decl_ref, is_complete_unit, unit_kind, parent_ref,
                   requires_parent_header, requires_imports)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(c.id, c.repo_id, c.file_id, c.symbol_id, c.chunk_type, c.language, c.path,
              c.start_line, c.end_line, c.content, c.summary, c.content_hash,
              c.ref, c.scope, c.qualified_name, c.parent,
              json.dumps(c.tags) if c.tags is not None else None,
              c.signature_hash, c.fingerprint, c.symbol_content_hash, c.decl_ref,
              int(c.is_complete_unit), c.unit_kind, c.parent_ref,
              int(c.requires_parent_header), int(c.requires_imports))
             for c in chunk_list],
        )
        for c in chunk_list:
            if not c.symbol_id:
                symbol_name = ""
            elif symbol_names is not None:  # caller-supplied map avoids a SELECT per chunk (N+1)
                symbol_name = symbol_names.get(c.symbol_id) or ""
            else:  # back-compat: look it up only if the caller didn't supply the map
                r = self.conn.execute(
                    "SELECT name FROM symbols WHERE id=?", (c.symbol_id,)).fetchone()
                symbol_name = (r["name"] if r else "") or ""
            self.fts.index_chunk(c.id, c.content, c.summary or "", c.path, symbol_name)

    def commit(self) -> None:
        self.conn.commit()

    # -- lookups (retrieval) ------------------------------------------------
    def symbols_by_name(self, repo_id: str, name: str, limit: int = 20) -> list[sqlite3.Row]:
        """Exact (case-insensitive) first, then prefix, then substring.

        Memoized per (repo_id, name, limit): the substring match uses a leading-wildcard LIKE
        that can't use the name indexes (full symbols scan), and the fan-out search path
        re-issues the same lookups up to ~5x per query. The cache is cleared on any symbol
        write (see insert_symbols / clear_file_derived / clear_repo_derived)."""
        key = (repo_id, name, limit)
        cached = self._symname_cache.get(key)
        if cached is not None:
            return cached
        like = name.replace("%", r"\%").replace("_", r"\_")
        rows = self.conn.execute(
            r"""SELECT *, CASE
                    WHEN name = ? COLLATE NOCASE THEN 3
                    WHEN name LIKE ? ESCAPE '\' COLLATE NOCASE THEN 2
                    ELSE 1 END AS match_rank
                FROM symbols
                WHERE repo_id=? AND (name LIKE ? ESCAPE '\' COLLATE NOCASE
                                     OR qualified_name LIKE ? ESCAPE '\' COLLATE NOCASE)
                ORDER BY match_rank DESC, length(name) ASC
                LIMIT ?""",
            (name, like + "%", repo_id, "%" + like + "%", "%" + like + "%", limit),
        ).fetchall()
        self._symname_cache[key] = rows
        return rows

    def chunk_ids_for_symbols(self, symbol_ids: list[str]) -> list[str]:
        if not symbol_ids:
            return []
        placeholders = ",".join("?" * len(symbol_ids))
        rows = self.conn.execute(
            f"SELECT id FROM chunks WHERE symbol_id IN ({placeholders})", symbol_ids)
        return [r["id"] for r in rows]

    def get_chunks(self, chunk_ids: list[str]) -> dict[str, sqlite3.Row]:
        if not chunk_ids:
            return {}
        placeholders = ",".join("?" * len(chunk_ids))
        rows = self.conn.execute(
            f"""SELECT c.*, s.name AS symbol_name
                FROM chunks c LEFT JOIN symbols s ON c.symbol_id = s.id
                WHERE c.id IN ({placeholders})""", chunk_ids)
        return {r["id"]: r for r in rows}

    # -- relationships (graph) ---------------------------------------------
    def insert_relationships(self, rows: Iterable[dict]) -> None:
        self.conn.executemany(
            """INSERT OR REPLACE INTO relationships(id, repo_id, file_id, source_type,
                   source_id, relationship_type, target_type, target_id, target_name,
                   receiver, confidence, origin, evidence, evidence_hash, created_at)
               VALUES(:id,:repo_id,:file_id,:source_type,:source_id,:relationship_type,
                      :target_type,:target_id,:target_name,:receiver,:confidence,:origin,
                      :evidence,:evidence_hash,:created_at)""",
            list(rows))

    def out_edges(self, source_id: str, relationship_type: Optional[str] = None
                  ) -> list[sqlite3.Row]:
        if relationship_type:
            return self.conn.execute(
                "SELECT * FROM relationships WHERE source_id=? AND relationship_type=?",
                (source_id, relationship_type)).fetchall()
        return self.conn.execute(
            "SELECT * FROM relationships WHERE source_id=?", (source_id,)).fetchall()

    def relationships_by_type(self, repo_id: str,
                              relationship_type: str) -> list[sqlite3.Row]:
        """All edges of one relationship_type across the repo (one query vs N per-symbol
        out_edges) — used by the architectural-skeleton builder to aggregate call direction."""
        return self.conn.execute(
            "SELECT * FROM relationships WHERE repo_id=? AND relationship_type=?",
            (repo_id, relationship_type)).fetchall()

    def edges_by_target_name(self, repo_id: str, name: str,
                             relationship_type: str = "calls") -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM relationships WHERE repo_id=? AND target_name=? "
            "AND relationship_type=?", (repo_id, name, relationship_type)).fetchall()

    def symbols_by_qname(self, repo_id: str, qualified_name: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM symbols WHERE repo_id=? AND qualified_name=?",
            (repo_id, qualified_name)).fetchall()

    def all_symbols(self, repo_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT s.*, f.path AS path, f.language AS language "
            "FROM symbols s JOIN files f ON s.file_id=f.id "
            "WHERE s.repo_id=? ORDER BY f.path, s.start_line", (repo_id,)).fetchall()

    def chunk_by_ref(self, repo_id: str, ref: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM chunks WHERE repo_id=? AND ref=? LIMIT 1",
            (repo_id, ref)).fetchone()

    def files(self, repo_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM files WHERE repo_id=? ORDER BY path", (repo_id,)).fetchall()

    def chunk_tags(self, repo_id: str) -> list[sqlite3.Row]:
        """Tagged symbol chunks (ref/path/qualified_name/language/tags) for repo_map modes
        that aggregate the heuristic tag schema (entrypoints, domains). Restricted to
        symbol scope so file/code chunks of non-code files (READMEs, configs) don't add
        tag noise."""
        return self.conn.execute(
            "SELECT path, qualified_name, ref, language, tags FROM chunks "
            "WHERE repo_id=? AND scope='symbol' AND tags IS NOT NULL AND tags != ''",
            (repo_id,)).fetchall()

    def symbols_in_file(self, file_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM symbols WHERE file_id=? ORDER BY start_line", (file_id,)).fetchall()

    def counts(self, repo_id: str) -> dict[str, int]:
        def n(sql: str) -> int:
            return self.conn.execute(sql, (repo_id,)).fetchone()[0]
        return {
            "files": n("SELECT COUNT(*) FROM files WHERE repo_id=?"),
            "symbols": n("SELECT COUNT(*) FROM symbols WHERE repo_id=?"),
            "chunks": n("SELECT COUNT(*) FROM chunks WHERE repo_id=?"),
        }

    def close(self) -> None:
        try:
            self.conn.commit()
        finally:
            self.conn.close()
