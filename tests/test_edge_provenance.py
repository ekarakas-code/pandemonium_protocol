"""Step 3 — edge provenance/confidence (tiered) + M2 grep-to-confirm (ROADMAP v2).

#9: every resolved edge carries an `evidence` string (WHY it resolved at its confidence),
surfaced inline only on request. M2: every UNVERIFIED edge (possible caller / ambiguous
callee) carries a one-shot `confirm` grep the agent can run as-is — the call graph closing
its own loop instead of relying on the agent to remember to verify.
"""

from __future__ import annotations

from support import make_settings, reindex

from pandemonium import graph, service


def _settings(tmp_path, files):
    for name, body in files.items():
        (tmp_path / name).write_text(body, encoding="utf-8")
    settings = make_settings(tmp_path)
    reindex(settings, incremental=False)
    return settings


def test_confident_edges_carry_evidence_and_no_confirm(tmp_path):
    """A confidently-resolved caller (module-qualified) and callee (self.method) each carry
    an evidence string explaining the resolution — and confident edges need no confirm."""
    settings = _settings(tmp_path, {
        "core.py": "def validate(x):\n    return x > 0\n",
        "app.py": ("import core\n\nclass Gate:\n"
                   "    def check(self, v):\n        return core.validate(v) or self.ok()\n"
                   "    def ok(self):\n        return True\n")})
    g = graph.repo_graph(settings, "app.py::Gate.check")
    assert any("validate" in c["ref"] for c in g["callees"])
    self_call = next(c for c in g["callees"] if c["ref"].endswith("Gate.ok"))
    assert "self" in self_call["evidence"] and "Gate" in self_call["evidence"]
    assert "confirm" not in self_call  # confident -> no grep offered

    gv = graph.repo_graph(settings, "core.py::validate")
    caller = next(c for c in gv["callers"] if "Gate.check" in c["ref"])
    assert "module 'core'" in caller["evidence"]
    assert "confirm" not in caller  # confident caller is verified


def test_possible_caller_carries_evidence_and_confirm(tmp_path):
    """A name-collision (ambiguous) caller is 'possible' and carries both an evidence string
    naming the collision AND a one-shot grep to confirm it (M2)."""
    settings = _settings(tmp_path, {"m.py": (
        "class A:\n    def helper(self):\n        return 1\n"
        "class B:\n    def helper(self):\n        return 2\n"
        "def use(obj):\n    return obj.helper()\n")})
    g = graph.repo_graph(settings, "m.py::A.helper")
    poss = next(c for c in g["callers_possible"] if "use" in c["ref"])
    assert "collides with 2" in poss["evidence"]
    assert poss["confirm"] == "grep -nF helper m.py"


def test_render_evidence_is_tiered(tmp_path):
    """Default render shows confidence + the M2 confirm for unverified edges, but NOT the
    evidence text; evidence appears only when show_evidence=True."""
    settings = _settings(tmp_path, {"m.py": (
        "class A:\n    def helper(self):\n        return 1\n"
        "class B:\n    def helper(self):\n        return 2\n"
        "def use(obj):\n    return obj.helper()\n")})
    g = graph.repo_graph(settings, "m.py::A.helper")
    default = graph.render_graph(g)
    full = graph.render_graph(g, show_evidence=True)
    assert "confirm: grep -nF" in default          # M2 offer is always present
    assert "collides with 2" not in default         # evidence hidden by default
    assert "collides with 2" in full                # ...shown on request


def test_impact_possible_callers_offer_confirm(tmp_path):
    settings = _settings(tmp_path, {"m.py": (
        "class A:\n    def helper(self):\n        return 1\n"
        "class B:\n    def helper(self):\n        return 2\n"
        "def use(obj):\n    return obj.helper()\n")})
    imp = service.impact_for(settings, "m.py::A.helper")
    assert imp["call_name"] == "helper"
    rendered = graph.render_impact(imp)
    assert "confirm: grep -nF helper m.py" in rendered


def _ref(settings, name, path_sub):
    for r in service.symbol(settings, name, 50):
        if path_sub in r["path"]:
            return r["path"] + "::" + r["qualified_name"]
    raise AssertionError(f"{name} not found in {path_sub}")


def test_confirm_grep_quotes_paths_with_spaces():
    """M2 commands are meant to be run as-is, so a path with spaces (common on Windows) must
    stay a single shell argument — otherwise grep silently mis-parses it."""
    assert graph._confirm_grep("helper", "m.py") == "grep -nF helper m.py"
    cmd = graph._confirm_grep("helper", "my dir/file.py")
    assert "'my dir/file.py'" in cmd  # spaced path quoted into one argument


def test_cpp_qualified_edge_evidence(tmp_path):
    """C++ nested-namespace call evidence names the qualified scope (provenance for the
    very edge whose silent drop M1 guards)."""
    settings = _settings(tmp_path, {"sim.cpp": (
        "namespace rts { namespace sim { namespace systems {\n"
        "  void runSeparation(int w) {}\n"
        "}}}\n"
        "namespace app {\nstruct World {\n"
        "  void tick() { rts::sim::systems::runSeparation(1); }\n"
        "};\n}\n")})
    g = graph.repo_graph(settings, _ref(settings, "tick", "sim.cpp"))
    callee = next(c for c in g["callees"] if c["ref"].endswith("runSeparation"))
    assert "systems::" in callee["evidence"] and "scope" in callee["evidence"]
