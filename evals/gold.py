"""Labeled retrieval eval set over PandemoniumProtocol's own source.

Each item: a realistic agent query + the file(s)/symbol(s) that actually answer it
(authored from first-hand knowledge of this codebase). Used to measure precision@k,
MRR, and token efficiency before/after embedding changes. Paths are POSIX substrings.
"""

GOLD = [
    {"q": "where is the hybrid search merge implemented",
     "files": ["retrieval/hybrid_search.py"], "symbols": ["hybrid_search"]},
    {"q": "how are code embeddings generated with the model",
     "files": ["embeddings/local_embedder.py"], "symbols": ["LocalEmbedder", "embed_documents"]},
    {"q": "where is the sqlite database schema and tables defined",
     "files": ["storage/sqlite_store.py"], "symbols": ["SqliteStore"]},
    {"q": "how does incremental indexing detect changed files",
     "files": ["indexer/index_runner.py", "indexer/tracker.py"], "symbols": ["is_unchanged", "run"]},
    {"q": "where are functions and classes extracted from source with tree-sitter",
     "files": ["indexer/tree_sitter_parser.py"], "symbols": ["parse_symbols"]},
    {"q": "how is the token budget enforced when building a context pack",
     "files": ["retrieval/context_packer.py", "tokens/counter.py"], "symbols": ["ContextPacker", "build"]},
    {"q": "where are secret files and ignored directories filtered out",
     "files": ["indexer/ignore.py"], "symbols": ["IgnoreMatcher"]},
    {"q": "how does full text keyword search use fts5 bm25",
     "files": ["storage/fts_store.py", "retrieval/keyword_search.py"], "symbols": ["FtsStore"]},
    {"q": "where is the mcp server and its tools defined",
     "files": ["mcp/server.py", "mcp/tools.py"], "symbols": ["build_server", "ToolContext"]},
    {"q": "how are code chunks created including class headers and line windows",
     "files": ["indexer/chunker.py"], "symbols": ["build_chunks"]},
    {"q": "where are vectors stored and searched",
     "files": ["storage/lancedb_store.py", "retrieval/vector_search.py"], "symbols": ["LanceStore"]},
    {"q": "how does the command line index command work",
     "files": ["cli/main.py"], "symbols": ["index"]},
    {"q": "where is the embedding model name and dimension configured",
     "files": ["config/settings.py", "pandemonium.yaml"], "symbols": ["Settings", "DEFAULTS"]},
    {"q": "how are related tests found for a target symbol or file",
     "files": ["retrieval/tests_finder.py"], "symbols": ["find_tests"]},
    {"q": "where is the project repo map built from indexed files",
     "files": ["mapping.py"], "symbols": ["build_repo_map"]},
]

# C++ retrieval fixture gold (evals/fixtures/cpp_grid). The Python gold + FakeEmbedder
# CANNOT reproduce the measured T2 failure — a compound query ("cell size") whose real
# target is buried while a `.size()` family collapses to the top under the REAL bge model.
# This set measures Step 2's confidence-gate + fan-out against exactly that target, plus a
# control that must NOT over-fire. `target` is a ref suffix; refs are `path::Qualified.Name`.
CPP_FIXTURE_GOLD = [
    # The collapse: .size() accessors bury Grid.rescaleCellSize; fan-out must recover it.
    {"q": "cell size", "target": "Grid.rescaleCellSize", "expect_fanout": True},
    # Control: a genuine container-size query. The detector must stay high-confidence (no
    # spurious fan-out) and surface the right type at the top — here the RingBuffer struct,
    # which dedup keeps as the representative of its inner size() method.
    {"q": "ring buffer size", "target": "RingBuffer", "expect_fanout": False},
]

# C++ header->cpp merge fixture gold (evals/fixtures/cpp_header_merge). Step 8: a method
# DECLARED with its Doxygen doc in a header but DEFINED out-of-line in a .cpp (where the def
# has no comment) is invisible to a semantic query on the doc's words — its descriptor
# collapses to the bare signature. The merge moves the header doc onto the .cpp definition.
# The query words ("defer destruction ... frame ... complete") live ONLY in the header doc,
# never in the target's name/signature/.cpp body; decoy methods carry them in their NAMES so
# the target is buried WITHOUT the merge. `target` is a ref suffix (out-of-line defs keep the
# `Class::method` form). Measured OFF-vs-ON with the real model in `run_eval.py --cppmerge`.
CPP_MERGE_GOLD = [
    {"q": "defer an entity's destruction until the frame is complete",
     "target": "World::queueDeath"},
]

