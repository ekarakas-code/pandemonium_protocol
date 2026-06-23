"""Thin facade shared by the CLI and MCP server.

One-shot helpers that open the stores, do the work, and close. (The long-running MCP
server uses its own shared-context path in `mcp/tools.py` so the embedding model loads
only once.)
"""

from __future__ import annotations

from typing import List, Optional

from pandemonium import mapping, refs
from pandemonium.logging.audit import AuditLog
from pandemonium.indexer.hasher import read_file
from pandemonium.indexer.ignore import IgnoreMatcher
from pandemonium.indexer.index_runner import run_index
from pandemonium.indexer.scanner import scan
from pandemonium.models import IndexStats, SearchResult
from pandemonium.retrieval.context_packer import ContextPacker
from pandemonium.retrieval.hybrid_search import Retriever
from pandemonium.retrieval.symbol_search import lookup_symbol
from pandemonium.retrieval.tests_finder import find_tests
from pandemonium.storage.sqlite_store import SqliteStore
from pandemonium.util import repo_id_for


def index(settings, incremental: bool = True) -> IndexStats:
    return run_index(settings, incremental=incremental)


def search(settings, query: str, top_k: Optional[int] = None,
           mode: Optional[str] = None) -> List[SearchResult]:
    AuditLog(settings.audit_log_path).log("query", surface="cli", op="search", query=query)
    retriever = Retriever(settings)
    try:
        return retriever.search(query, top_k=top_k, mode=mode)
    finally:
        retriever.close()


def context_pack(settings, task: str, token_budget: Optional[int] = None,
                 mode: Optional[str] = None) -> str:
    AuditLog(settings.audit_log_path).log("context_pack", surface="cli", task=task,
                                          budget=token_budget)
    packer = ContextPacker(settings)
    try:
        return packer.build(task, token_budget=token_budget, mode=mode)
    finally:
        packer.close()


def get(settings, ref: str, expand: str = "exact", view: str = "full"):
    """Resolve a ref to exact code (edit-stable for symbol refs). `view` narrows the span
    (full | signature | head:N | lines:a-b) to save tokens. A partial cAST `ast_block` child
    auto-upgrades to its complete parent (expand="block" opts out). Returns ResolvedCode."""
    AuditLog(settings.audit_log_path).log("repo_get", surface="cli", ref=ref,
                                          expand=expand, view=view)
    store = SqliteStore(settings.sqlite_path)
    store.create_schema()
    repo_id = repo_id_for(settings.repo_root)
    try:
        row = store.chunk_by_ref(repo_id, ref)
        return refs.resolve_with_upgrade(
            settings.repo_root, ref, row,
            fetch_row=lambda r: store.chunk_by_ref(repo_id, r),
            expand=expand, view=view)
    finally:
        store.close()


def staleness(settings, refs: Optional[List[str]] = None) -> List[dict]:
    """Are the files behind these refs (or all indexed files) changed since indexing?
    Powers repo_changed — 'don't trust a symbol if its file changed after indexing'."""
    store = SqliteStore(settings.sqlite_path)
    store.create_schema()
    repo_id = repo_id_for(settings.repo_root)
    try:
        if refs:
            pairs = [(ref, refs_parse(ref)) for ref in refs]
        else:
            pairs = [(r["path"], r["path"]) for r in store.files(repo_id)]
        out = []
        for ref, path in pairs:
            row = store.get_file(repo_id, path)
            read = read_file(settings.repo_root / path)
            current = read[2] if read else None
            stored = row["content_hash"] if row is not None else None
            if current is None:
                state = "missing"
            elif stored is None:
                state = "not_indexed"
            elif current != stored:
                state = "changed"
            else:
                state = "current"
            out.append({"ref": ref, "path": path, "state": state,
                        "stale": state in ("missing", "changed", "not_indexed")})
        return out
    finally:
        store.close()


def refs_parse(ref: str) -> str:
    return refs.parse_ref(ref)[0]


def symbol(settings, name: str, limit: int = 10) -> List[dict]:
    AuditLog(settings.audit_log_path).log("query", surface="cli", op="symbol", name=name)
    store = SqliteStore(settings.sqlite_path)
    store.create_schema()
    try:
        return lookup_symbol(store, repo_id_for(settings.repo_root), name, limit=limit)
    finally:
        store.close()


def tests(settings, target: str, limit: int = 10) -> List[str]:
    store = SqliteStore(settings.sqlite_path)
    store.create_schema()
    try:
        return find_tests(store, repo_id_for(settings.repo_root), target, limit=limit)
    finally:
        store.close()


def graph_for(settings, ref: str):
    """Related code for a ref (callers/callees/imports/inheritance/tests)."""
    AuditLog(settings.audit_log_path).log("repo_graph", surface="cli", ref=ref)
    from pandemonium.graph import repo_graph
    return repo_graph(settings, ref)


def impact_for(settings, ref: str):
    """What may be affected if a ref changes (transitive callers + tests + files)."""
    AuditLog(settings.audit_log_path).log("repo_impact", surface="cli", ref=ref)
    from pandemonium.graph import repo_impact
    return repo_impact(settings, ref)


def edit_plan(settings, ref: str):
    """A ranked change plan for a ref (target + callers + tests + deps + risks + order)."""
    AuditLog(settings.audit_log_path).log("repo_edit_plan", surface="cli", ref=ref)
    from pandemonium.graph import edit_plan as _edit_plan
    return _edit_plan(settings, ref)


def logic_map(settings, topic: str):
    """Conceptual flow for a topic (relevant symbols + domains + call flow)."""
    AuditLog(settings.audit_log_path).log("repo_logic_map", surface="cli", topic=topic)
    from pandemonium.graph import repo_logic_map
    return repo_logic_map(settings, topic)


def brief(settings, task: str):
    """Capstone pre-flight brief for a task (verified-vs-guess hard-separated)."""
    AuditLog(settings.audit_log_path).log("repo_brief", surface="cli", task=task)
    from pandemonium.brief import repo_brief
    return repo_brief(settings, task)


def repo_map(settings, mode: str = "default") -> dict:
    store = SqliteStore(settings.sqlite_path)
    store.create_schema()
    try:
        return mapping.build_repo_map(settings, store, mode=mode)
    finally:
        store.close()


def detect_changes(settings) -> dict:
    """Dry-run change detection (for `pandemonium changed`)."""
    repo_id = repo_id_for(settings.repo_root)
    store = SqliteStore(settings.sqlite_path)
    store.create_schema()
    try:
        matcher = IgnoreMatcher.load(settings.repo_root)
        stored = store.all_files(repo_id)
        max_bytes = settings.section("indexing").get("max_file_bytes", 2_000_000)
        seen: set[str] = set()
        new: List[str] = []
        changed: List[str] = []
        unchanged: List[str] = []
        skipped_large: List[str] = []
        for cand in scan(settings.repo_root, matcher, max_file_bytes=max_bytes,
                         skipped_large=skipped_large):
            seen.add(cand.rel_path)
            read = read_file(cand.abs_path)
            if read is None:
                continue
            _, _, content_hash = read
            row = stored.get(cand.rel_path)
            if row is None:
                new.append(cand.rel_path)
            elif row["content_hash"] != content_hash:
                changed.append(cand.rel_path)
            else:
                unchanged.append(cand.rel_path)
        deleted = [p for p in stored if p not in seen]
        return {"new": new, "changed": changed, "deleted": deleted,
                "unchanged": unchanged, "skipped_too_large": skipped_large}
    finally:
        store.close()
