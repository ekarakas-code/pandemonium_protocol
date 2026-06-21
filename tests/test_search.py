"""Retrieval: channels, hybrid merge, and the normalization guard."""

from __future__ import annotations

from support import make_retriever, make_settings, reindex

from pandemonium.descriptor import build_descriptor
from pandemonium.models import SearchResult
from pandemonium.retrieval.hybrid_search import _dedup, hybrid_search


def test_descriptor_has_labeled_fields():
    d = build_descriptor("a/b.py", "symbol", "python", "C.m", "def m(self):", "Does X.",
                         {"search_terms": ["foo", "bar"], "domain": ["a"]})
    assert "Scope: symbol" in d
    assert "Qualified name: C.m" in d
    assert "Summary: Does X." in d
    assert "Search terms: foo, bar" in d


def _paths(results):
    return [r.path for r in results]


def test_symbol_query_resolves_exact_symbol(indexed):
    retriever = make_retriever(indexed)
    try:
        results = retriever.search("send_vendor_email")
    finally:
        retriever.close()
    assert results
    assert any("email_service.py" in p for p in _paths(results))
    assert any(r.symbol_name == "send_vendor_email" for r in results)


def test_natural_language_query_finds_relevant_file(indexed):
    retriever = make_retriever(indexed)
    try:
        results = retriever.search("send a vendor email after purchase order approval")
    finally:
        retriever.close()
    assert any("email_service.py" in p for p in _paths(results))


def test_keyword_query_finds_function(indexed):
    retriever = make_retriever(indexed)
    try:
        results = retriever.search("multiply two numbers")
    finally:
        retriever.close()
    assert any("calculator.py" in p for p in _paths(results))


def test_hybrid_merge_orders_by_weighted_score():
    channels = {
        "symbol": [("c1", 1.0), ("c2", 0.0)],
        "keyword": [("c2", 5.0)],
        "vector": [],
    }
    weights = {"symbol": 0.40, "keyword": 0.30, "vector": 0.30}
    merged = hybrid_search(channels, weights, 10)
    ids = [cid for cid, _, _ in merged]
    assert set(ids) == {"c1", "c2"}
    # c1: symbol 1.0*0.4 = 0.40 ; c2: symbol 0.0 + keyword (single -> 0.7)*0.3 = 0.21
    score = dict((cid, s) for cid, s, _ in merged)
    assert score["c1"] > score["c2"]


def test_hybrid_merge_ties_break_deterministically_by_chunk_id():
    """Equal combined scores must order by chunk_id, NOT by `all_ids` set-iteration order
    (PYTHONHASHSEED-dependent — it made the eval gate non-reproducible). The same tied pair
    fed in either input order must produce the same ranking."""
    weights = {"symbol": 0.40, "keyword": 0.30, "vector": 0.30}
    a = hybrid_search({"symbol": [("c_b", 0.5), ("c_a", 0.5)]}, weights, 10)
    b = hybrid_search({"symbol": [("c_a", 0.5), ("c_b", 0.5)]}, weights, 10)
    assert [cid for cid, _, _ in a] == ["c_a", "c_b"]            # chunk_id ascending on a tie
    assert [cid for cid, _, _ in a] == [cid for cid, _, _ in b]  # input-order independent


def test_normalization_guard_single_result_not_forced_to_one():
    # A lone hit in an UNCALIBRATED channel (keyword/vector) must not be normalized to
    # 1.0 (advisor guard) — it collapses to the degenerate present-but-not-maximal score.
    channels = {"symbol": [], "keyword": [("only", 42.0)], "vector": []}
    weights = {"symbol": 0.40, "keyword": 0.30, "vector": 0.30}
    merged = hybrid_search(channels, weights, 10)
    assert merged[0][0] == "only"
    assert abs(merged[0][1] - 0.30 * 0.7) < 1e-6


def test_symbol_channel_passed_through_identity():
    """#3: the calibrated symbol channel is NOT min-max-normalized — a lone exact match
    keeps its 1.0 and out-ranks a strong semantic near-match, instead of collapsing to
    the degenerate 0.7 and losing top-1 (the measured Benchmark-1 regression)."""
    channels = {
        "symbol": [("c1", 1.0)],                  # lone exact symbol hit
        "keyword": [],
        "vector": [("c2", 0.95), ("c3", 0.10)],   # a strong semantic near-match on c2
    }
    weights = {"symbol": 0.40, "keyword": 0.30, "vector": 0.30}
    score = {cid: s for cid, s, _ in hybrid_search(channels, weights, 10)}
    assert abs(score["c1"] - 0.40) < 1e-6   # identity: 0.40*1.0, NOT 0.40*0.7 = 0.28
    assert score["c1"] > score["c2"]        # exact symbol beats the vector near-match