# Impact / edge gold: the TRUE direct callers of a symbol, hand-authored from an exhaustive
# grep of the source (independent of repo_impact's own output, so the FP/FN + precision/recall
# numbers are a real comparison, not circular). Refs are `path::qualified_name`.
#
# This is the relation/edge eval set (Improvements5 "relation evals" / ROADMAP Step 1 #11):
# it measures the CALLER (edit-impact) edge — the one that answers "what breaks if I change
# this" and "did the tool find the right edit site." Targets are chosen with UNIQUE names so
# cross-file bare calls resolve via the unique-name fallback (conf 0.6 == CALLER_MIN_CONFIDENCE,
# so they count as confident, not "possible") — i.e. the tool is expected to find ALL real
# callers, and any FP/FN is a real signal, not an artefact of name collision.
#
# Re-derived 2026-06-23 against the current tree (commit 3a306f9). Each entry's `true_direct`
# is the set of FUNCTIONS/METHODS that contain a call to the target, mapped from a fresh
# `grep '<name>('` across pandemonium/ + tests/ to its enclosing symbol. Module-level script
# call sites (e.g. _probe_stem.py) are excluded — callers are symbols, not loose statements.
IMPACT_GOLD = [
    {"ref": "pandemonium/graph.py::GraphIndex.resolve_call",
     "true_direct": ["pandemonium/graph.py::_callees_of",
                     "pandemonium/graph.py::_callers_of"]},
    {"ref": "pandemonium/graph.py::_callers_of",
     "true_direct": ["pandemonium/graph.py::repo_graph",
                     "pandemonium/graph.py::repo_impact"]},
    {"ref": "pandemonium/graph.py::_callees_of",
     "true_direct": ["pandemonium/brief.py::_call_flow",
                     "pandemonium/graph.py::repo_graph",
                     "pandemonium/graph.py::repo_logic_map",
                     "pandemonium/viz.py::build_graph_data"]},
    {"ref": "pandemonium/graph.py::_edges_available",
     "true_direct": ["pandemonium/graph.py::repo_graph",
                     "pandemonium/graph.py::repo_impact"]},
    {"ref": "pandemonium/graph.py::_affects_evidence_hash",
     "true_direct": ["pandemonium/graph.py::ingest_affects",
                     "pandemonium/graph.py::repo_graph"]},
    {"ref": "pandemonium/graph.py::_split_ambiguous_callees",
     "true_direct": ["pandemonium/graph.py::repo_graph"]},
    {"ref": "pandemonium/graph.py::_split_prod_test",
     "true_direct": ["pandemonium/graph.py::repo_impact",
                     "pandemonium/brief.py::_verified_block"]},
    {"ref": "pandemonium/graph.py::_resolve_target",
     "true_direct": ["pandemonium/brief.py::_call_flow",
                     "pandemonium/graph.py::repo_graph",
                     "pandemonium/graph.py::repo_impact",
                     "pandemonium/graph.py::repo_logic_map"]},
    {"ref": "pandemonium/graph.py::_plan_tests",
     "true_direct": ["pandemonium/graph.py::edit_plan"]},
    # Cross-file recall case + nested-function attribution. Re-derived 2026-06-23: the
    # 2026-06-20 list omitted the viz callers (added with the viz work). In viz.py the three
    # is_test_path calls live in NESTED helpers inside build_graph_data (ensure_dir_chain @127,
    # ensure_file @142, ensure_symbol @166 — verified by reading viz.py), so the caller refs
    # are the nested-qualified names, NOT the outer build_graph_data.
    {"ref": "pandemonium/retrieval/tests_finder.py::is_test_path",
     "true_direct": ["pandemonium/retrieval/tests_finder.py::find_tests",
                     "pandemonium/graph.py::_plan_tests",
                     "pandemonium/graph.py::_split_prod_test",
                     "pandemonium/mapping.py::_build_tests",
                     "pandemonium/brief.py::_partition_tests",
                     "pandemonium/viz.py::build_graph_data.ensure_dir_chain",
                     "pandemonium/viz.py::build_graph_data.ensure_file",
                     "pandemonium/viz.py::build_graph_data.ensure_symbol",
                     "tests/test_graph.py::test_is_test_path_uses_word_boundaries_not_substring"]},
]
