"""Phase 9: relationship graph — receiver-aware call resolution (the gate)."""

from __future__ import annotations

import json

from support import make_settings, reindex

from pandemonium import graph


def _has(items, suffix):
    return any(suffix in c["ref"] for c in items)


def test_call_resolution_is_receiver_aware(tmp_path):
    """Two same-named free functions; a class method calls each via its module. The
    graph must resolve each to the RIGHT module (not collapse the name collision), and
    callers must not be polluted with the other module's callers."""
    (tmp_path / "alpha.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (tmp_path / "beta.py").write_text("def run():\n    return 2\n", encoding="utf-8")
    (tmp_path / "main.py").write_text(
        "import alpha\nimport beta\n\n"
        "class Engine:\n"
        "    def go(self):\n"
        "        alpha.run()\n"
        "        beta.run()\n"
        "        self.helper()\n"
        "    def helper(self):\n"
        "        return 0\n",
        encoding="utf-8")
    settings = make_settings(tmp_path)
    reindex(settings, incremental=False)

    g = graph.repo_graph(settings, "main.py::Engine.go")
    assert g is not None
    assert _has(g["callees"], "alpha.py::run")            # receiver alpha. -> alpha module
    assert _has(g["callees"], "beta.py::run")             # receiver beta.  -> beta module
    assert _has(g["callees"], "main.py::Engine.helper")   # self.helper -> same class

    # Callers of alpha.run = Engine.go ONLY — NOT polluted by beta's callers.
    ga = graph.repo_graph(settings, "alpha.py::run")
    caller_refs = [c["ref"] for c in ga["callers"]]
    assert any("main.py::Engine.go" in r for r in caller_refs)
    assert not any("beta.py" in r for r in caller_refs)


def test_bare_call_same_stem_files_do_not_cross_resolve(tmp_path):
    """Two files share a stem (pkg_a/util.py, pkg_b/util.py), each with helper() + a bare
    caller. A bare call must resolve to its OWN file's helper, never the same-stem twin —
    the stem-collision silent-wrong-answer (confident, non-ambiguous WRONG edge)."""
    (tmp_path / "pkg_a").mkdir()
    (tmp_path / "pkg_b").mkdir()
    (tmp_path / "pkg_a" / "util.py").write_text(
        "def helper():\n    return 1\n\ndef use_a():\n    return helper()\n", encoding="utf-8")
    (tmp_path / "pkg_b" / "util.py").write_text(
        "def helper():\n    return 2\n\ndef use_b():\n    return helper()\n", encoding="utf-8")
    settings = make_settings(tmp_path)
    reindex(settings, incremental=False)

    a_callees = [c["ref"] for c in graph.repo_graph(settings, "pkg_a/util.py::use_a")["callees"]]
    assert any("pkg_a/util.py::helper" in r for r in a_callees)
    assert not any("pkg_b/util.py::helper" in r for r in a_callees)  # no cross-stem leak

    imp = graph.repo_impact(settings, "pkg_a/util.py::helper")
    assert imp["affected_files"] == ["pkg_a/util.py"]
    assert any("use_a" in r for r in imp["direct"])
    assert not any("use_b" in r for r in imp["direct"])  # false caller would be the bug


def test_imports_and_inheritance(tmp_path):
    (tmp_path / "m.py").write_text(
        "import os\nfrom collections import OrderedDict\n\n"
        "class Base:\n    pass\n\nclass Child(Base):\n    pass\n",
        encoding="utf-8")
    settings = make_settings(tmp_path)
    reindex(settings, incremental=False)

    child = graph.repo_graph(settings, "m.py::Child")
    assert "Base" in child["inherits"]
    assert "os" in child["imports"] and "OrderedDict" in child["imports"]


def test_impact_lists_callers(tmp_path):
    (tmp_path / "alpha.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (tmp_path / "main.py").write_text(
        "import alpha\n\nclass Engine:\n    def go(self):\n        alpha.run()\n",
        encoding="utf-8")
    settings = make_settings(tmp_path)
    reindex(settings, incremental=False)
    impact = graph.repo_impact(settings, "alpha.py::run")
    assert impact is not None
    assert any("main.py::Engine.go" in r for r in impact["direct"])
    assert any(p.endswith("main.py") for p in impact["affected_files"])


def test_edit_plan_composes_target_callers_tests(tmp_path):
    """edit_plan ranks the target, its direct callers, and a fetch order from the graph."""
    (tmp_path / "core.py").write_text("def validate(x):\n    return x > 0\n", encoding="utf-8")
    (tmp_path / "app.py").write_text(
        "import core\n\nclass Gate:\n    def check(self, v):\n        return core.validate(v)\n",
        encoding="utf-8")
    settings = make_settings(tmp_path)
    reindex(settings, incremental=False)

    p = graph.edit_plan(settings, "core.py::validate")
    assert p is not None
    assert p["primary"] == "core.py::validate"
    assert any("app.py::Gate.check" in r for r in p["callers_direct"])  # caller found
    assert p["fetch_order"][0] == "core.py::validate"  # target first
    assert any("app.py::Gate.check" in r for r in p["fetch_order"])
    assert "No tests found" in " ".join(p["risks"])  # non-test caller -> flagged
    assert "Edit plan: core.py::validate" in graph.render_edit_plan(p)


def test_edit_plan_counts_test_file_callers_as_tests(tmp_path):
    """Regression: a caller that lives in a test file counts as a test, so the plan does
    not claim 'no tests' while a test caller is listed elsewhere."""
    (tmp_path / "lib.py").write_text("def parse(s):\n    return s.strip()\n", encoding="utf-8")
    (tmp_path / "test_lib.py").write_text(
        "import lib\n\n\ndef test_parse():\n    assert lib.parse(' x ') == 'x'\n",
        encoding="utf-8")
    settings = make_settings(tmp_path)
    reindex(settings, incremental=False)
    p = graph.edit_plan(settings, "lib.py::parse")
    assert p is not None
    assert any("test_lib.py" in t for t in p["tests"])         # test caller recognized
    assert not any("No tests found" in r for r in p["risks"])  # so the risk is suppressed


def test_high_collision_callees_are_suppressed(tmp_path):
    """#8: a name-only call to an ultra-common method (8 same-named defs) collapses into
    one summary line instead of dumping every collision, while a low-collision ambiguous
    callee (2 defs) stays fully listed. The threshold is data-driven from by_name sizes."""
    src = "".join(f"class C{i}:\n    def size(self):\n        return {i}\n" for i in range(8))
    src += ("class T0:\n    def tag(self):\n        return 0\n"
            "class T1:\n    def tag(self):\n        return 1\n"
            "def use(obj):\n    obj.size()\n    obj.tag()\n    return 0\n")
    (tmp_path / "m.py").write_text(src, encoding="utf-8")
    settings = make_settings(tmp_path)
    reindex(settings, incremental=False)

    g = graph.repo_graph(settings, "m.py::use")
    assert g is not None
    sup = g["callees_suppressed"]
    assert sup is not None and sup["count"] == 8 and "size" in sup["examples"]
    assert all(c["name"] != "size" for c in g["callees_ambiguous"])   # size collapsed
    assert any(c["name"] == "tag" for c in g["callees_ambiguous"])    # tag (2 defs) kept
    rendered = graph.render_graph(g)
    assert "suppressed" in rendered and "size" in rendered


def test_is_test_path_uses_word_boundaries_not_substring():
    """#6: hardened is_test_path matches test tokens at word/camelCase boundaries, so
    `contest.cpp`/`latest.cpp`/`fastest.h` are no longer misclassified as tests."""
    from pandemonium.retrieval.tests_finder import is_test_path
    assert is_test_path("tests/test_foo.py")
    assert is_test_path("pkg/foo_test.cpp")
    assert is_test_path("src/CalculatorTests.cs")   # camelCase boundary
    assert is_test_path("a/foo.spec.ts")
    assert is_test_path("test/x.cpp")
    assert not is_test_path("src/contest.cpp")       # the old false positives
    assert not is_test_path("game/latest.cpp")
    assert not is_test_path("util/fastest.h")


def test_possible_callers_tier(tmp_path):
    """#6: a real-but-ambiguous caller (name collision) is surfaced as a 'possible' caller
    rather than silently discarded — honest residual the agent can grep to confirm."""
    (tmp_path / "m.py").write_text(
        "class A:\n    def helper(self):\n        return 1\n"
        "class B:\n    def helper(self):\n        return 2\n"
        "def use(obj):\n    return obj.helper()\n",
        encoding="utf-8")
    settings = make_settings(tmp_path)
    reindex(settings, incremental=False)
    g = graph.repo_graph(settings, "m.py::A.helper")
    assert not any("use" in c["ref"] for c in g["callers"])             # not confident
    assert any("use" in c["ref"] for c in g["callers_possible"])        # but possible
    imp = graph.repo_impact(settings, "m.py::A.helper")
    assert any("use" in r for r in imp["possible"])
    assert "Possible callers" in graph.render_graph(g)


def test_impact_splits_production_and_test_callers(tmp_path):
    """#6: direct callers split into Production vs Test, leading with Production."""
    (tmp_path / "core.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (tmp_path / "app.py").write_text(
        "import core\n\ndef main():\n    return core.run()\n", encoding="utf-8")
    (tmp_path / "test_core.py").write_text(
        "import core\n\ndef test_run():\n    assert core.run() == 1\n", encoding="utf-8")
    settings = make_settings(tmp_path)
    reindex(settings, incremental=False)
    imp = graph.repo_impact(settings, "core.py::run")
    assert any("app.py" in r for r in imp["direct_production"])
    assert any("test_core.py" in r for r in imp["direct_test"])
    assert not any("test_core.py" in r for r in imp["direct_production"])
    assert "Production" in graph.render_impact(imp)


def test_logic_map_and_affects_ingest(tmp_path):
    (tmp_path / "m.py").write_text("def a():\n    return 1\n\n\ndef b():\n    return 2\n",
                                   encoding="utf-8")
    settings = make_settings(tmp_path)
    reindex(settings, incremental=False)

    # repo_logic_map returns relevant symbols grouped
    lm = graph.repo_logic_map(settings, "returns a number")
    assert lm is not None and lm["files"]

    # affects ingest (LLM-inferred edges) surfaces in repo_graph, labeled as hypotheses
    shard = tmp_path / "_aff.json"
    shard.write_text(json.dumps([{"source": "m.py::a", "target": "m.py::b",
                                  "confidence": 0.6, "evidence": "a's value feeds b"}]),
                     encoding="utf-8")
    assert graph.ingest_affects(settings, [str(shard)]) == 1
    g = graph.repo_graph(settings, "m.py::a")
    assert any("m.py::b" in x["ref"] for x in g["affects"])
    assert "similar" in g  # vector-similarity section present


def test_affects_revalidation_after_target_edit(tmp_path):
    """An affects hypothesis is flagged needs_revalidation once the code it was inferred
    from changes — so stale LLM edges aren't silently reused as truth."""
    (tmp_path / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def b():\n    return 2\n", encoding="utf-8")
    settings = make_settings(tmp_path)
    reindex(settings, incremental=False)

    shard = tmp_path / "_aff.json"
    shard.write_text(json.dumps([{"source": "a.py::a", "target": "b.py::b",
                                  "confidence": 0.6, "evidence": "a feeds b"}]),
                     encoding="utf-8")
    assert graph.ingest_affects(settings, [str(shard)]) == 1
    aff = [x for x in graph.repo_graph(settings, "a.py::a")["affects"]
           if "b.py::b" in x["ref"]][0]
    assert aff["needs_revalidation"] is False  # fresh

    # Edit the TARGET only; incremental reindex keeps the edge (owned by a.py) but the
    # body it was inferred against has now changed.
    (tmp_path / "b.py").write_text("def b():\n    return 222\n", encoding="utf-8")
    reindex(settings, incremental=True)
    aff2 = [x for x in graph.repo_graph(settings, "a.py::a")["affects"]
            if "b.py::b" in x["ref"]][0]
    assert aff2["needs_revalidation"] is True
