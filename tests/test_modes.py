"""Step 6 context modes — the MECHANISM (weight-preset resolution + plumbing). These lock
that a mode re-ranks via weights only and is threaded end-to-end; they do NOT claim a mode
*helps* (that needs the #11 crossover matrix — modes ship as labelled hypotheses)."""

from __future__ import annotations

from support import make_retriever, make_settings, reindex


def test_weights_for_resolves_preset_default_and_unknown(indexed):
    r = make_retriever(indexed)
    try:
        default = r._weights_for(None)
        assert r._weights_for("impact")["symbol"] > default["symbol"]     # impact: + symbol
        assert r._weights_for("discovery")["vector"] > default["vector"]  # discovery: + vector
        assert r._weights_for("bugfix")["keyword"] > default["keyword"]   # bugfix: + keyword
        assert r._weights_for("") == default                              # empty -> default
        assert r._weights_for("nonsense-mode") == default                 # unknown -> default
    finally:
        r.close()


def test_partial_preset_merges_onto_default_not_zeroes(indexed):
    # A preset that overrides only ONE channel must keep the others from the default — a
    # bare replace would silently zero keyword/vector and mis-rank by omission.
    r = make_retriever(indexed)
    try:
        indexed.data["retrieval"]["modes"]["_partial"] = {"weights": {"symbol": 0.9}}
        default = r._weights_for(None)
        w = r._weights_for("_partial")
        assert w["symbol"] == 0.9
        assert w["keyword"] == default["keyword"]   # preserved, not zeroed
        assert w["vector"] == default["vector"]
    finally:
        r.close()


def test_search_with_mode_runs_and_returns_results(indexed):
    r = make_retriever(indexed)
    try:
        for mode in ("impact", "discovery", "bugfix", None):
            res = r.search("calculator add numbers", mode=mode)
            assert isinstance(res, list)  # plumbing works for every mode + default
    finally:
        r.close()


def test_mode_weights_reach_the_merge_scores(tmp_path):
    # symbolmatch.py defines a symbol literally named `validate`; semanticmatch.py carries
    # the words in its DOCSTRING under a different symbol name — so the candidates draw on
    # different channels. A two-word query avoids the exact-identifier short-circuit (which
    # ignores weights). The combined merge score = w_symbol*sym + w_keyword*kw + w_vector*vec,
    # so different presets MUST yield different combined scores — proving the preset reaches
    # the ranking math (the mechanism), without claiming either mode ranks "better".
    (tmp_path / "symbolmatch.py").write_text(
        "def validate(data):\n    return bool(data)\n", encoding="utf-8")
    (tmp_path / "semanticmatch.py").write_text(
        'def check_input(data):\n'
        '    """Validate and sanitize an incoming request payload before use."""\n'
        '    return data\n', encoding="utf-8")
    settings = make_settings(tmp_path)
    reindex(settings, incremental=False)
    r = make_retriever(settings)
    try:
        q = "validate request data"
        scores = {m: {x.ref: x.score for x in r.search(q, mode=m)}
                  for m in ("impact", "discovery", "bugfix")}
        assert all(scores.values())
        shared = set.intersection(*(set(s) for s in scores.values()))
        # At least one shared result scores differently across the three presets — the
        # weights demonstrably reach the linear merge (e.g. a keyword-only hit is 0.25 under
        # impact/discovery but 0.55 under bugfix).
        assert shared and any(len({scores[m][ref] for m in scores}) > 1 for ref in shared)
    finally:
        r.close()
