"""Step 2 — retrieval confidence assessment + domain-coverage fan-out (ROADMAP v2).

The pure detector/re-ranker are tested with hand-built results (no embedder) so the logic
is locked deterministically; the integration test checks the plumbing (low-confidence ->
fan-out; exact-symbol fast path stays high and never fans out).
"""

from __future__ import annotations

from support import make_retriever, make_settings, reindex

from pandemonium.models import SearchResult
from pandemonium.retrieval import confidence


def _r(name, summary="", score=1.0, cid=None, tags=None):
    cid = cid or f"c_{name}_{score}"
    return SearchResult(chunk_id=cid, path=f"{name}.py", start_line=1, end_line=2,
                        score=score, symbol_id=cid, symbol_name=name, summary=summary,
                        ref=f"{name}.py::{name}", qualified_name=name, tags=tags)


# --- the detector ------------------------------------------------------------
def test_terms_splits_camel_and_snake():
    assert confidence.terms_of("cellSize") == {"cell", "size"}
    assert confidence.terms_of("get_cell_size") == {"get", "cell", "size"}
    assert "http" in confidence.terms_of("HTTPServer")


def test_single_term_query_is_always_high():
    a = confidence.assess("size", [_r("size"), _r("getSize")])
    assert a["confidence"] == "high"


def test_clustered_set_missing_domain_term_is_low():
    """The measured `.size()` failure: query asks for 'cell size', every top hit is a
    `size` accessor, and 'cell' is covered by none of them."""
    results = [_r("size", "return the number of elements", score=s)
               for s in (1.0, 0.9, 0.8, 0.7, 0.6)]
    a = confidence.assess("cell grid size", results)
    assert a["confidence"] == "low"
    assert a["clustered_on"] == "size"
    assert "cell" in a["missing_terms"] and "grid" in a["missing_terms"]


def test_diverse_covering_results_are_high():
    """A healthy result set: names are diverse and the query's domain terms are covered,
    so confidence stays high even though the query is multi-term."""
    results = [_r("CellGrid", "the spatial cell grid container"),
               _r("computeCellSize", "compute the size of a grid cell"),
               _r("Renderer", "draws the grid"),
               _r("World", "owns the cells"),
               _r("update", "advance the simulation")]
    a = confidence.assess("cell grid size", results)
    assert a["confidence"] == "high"


# --- the AND boundary (both guards are load-bearing) -------------------------
def test_name_cluster_but_all_terms_covered_stays_high():
    """ONE guard true (name cluster) but the other false (every query term covered) → NOT
    the failure pattern. Pins the `missing` guard: dropping it would resurrect the measured
    ~60% false-positive rate."""
    results = [_r("size", "the cell grid size value", score=s)
               for s in (1.0, 0.9, 0.8, 0.7, 0.6)]
    a = confidence.assess("cell grid size", results)
    assert a["confidence"] == "high" and not a["missing_terms"]


def test_missing_term_but_no_name_cluster_stays_high():
    """ONE guard true (a buried term) but the other false (diverse names) → NOT the failure
    pattern. Pins the `name_cluster` guard: dropping it would flag every multi-term query
    with any uncovered word."""
    results = [_r("Renderer", "draws things"), _r("World", "owns state"),
               _r("update", "advance one step"), _r("Engine", "runs the loop"),
               _r("Loader", "reads files")]
    a = confidence.assess("cell grid size", results)
    assert a["confidence"] == "high" and a["clustered_on"] is None
    assert "cell" in a["missing_terms"]  # the term IS missing — but no cluster, so high


# --- the re-ranker -----------------------------------------------------------
def test_rerank_promotes_domain_coverage_over_single_term_cluster():
    """A result covering both 'cell' and 'size' must overtake a strong `size`-only cluster
    after the coverage re-rank — the heart of the recovery."""
    cluster = [_r("size", "element count", score=1.0, cid=f"s{i}") for i in range(5)]
    target = _r("cellSize", "size of one grid cell", score=0.6, cid="target")
    ranked = confidence.rerank_by_coverage(["cell", "grid", "size"], cluster + [target])
    assert ranked[0].symbol_name == "cellSize"  # coverage beat the narrow cluster


def test_rerank_ties_break_deterministically_by_chunk_id():
    """Equal coverage AND equal score must order by chunk_id, so the recovery re-rank is
    itself reproducible across processes (same reason the merge needs a tiebreak)."""
    rs = [_r("a", "x", score=0.5, cid="c_b"), _r("b", "x", score=0.5, cid="c_a")]
    out = confidence.rerank_by_coverage(["foo"], rs)  # neither covers 'foo' -> a flat tie
    assert [r.chunk_id for r in out] == ["c_a", "c_b"]


# --- integration: plumbing ---------------------------------------------------
def test_exact_symbol_query_is_high_and_never_fans_out(tmp_path):
    (tmp_path / "m.py").write_text(
        "def computeStepFromVelocity(v):\n    return v * 2\n", encoding="utf-8")
    settings = make_settings(tmp_path)
    reindex(settings, incremental=False)
    retriever = make_retriever(settings)
    try:
        results, a = retriever.search_assessed("computeStepFromVelocity")
    finally:
        retriever.close()
    assert results and a["confidence"] == "high"
    assert "fanned_out" not in a  # the exact fast path must not fan out


def test_fanout_wiring_respects_low_verdict_and_gate(tmp_path, monkeypatch):
    """The fan-out WIRING: when the assessor returns low, `search_assessed` fans out
    per-term sub-queries and surfaces the recovered, re-ranked target — and the
    `auto_fanout` config gate disables it. The natural low-confidence trigger depends on
    real bge embeddings collapsing a compound query onto one token (the measured `.size()`
    failure); the offline fake embedder *rewards* multi-term matches and so can't reproduce
    a buried target — hence we force the verdict and assert the plumbing deterministically."""
    src = "".join(
        f"class C{i}:\n    def size(self):\n        '''element count'''\n        return {i}\n"
        for i in range(6))
    src += ("def cell_size(grid):\n"
            "    '''the spatial size of one grid cell'''\n    return grid * 2\n")
    (tmp_path / "m.py").write_text(src, encoding="utf-8")
    settings = make_settings(tmp_path)
    reindex(settings, incremental=False)

    from pandemonium.retrieval import hybrid_search

    def force_low(query, results):
        return {"confidence": "low", "query_terms": confidence.query_terms(query),
                "covered_terms": ["size"], "missing_terms": ["cell", "grid"],
                "clustered_on": "size", "reason": "forced for the wiring test"}

    monkeypatch.setattr(hybrid_search.confidence, "assess", force_low)
    retriever = make_retriever(settings)
    try:
        settings.data["retrieval"]["auto_fanout"] = True
        recovered, rec_a = retriever.search_assessed("cell grid size")
        assert rec_a.get("fanned_out") == ["cell", "grid", "size"]  # per-term fan-out ran
        assert any(r.symbol_name == "cell_size" for r in recovered)  # target recovered

        settings.data["retrieval"]["auto_fanout"] = False
        _, gated_a = retriever.search_assessed("cell grid size")
        assert "fanned_out" not in gated_a  # the gate disables fan-out
    finally:
        retriever.close()
