# Build Log — PandemoniumProtocol (ProjectRAG) MVP

A stage-by-stage record of how the MVP vertical slice was built and verified.
Scope decided with the user: **end-to-end MVP** (init → index → hybrid search →
token-budgeted context pack → CLI + MCP), Python-first, dogfooded on this repo;
**Part 2** as the authoritative architecture; summaries default to heuristic with an
**opt-in external-LLM** provider; project named **PandemoniumProtocol** (package
`pandemonium`).

## Verified toolchain (Python 3.12 / Windows x64, all prebuilt wheels)

torch 2.12.1+cpu · lancedb 0.33.0 · tree-sitter 0.25.2 · **tree-sitter-python**
(see Stage 3 gotcha) · tiktoken 0.13.0 · sentence-transformers 5.6.0 · mcp 1.28.0 ·
typer 0.25.1 · rich · fastapi · uvicorn. CPU torch installed first to keep the pull
~300 MB instead of ~2 GB.

## Stages

### Stage 1 — Scaffold ✅
`pyproject.toml` (deps + `pandemonium` console script + `[llm]` extra), package
skeleton, `config/settings.py` (defaults + YAML deep-merge + path resolution),
`.pandemoniumignore` (self-excludes `.pandemonium/` + secrets), `pandemonium.yaml`,
`README.md`, shared `models.py` / `util.py` (deterministic IDs), `logging/audit.py`.

### Stage 2 — Storage ✅
`sqlite_store.py` — single WAL connection, **full Part 2 schema** emitted up front
(only `repositories/files/symbols/chunks` populated in the MVP). `fts_store.py` — FTS5
keyword index on the same connection (token→prefix-OR query builder, bm25). 
`lancedb_store.py` — `code_chunks` table (384-dim, delete-by-file, KNN). Verified by
unit round-trip.

### Stage 3 — Indexer ✅
`scanner` (ignore-pruned walk) → `language_detector` + `hasher` (sha256, incremental)
→ `tree_sitter_parser` (walk-based: class/function/method + line ranges) → `chunker`
(class-header + method/function spans; line-window fallback) → `summarizer`
(heuristic) → `local_embedder` (bge-small, lazy) → SQLite/FTS5/LanceDB. Incremental:
skip unchanged hash, purge+rebuild changed, cascade-delete removed.
**Gotcha (caught at test time):** `tree-sitter-language-pack`'s bundled wheels expose a
nonstandard `builtins.*` node API (`root_node` as a *method*, no `Node.type`) that does
NOT interoperate with the documented `tree_sitter` core — symbols came back empty.
Switched to the canonical `tree_sitter` + per-grammar `tree-sitter-python` path. The
earlier dependency research had asserted language-pack returns `tree_sitter.*` objects;
it does not — only empirical testing surfaced this.

### Stage 4 — Retrieval + hybrid merge ✅
`symbol_search` (exact/prefix/substring), `keyword_search` (FTS5), `vector_search`
(LanceDB + bge query prefix). `hybrid_search()` — per-channel min-max **with guards**
(single/identical/empty channels don't divide-by-zero or force 1.0), weighted merge
(symbol .40 / keyword .30 / vector .30), overlap dedup. `Retriever` ties it together.

### Stage 5 — Context packer + token counter ✅
`tokens/counter.py` (tiktoken cl100k + chars/4 fallback). `context_packer.py` greedily
fills a budget-respecting markdown pack (task, project area, file list, per-file
reason/symbols/lines/summary/excerpt, inspection order, related tests, risks, budget
summary).

### Stage 6 — MCP server ✅
`mcp/server.py` (`build_server` + `serve`, FastMCP stdio) + `mcp/tools.py`
(`ToolContext` keeps ONE Retriever so the model loads once). Tools: `repo_map,
repo_search, repo_symbol, repo_context_pack` (+`repo_prompt_context`),
`repo_find_tests, repo_reindex_changed`. CLI (`cli/main.py`) exposes the full surface;
stdout reconfigured to UTF-8 for Windows consoles.

### Stage 7 — Tests, dogfood, docs ✅
15 pytest tests over a fixture repo (offline `FakeEmbedder`): symbol extraction,
incremental skip, change/deletion, retrieval channels + merge guard, pack budget, MCP
registration. `docs/ARCHITECTURE.md` + this log.

## Verification evidence

- **Tests:** 15 passed (offline, deterministic).
- **Dogfood index:** `pandemonium index .` → scanned=57, indexed=57, symbols=221,
  chunks=321.
- **Acceptance:** `pandemonium context "where is the hybrid search merge implemented"
  --tokens 4000` → `hybrid_search.py` ranked #1, pack used ~2177/4000 tokens;
  `pandemonium symbol hybrid_search` → `hybrid_search.py:36-58`.
- **Incremental proof:** after editing 2 files, reindex reported `indexed=2 skipped=55`.
- **Dependency closure:** uninstalled `tree-sitter-language-pack`, `pip install -e .`
  re-resolved, 15 tests still green → declared deps are complete and self-sufficient.
- **MCP call-through:** `ToolContext.repo_search("hybrid search merge")` returns
  `hybrid_search.py` hits over the long-lived read-only Retriever.

## Known gaps (acceptable for MVP)

- Retrieval "reason" strings can be generic when a query token matches a common symbol
  name (e.g. `search`); ranking is still correct.
- Deferred per Part 2: notes system + trees, git-mode tracking + change-intelligence,
  dependency/relationship graph, FastAPI REST server, local-LLM summarizer, languages
  beyond Python, web UI. (Their SQLite schema is already created to avoid migrations.)
