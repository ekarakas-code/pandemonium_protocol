"""Shared data models that flow through the indexing and retrieval pipeline.

These dataclasses pin the interfaces between modules (storage <-> indexer <->
retrieval <-> context pack), so each component can be built and tested in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FileRecord:
    """A tracked source file (row in `files`)."""

    id: str
    repo_id: str
    path: str  # repo-relative, POSIX
    language: Optional[str]
    content_hash: str
    size_bytes: int
    last_indexed_at: str
    summary: Optional[str] = None
    importance: int = 0


@dataclass
class Symbol:
    """A code symbol extracted by the parser (row in `symbols`)."""

    id: str
    repo_id: str
    file_id: str
    symbol_type: str  # class | function | method | interface | enum | ...
    name: str
    qualified_name: Optional[str]
    signature: Optional[str]
    start_line: int  # 1-based, inclusive
    end_line: int  # 1-based, inclusive
    summary: Optional[str] = None
    content_hash: Optional[str] = None
    # Durable identity discriminants (used to resolve same-named symbols + survive rename).
    signature_hash: Optional[str] = None
    fingerprint: Optional[str] = None
    # C++ header↔cpp merge (Step 8): for an out-of-line `.cpp` definition whose declaration
    # lives in a sibling header, a `path:line-line` ref to that declaration site. The
    # definition stays the canonical symbol; this is the navigable "also declared here".
    decl_ref: Optional[str] = None


@dataclass
class Chunk:
    """An embeddable, retrievable unit of code (row in `chunks` + LanceDB)."""

    id: str
    repo_id: str
    file_id: str
    symbol_id: Optional[str]
    chunk_type: str  # method | function | class | ast_block | block | window | file
    language: Optional[str]
    path: str  # repo-relative, POSIX
    start_line: int
    end_line: int
    content: str
    summary: Optional[str]
    content_hash: str
    # Card fields (Phase 1): populated by the indexer after chunking.
    ref: Optional[str] = None
    scope: Optional[str] = None  # symbol | file | code
    qualified_name: Optional[str] = None
    parent: Optional[str] = None
    tags: Optional[dict] = None
    # Denormalized from the owning symbol so `chunk_by_ref` gives resolve() everything
    # it needs to disambiguate / detect rename / confirm freshness in one lookup.
    # NOTE: symbol_content_hash is the owning symbol's FULL-span hash — distinct from
    # content_hash, which may be only a class header or one window of a large symbol.
    signature_hash: Optional[str] = None
    fingerprint: Optional[str] = None
    symbol_content_hash: Optional[str] = None
    # Denormalized from the owning symbol (C++ header↔cpp merge, Step 8) so repo_get can
    # surface "declared in <header>:line" from the one chunk_by_ref lookup it already does.
    decl_ref: Optional[str] = None
    # cAST completeness metadata (Improvements4 #3/#7): so delivery never hands the agent a
    # partial unit. An `ast_block` child carries parent_ref (the full symbol's ref) and
    # is_complete_unit=False; repo_get auto-upgrades to the parent. safe_for_reasoning is
    # NOT stored — it is derived downstream as (is_complete_unit and not requires_parent_header).
    is_complete_unit: bool = True
    unit_kind: Optional[str] = None  # function|method|class_outline|file_outline|ast_block|block|window
    parent_ref: Optional[str] = None
    requires_parent_header: bool = False
    requires_imports: bool = False


@dataclass
class SearchResult:
    """A merged, scored retrieval hit (one logical chunk)."""

    chunk_id: str
    path: str
    start_line: int
    end_line: int
    score: float
    symbol_id: Optional[str] = None
    symbol_name: Optional[str] = None
    chunk_type: Optional[str] = None
    language: Optional[str] = None
    content: Optional[str] = None
    summary: Optional[str] = None
    reason: Optional[str] = None
    # Card fields (Phase 1): a search hit IS a card — ref + scope + tags, no raw code.
    ref: Optional[str] = None
    scope: Optional[str] = None
    qualified_name: Optional[str] = None
    tags: Optional[dict] = None
    # cAST completeness (Improvements4 #3/#7): a card may be a partial `ast_block` child —
    # these flag it and point at the full parent so the agent never reasons from half a unit.
    unit_kind: Optional[str] = None
    is_complete_unit: bool = True
    safe_for_reasoning: bool = True  # derived: is_complete_unit and not requires_parent_header
    parent_ref: Optional[str] = None
    # Per-channel contributions, e.g. {"symbol": 1.0, "keyword": 0.4, "vector": 0.7}
    channel_scores: dict = field(default_factory=dict)


@dataclass
class IndexStats:
    """Outcome of an index run (for CLI output + audit log)."""

    scanned: int = 0
    indexed: int = 0
    skipped: int = 0
    deleted: int = 0
    symbols: int = 0
    chunks: int = 0
    errors: int = 0
    skipped_too_large: int = 0  # files dropped for exceeding indexing.max_file_bytes
