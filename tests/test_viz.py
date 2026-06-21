"""Graph visualization overlays — prod/test split, edge evidence, confidence tiering.

Covers the data layer (`build_graph_data`) + the one render-time security fix (escaping
`<` so a `</script>` inside a summary/evidence string can't break the inlined script tag).
The cytoscape JS itself can't run headless; it is reviewed, not unit-tested.
"""

from __future__ import annotations

from support import make_settings, reindex

from pandemonium import viz


def _nodes_by(data, kind):
    return [n["data"] for n in data["nodes"] if n["data"]["kind"] == kind]


def _sym(data, label):
    return next(n["data"] for n in data["nodes"]
               if n["data"]["kind"] == "symbol" and n["data"]["label"] == label)


def test_overlays_in_graph_data(tmp_path):
    """A prod file (caller + callee + inheritance) and a sibling test file. The graph data
    must tag test-path nodes, carry per-edge evidence, and tier non-ambiguous calls."""
    (tmp_path / "prod.py").write_text(
        "class Animal:\n    pass\n\n"
        "class Dog(Animal):\n    pass\n\n"
        "def helper():\n    return 1\n\n"
        "def use():\n    return helper()\n",
        encoding="utf-8")
    (tmp_path / "test_prod.py").write_text(
        "from prod import use\n\n"
        "def test_use():\n    return use()\n",
        encoding="utf-8")
    settings = make_settings(tmp_path)
    reindex(settings, incremental=False)

    data = viz.build_graph_data(settings)
    stats = data["stats"]

    # --- prod/test split: every node carries a boolean test flag, path-derived ---
    files = {f["label"]: f for f in _nodes_by(data, "file")}
    assert files["prod.py"]["test"] is False
    assert files["test_prod.py"]["test"] is True
    assert _sym(data, "use")["test"] is False
    assert _sym(data, "test_use")["test"] is True
    assert stats["symbols_test"] >= 1 and stats["symbols_prod"] >= 1
    assert stats["symbols_test"] + stats["symbols_prod"] == stats["symbols_shown"]

    # --- every edge carries an evidence field + a stable etype ---
    assert data["edges"], "expected at least the call + inherit edges"
    for e in data["edges"]:
        assert "ev" in e["data"] and "etype" in e["data"]

    # --- confidence tiering: the real gradient produces BOTH tiers (not a dead toggle) ---
    id2label = {n["data"]["id"]: n["data"].get("label") for n in data["nodes"]}
    pairs = lambda es: [(id2label.get(e["data"]["source"]), id2label.get(e["data"]["target"]))
                        for e in es]
    confident = [e for e in data["edges"] if "confident" in e["classes"]]
    possible = [e for e in data["edges"] if "possible" in e["classes"]]
    # use()->helper(): bare call within prod.py -> resolved in caller's file (0.8) -> confident
    assert ("use", "helper") in pairs(confident)
    # test_use()->use(): use is imported (not defined in the test file) -> 0.6 name-only -> possible
    assert ("test_use", "use") in pairs(possible)
    assert all(e["data"]["etype"] == "calls" for e in confident + possible)

    # --- inherits edge carries a readable base-class evidence string ---
    inh = [e for e in data["edges"] if e["classes"] == "inherits"]
    assert inh and any("base class" in (e["data"]["ev"] or "") for e in inh)

    # --- additive stats: `calls` (CLI-stable) == confident + possible ---
    assert stats["calls"] == stats["calls_confident"] + stats["calls_possible"]
    assert {"calls", "calls_ambiguous", "inherits"} <= set(stats)  # CLI echo keys kept


def test_render_html_escapes_angle_brackets():
    """A `<` / `</script>` inside a summary must be escaped in the inlined JSON payload, or
    it breaks out of the <script> block and white-screens the page (real risk now that #5
    feeds C/Doxygen comment text into summaries)."""
    data = {
        "nodes": [{"data": {"id": "s1", "kind": "symbol", "label": "f",
                            "summary": "if a<b then </script> boom", "test": False}}],
        "edges": [],
        "areas": [{"name": "(root)", "color": "#888"}],
        "stats": {"repo": "x", "folders": 0, "files": 1, "symbols": 1, "symbols_shown": 1,
                  "symbols_test": 0, "calls": 0, "calls_confident": 0, "calls_possible": 0,
                  "calls_ambiguous": 0, "inherits": 0},
    }
    out = viz.render_html(data)
    assert "a\\u003cb" in out                       # the `<` was escaped in the payload
    assert "a<b then </script> boom" not in out     # raw injected markup never lands
