"""Index orchestration: scan -> parse -> chunk -> summarize -> embed -> store.

Incremental by content hash: unchanged files are skipped; changed files have their
old symbols/chunks/vectors/FTS rows purged then rebuilt; files gone from disk are
cascade-deleted.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from pandemonium.embeddings.local_embedder import LocalEmbedder
from pandemonium.indexer.chunker import build_chunks
from pandemonium.indexer.hasher import read_file
from pandemonium.indexer.ignore import IgnoreMatcher
from pandemonium.indexer.language_detector import is_parseable
from pandemonium.indexer.scanner import Candidate, scan
from pandemonium.indexer import tree_sitter_parser as tsp
from pandemonium.indexer.tracker import select_tracker
from pandemonium.logging.audit import AuditLog
from pandemonium.descriptor import build_descriptor
from pandemonium.enrich import load_enricher
from pandemonium.graph import extract_edges
from pandemonium.models import Chunk, FileRecord, IndexStats, Symbol
from pandemonium.refs import build_ref
from pandemonium.tags import heuristic_tags, scope_for
from pandemonium.storage.lancedb_store import LanceStore
from pandemonium.storage.sqlite_store import SqliteStore
from pandemonium.summaries.summarizer import extract_leading_comment, get_summarizer
from pandemonium.util import (file_id_for, fingerprint_for, now_iso, repo_id_for,
                              sha256_text, signature_hash_for, symbol_id_for)

_MAX_EMBED_CHARS = 4000
_HEADER_EXTS = (".hpp", ".hh", ".hxx", ".h")


def _sibling_headers(abs_path: Path) -> List[Path]:
    """Candidate header paths declaring the symbols a C++ translation unit defines: the
    same-stem header beside it, and (the common layout) the `src/foo.cpp <-> include/foo.hpp`
    mirror. Order = preference; the caller takes the first existing match per qualified name."""
    stem = abs_path.stem
    d = abs_path.parent
    out: List[Path] = []
    seen: set = set()

    def add(p: Path) -> None:
        if p not in seen:
            seen.add(p)
            out.append(p)

    for ext in _HEADER_EXTS:
        add(d / f"{stem}{ext}")
    parts = d.parts
    for i in range(len(parts) - 1, -1, -1):  # nearest `src/` segment -> `include/` mirror
        if parts[i] == "src":
            mirror = Path(*parts[:i], "include", *parts[i + 1:])
            for ext in _HEADER_EXTS:
                add(mirror / f"{stem}{ext}")
            break
    inc = d.parent / "include"
    for ext in _HEADER_EXTS:
        add(inc / f"{stem}{ext}")
    return out


class Indexer:
    def __init__(self, settings, sqlite: Optional[SqliteStore] = None,
                 lance: Optional[LanceStore] = None,
                 embedder: Optional[LocalEmbedder] = None,
                 summarizer=None, audit: Optional[AuditLog] = None):
        self.settings = settings
        self.repo_root = Path(settings.repo_root)
        self.repo_id = repo_id_for(self.repo_root)
        self.audit = audit or AuditLog(settings.audit_log_path)
        self.sqlite = sqlite or SqliteStore(settings.sqlite_path)
        self.sqlite.create_schema()
        self.embedder = embedder or LocalEmbedder.from_settings(settings)
        # Auto-detect the vector dim from the model so model swaps "just work".
        self.lance = lance or LanceStore(settings.lancedb_path, dim=self.embedder.dim)
        self.summarizer = summarizer or get_summarizer(settings, audit=self.audit)
        self.enricher = load_enricher(settings)
        self.tracker = select_tracker(self.repo_root)

    # -- public --------------------------------------------------------------
    def run(self, incremental: bool = True) -> IndexStats:
        stats = IndexStats()
        idx = self.settings.section("indexing")
        self.sqlite.upsert_repository(self.repo_id, self.settings.project_name,
                                      str(self.repo_root), self.tracker.mode, now_iso())
        matcher = IgnoreMatcher.load(self.repo_root)
        skipped_large: List[str] = []
        candidates = list(scan(self.repo_root, matcher,
                               max_file_bytes=idx.get("max_file_bytes", 2_000_000),
                               skipped_large=skipped_large))
        stats.scanned = len(candidates)
        stats.skipped_too_large = len(skipped_large)
        stored = self.sqlite.all_files(self.repo_id)
        scanned_paths: set[str] = set()

        for cand in candidates:
            scanned_paths.add(cand.rel_path)
            read = read_file(cand.abs_path)
            if read is None:
                continue
            _, text, content_hash = read
            row = stored.get(cand.rel_path)
            if incremental and self.tracker.is_unchanged(row, content_hash):
                stats.skipped += 1
                continue
            self._index_file(cand, text, content_hash, stats)
            stats.indexed += 1

        for rel_path, row in stored.items():
            if rel_path not in scanned_paths:
                self.sqlite.delete_file(row["id"])
                self.lance.delete_file(row["id"])
                stats.deleted += 1

        self.sqlite.commit()
        self.audit.log("index_run", repo=self.repo_id, mode=self.tracker.mode,
                       incremental=incremental, **vars(stats))
        return stats

    def close(self) -> None:
        self.sqlite.close()

    # -- C++ header↔cpp merge (Step 8) ---------------------------------------
    def _collect_header_docs(self, cand: Candidate) -> dict:
        """For a C++ translation unit, mine its sibling header(s) for function-declaration
        doc comments: canonical qualified_name -> (doc-window lines, decl-site ref). The
        indexer merges these onto the matching out-of-line definitions so a header's Doxygen
        doc (often the only prose) reaches the definition's embedded descriptor. Self-contained
        per `.cpp` (reads only sibling headers); see RESULTS.md for the silent-staleness limit
        when ONLY the header changes."""
        docs: dict = {}
        for hp in _sibling_headers(Path(cand.abs_path)):
            if not hp.exists():
                continue
            read = read_file(str(hp))
            if read is None:
                continue
            try:
                rel = hp.resolve().relative_to(self.repo_root.resolve()).as_posix()
            except (ValueError, OSError):
                rel = hp.name  # header outside the repo: doc still merges, ref is best-effort
            for qn, (window, line) in tsp.cpp_decl_docs(read[0]).items():
                docs.setdefault(qn, (window, build_ref(rel, "code", start_line=line,
                                                        end_line=line)))
        return docs

    # -- per-file ------------------------------------------------------------
    def _index_file(self, cand: Candidate, text: str, content_hash: str,
                    stats: IndexStats) -> None:
        fid = file_id_for(self.repo_id, cand.rel_path)
        # Purge any previous derived data for this file (idempotent re-index).
        self.sqlite.clear_file_derived(fid)
        self.lance.delete_file(fid)

        language = cand.language
        lines = text.splitlines()
        source_bytes = text.encode("utf-8", "replace")
        parsed = tsp.parse_symbols(source_bytes, language) if is_parseable(language) else []

        symbols: List[Symbol] = []
        for ps in parsed:
            sym_id = symbol_id_for(fid, ps.qualified_name, ps.start_line)
            symbols.append(Symbol(
                sym_id, self.repo_id, fid, ps.symbol_type, ps.name, ps.qualified_name,
                ps.signature, ps.start_line, ps.end_line, None, None))

        # C++ header↔cpp merge (Step 8): a translation unit's out-of-line definitions often
        # carry no comment — the Doxygen doc lives on the DECLARATION in the sibling header.
        # Mine those docs (keyed by canonical qualified_name) so the definition's descriptor
        # isn't just its signature. Empty for non-C++, for header files themselves, and for a
        # `.cpp` with no sibling header — so this is a no-op everywhere else.
        header_docs: dict = {}
        if (language == "cpp"
                and self.settings.section("indexing").get("cpp_header_merge", True)
                and Path(cand.rel_path).suffix.lower() not in _HEADER_EXTS):
            header_docs = self._collect_header_docs(cand)

        # Summaries for symbols (from their own source span + the few lines above it, so
        # the summarizer can pick up a leading Doxygen/JSDoc/`//` doc comment in C-family
        # languages where the doc lives above the symbol, not in the body).
        for s in symbols:
            src = "\n".join(lines[s.start_line - 1:s.end_line])
            preceding = lines[max(0, s.start_line - 1 - 12):s.start_line - 1]
            if header_docs:
                merged = header_docs.get((s.qualified_name or s.name).replace("::", "."))
                if merged is not None:
                    # The decl-site backlink is independent of whose doc won — record it
                    # whenever a matching header declaration exists (navigation feature).
                    s.decl_ref = merged[1]
                    # Use the header doc only when the definition has none locally; a doc on
                    # the definition itself is more specific and always wins.
                    if not extract_leading_comment(preceding):
                        preceding = merged[0]
            s.summary = self.summarizer.summarize_symbol(s, src, language=language,
                                                         preceding=preceding)
            s.content_hash = sha256_text(src)
            s.signature_hash = signature_hash_for(s.signature)
            s.fingerprint = fingerprint_for(src)
        file_summary = self.summarizer.summarize_file(cand.rel_path, language, text, symbols)

        scopes = self.settings.section("indexing").get("scopes", ["symbol", "file", "code"])
        chunks: List[Chunk] = build_chunks(self.repo_id, fid, cand.rel_path, language,
                                           text, symbols, scopes=scopes,
                                           window_lines=60, overlap=10)
        sym_by_id = {s.id: s for s in symbols}
        for c in chunks:
            if c.chunk_type == "file":
                c.summary = file_summary
            elif c.symbol_id and c.symbol_id in sym_by_id:
                c.summary = sym_by_id[c.symbol_id].summary
            else:
                c.summary = self.summarizer.summarize_chunk(c.content, language)

        # Card fields + descriptor (the descriptor is what we embed — Phase 2).
        embed_inputs: List[str] = []
        for c in chunks:
            sym = sym_by_id.get(c.symbol_id) if c.symbol_id else None
            c.scope = scope_for(c.chunk_type)
            signature = sym.signature if sym is not None else None
            if sym is not None:
                c.signature_hash = sym.signature_hash
                c.fingerprint = sym.fingerprint
                c.symbol_content_hash = sym.content_hash  # FULL-span hash for staleness
                c.decl_ref = sym.decl_ref  # header decl-site (Step 8), denormalized for repo_get
            if c.scope == "symbol" and sym is not None:
                qn = sym.qualified_name or sym.name
                c.qualified_name = qn
                c.parent = qn.rsplit(".", 1)[0] if "." in qn else ""
                c.ref = build_ref(c.path, "symbol", qn)
            elif c.scope == "file":
                c.ref = build_ref(c.path, "file")
            else:
                c.ref = build_ref(c.path, "code", start_line=c.start_line, end_line=c.end_line)
            c.tags = heuristic_tags(c.path, c.qualified_name, sym.name if sym else None,
                                    c.content)
            # Optional enrichment override (cache / claude_cli); heuristic is the fallback.
            override = self.enricher.get(c.ref, code=c.content, language=language or "")
            if override is not None:
                if override.summary:
                    c.summary = override.summary
                if override.tags:
                    c.tags = override.tags
            embed_inputs.append(build_descriptor(
                c.path, c.scope, language, c.qualified_name, signature, c.summary, c.tags))

        vectors = self.embedder.embed_documents(embed_inputs) if embed_inputs else []

        # Persist metadata.
        file_rec = FileRecord(fid, self.repo_id, cand.rel_path, language, content_hash,
                              cand.size, now_iso(), file_summary, 0)
        self.sqlite.upsert_file(file_rec)
        if symbols:
            self.sqlite.insert_symbols(symbols)
        if chunks:
            self.sqlite.insert_chunks(chunks)

        # Relationship graph: unresolved edges (calls/imports/inherits), per file.
        edges = extract_edges(source_bytes, language, symbols, fid, cand.rel_path,
                              self.repo_id)
        if edges:
            self.sqlite.insert_relationships(edges)

        # Persist vectors.
        rows = []
        for c, vec in zip(chunks, vectors):
            sym = sym_by_id.get(c.symbol_id) if c.symbol_id else None
            rows.append({
                "id": c.id, "repo_id": c.repo_id, "file_id": c.file_id,
                "symbol_id": c.symbol_id or "", "path": c.path,
                "language": c.language or "", "chunk_type": c.chunk_type,
                "symbol_name": sym.name if sym else "",
                "start_line": int(c.start_line), "end_line": int(c.end_line),
                "text": c.content[:_MAX_EMBED_CHARS], "summary": c.summary or "",
                "ref": c.ref or "", "scope": c.scope or "",
                "qualified_name": c.qualified_name or "", "parent": c.parent or "",
                "vector": vec,
            })
        self.lance.add(rows)

        stats.symbols += len(symbols)
        stats.chunks += len(chunks)


def run_index(settings, incremental: bool = True) -> IndexStats:
    indexer = Indexer(settings)
    try:
        return indexer.run(incremental=incremental)
    finally:
        indexer.close()


def reindex_changed(settings) -> IndexStats:
    return run_index(settings, incremental=True)
