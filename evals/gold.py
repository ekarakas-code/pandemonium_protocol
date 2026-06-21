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

# Impact gold: the TRUE direct callers of a symbol, hand-authored from an exhaustive
# grep of the source (independent of repo_impact's own output, so impact FP/FN is a real
# comparison, not circular). Refs are `path::qualified_name`.
IMPACT_GOLD = [
    {"ref": "pandemonium/graph.py::GraphIndex.resolve_call",
     "true_direct": ["pandemonium/graph.py::_callees_of",
                     "pandemonium/graph.py::_callers_of"]},
    # Re-derived 2026-06-20 from an exhaustive grep of `is_test_path(` across pandemonium/
    # AND tests/ (the gold is independent of the tool). The earlier 3-entry list was stale:
    # it omitted the pre-existing graph._split_prod_test caller, find_tests, and the
    # test_graph unit test, and predates brief._partition_tests. All six below are real
    # call sites; repo_impact returns exactly these, so impact_fp/fn stay 0 by truth, not
    # by matching the tool.
    {"ref": "pandemonium/retrieval/tests_finder.py::is_test_path",
     "true_direct": ["pandemonium/retrieval/tests_finder.py::find_tests",
                     "pandemonium/graph.py::_plan_tests",
                     "pandemonium/graph.py::_split_prod_test",
                     "pandemonium/mapping.py::_build_tests",
                     "pandemonium/brief.py::_partition_tests",
                     "tests/test_graph.py::test_is_test_path_uses_word_boundaries_not_substring"]},
]