def test_dedup_collapses_same_symbol_windows():
    """#4: the window-chunks of one large symbol share a symbol_id/ref (build_ref drops
    the window range), and the symbol channel expands a match to all of them. They must
    collapse to ONE card — killing the measured 'same ref 3× in top-3' — anchored at the
    signature window (the smallest start_line)."""
    common = dict(path="big.py", symbol_name="Big.run", chunk_type="method",
                  language="python", content="", summary="", ref="big.py::Big.run",
                  scope="symbol", qualified_name="Big.run", symbol_id="S")
    rows = [
        SearchResult(chunk_id="w2", start_line=61, end_line=120, score=0.9, **common),
        SearchResult(chunk_id="w1", start_line=1, end_line=60, score=0.9, **common),
        SearchResult(chunk_id="w3", start_line=110, end_line=160, score=0.9, **common),
    ]
    kept = _dedup(rows)
    assert len(kept) == 1
    assert kept[0].start_line == 1  # representative anchored at the signature window


def test_dedup_keeps_distinct_symbols_in_same_file():
    """The collapse only merges SAME-symbol windows; two different symbols (non-overlapping)
    in one file both survive."""
    base = dict(path="m.py", chunk_type="method", language="python", content="",
                summary="", scope="symbol")
    a = SearchResult(chunk_id="a", start_line=1, end_line=5, score=0.9, symbol_id="A",
                     symbol_name="A", ref="m.py::A", qualified_name="A", **base)
    b = SearchResult(chunk_id="b", start_line=20, end_line=25, score=0.8, symbol_id="B",
                     symbol_name="B", ref="m.py::B", qualified_name="B", **base)
    kept = _dedup([a, b])
    assert {r.ref for r in kept} == {"m.py::A", "m.py::B"}


def test_empty_channels_do_not_crash():
    assert hybrid_search({"symbol": [], "keyword": [], "vector": []},
                         {"symbol": 0.4, "keyword": 0.3, "vector": 0.3}, 10) == []


class _BoomEmbedder:
    """An embedder that explodes if used — proves the model never loads/runs."""
    dim = 64

    def embed_query(self, text):
        raise AssertionError("embedding model must not run on an exact short-circuit")

    def embed_documents(self, texts):
        raise AssertionError("embedding model must not run on an exact short-circuit")


def test_exact_short_circuit_returns_symbol_without_loading_model(indexed):
    """#7: a bare-identifier exact-symbol query returns the symbol card directly and never
    opens the vector store / loads the embedding model (the CLI per-search model-load tax)."""
    from pandemonium.retrieval.hybrid_search import Retriever
    retr = Retriever(indexed, embedder=_BoomEmbedder())
    try:
        results = retr.search("multiply")            # single exact-symbol token
        assert results and results[0].symbol_name == "multiply"
        assert results[0].score == 1.0 and results[0].scope == "symbol"
        assert results[0].ref and "calculator.py" in results[0].ref
        assert retr._lance is None                   # vector store never opened
    finally:
        retr.close()


def test_short_circuit_gate_off_falls_through_to_hybrid(indexed):
    """With the gate disabled, the same query runs the full hybrid path (vector channel
    forces the lazy vector-store open)."""
    indexed.data["retrieval"]["exact_short_circuit"] = False
    retr = make_retriever(indexed)                   # FakeEmbedder (no real model)
    try:
        results = retr.search("multiply")
        assert any(r.symbol_name == "multiply" for r in results)
        assert retr._lance is not None               # vector channel ran
    finally:
        retr.close()


def test_multiword_query_does_not_short_circuit(indexed):
    """A multi-word / semantic query is NOT short-circuited — it must use the full hybrid
    path so semantic recall is preserved."""
    retr = make_retriever(indexed)
    try:
        retr.search("multiply two numbers")
        assert retr._lance is not None               # vector channel ran (no short-circuit)
    finally:
        retr.close()


def test_scope_filter_restricts_results(repo):
    settings = make_settings(repo)
    settings.data["indexing"]["scopes"] = ["symbol", "file", "code"]  # emit all to filter
    reindex(settings, incremental=False)
    retriever = make_retriever(settings)
    try:
        sym = retriever.search("multiply two numbers",
                               chunk_types={"class", "method", "function"})
        files = retriever.search("multiply two numbers", chunk_types={"file"})
    finally:
        retriever.close()
    assert sym and all(r.scope == "symbol" for r in sym)
    assert all(r.scope == "file" for r in files)  # file-only filter -> only file cards
