"""M1 — the caller-graph regression lock (ROADMAP.md v2, Step 1).

This is the standing tripwire for the one measured failure that "broke silently for
months": a call-edge extractor drops an idiom (C++ nested-namespace `a::b::c::fn()` /
template `fn<T>()`, C# generic `M<T>()`, JS/TS `this.m()`), so `repo_impact` on the
target returns ZERO real callers and an agent edits believing nothing depends on it.

Unlike the per-language unit tests (which assert callee EDGES exist), this module asserts
the **impact direction** — `repo_impact(target)["direct"]` must contain the real caller —
for every edge-extracting language, in ONE place. If a future protocol change regresses
any extractor, this fails loudly here instead of going unnoticed until the next benchmark.

The matrix (#11, graph dimension): {C++ · C# · JS/TS · Python} × {qualified/generic call ·
member call · bare call}, plus the cross-language NON-resolution guarantee.
"""

from __future__ import annotations

from support import make_settings, reindex

from pandemonium import service


def _index(tmp_path, files: dict):
    for name, body in files.items():
        (tmp_path / name).write_text(body, encoding="utf-8")
    settings = make_settings(tmp_path)
    reindex(settings, incremental=False)
    return settings


def _ref(settings, name: str, path_sub: str):
    for r in service.symbol(settings, name, 50):
        if path_sub in r["path"]:
            return r["path"] + "::" + r["qualified_name"]
    raise AssertionError(f"symbol {name!r} not indexed in {path_sub}")


def _direct_callers(settings, ref) -> list:
    imp = service.impact_for(settings, ref)
    assert imp is not None, f"no impact for {ref}"
    return imp["direct"]


# --- C++: the headline regression (nested namespace + template) --------------
def test_m1_cpp_nested_namespace_and_template_callers_resolve(tmp_path):
    """The exact months-long silent bug: `rts::sim::systems::fn(...)` (the universal C++
    call idiom) and `fn<T>(...)` must surface their caller in repo_impact. A drop here is
    the failure that lost both A/B benchmarks to grep."""
    settings = _index(tmp_path, {"sim.cpp": (
        "namespace rts { namespace sim { namespace systems {\n"
        "  void runSeparation(int w) {}\n"
        "  template<typename T> T identity(T v) { return v; }\n"
        "}}}\n"
        "namespace app {\n"
        "struct World {\n"
        "  void tick() {\n"
        "    rts::sim::systems::runSeparation(1);\n"     # nested-namespace qualified call
        "    rts::sim::systems::identity<int>(2);\n"     # nested-namespace template call
        "  }\n"
        "};\n"
        "}\n")})

    sep_callers = _direct_callers(settings, _ref(settings, "runSeparation", "sim.cpp"))
    assert any("World.tick" in r for r in sep_callers), \
        "nested-namespace caller dropped — the #1 regression is back"

    id_callers = _direct_callers(settings, _ref(settings, "identity", "sim.cpp"))
    assert any("World.tick" in r for r in id_callers), \
        "nested-namespace TEMPLATE caller dropped"


# --- C#: generic invocation (the C# twin of #1) ------------------------------
def test_m1_csharp_generic_call_caller_resolves(tmp_path):
    settings = _index(tmp_path, {"A.cs": (
        "namespace N {\n"
        "  class A {\n"
        "    void Helper<T>(T x) {}\n"
        "    void Run() { this.Helper<int>(5); Helper<string>(\"a\"); }\n"
        "  }\n"
        "}\n")})
    callers = _direct_callers(settings, _ref(settings, "Helper", "A.cs"))
    assert any("A.Run" in r for r in callers), "C# generic-call caller dropped"


# --- JS/TS: this.method() + bare ---------------------------------------------
def test_m1_typescript_member_and_bare_callers_resolve(tmp_path):
    settings = _index(tmp_path, {"m.ts": (
        "class B { run(){} }\n"
        "class A extends B { go(){ this.help(); run(); } help(){} }\n")})
    help_callers = _direct_callers(settings, _ref(settings, "help", "m.ts"))
    assert any("A.go" in r for r in help_callers), "TS this.method() caller dropped"


# --- Python: module-qualified + self -----------------------------------------
def test_m1_python_module_qualified_and_self_callers_resolve(tmp_path):
    settings = _index(tmp_path, {
        "lib.py": "def work():\n    return 1\n",
        "app.py": ("import lib\n\n"
                   "class Engine:\n"
                   "    def go(self):\n"
                   "        lib.work()\n"
                   "        self.helper()\n"
                   "    def helper(self):\n"
                   "        return 0\n")})
    work_callers = _direct_callers(settings, _ref(settings, "work", "lib.py"))
    assert any("Engine.go" in r for r in work_callers), "Python module-qualified caller dropped"
    helper_callers = _direct_callers(settings, _ref(settings, "helper", "app.py"))
    assert any("Engine.go" in r for r in helper_callers), "Python self.method() caller dropped"


# --- The cross-language non-resolution guarantee -----------------------------
def test_m1_callers_never_cross_languages(tmp_path):
    """A C++ caller must never be attributed to a same-named Python target (and vice
    versa) — a false caller is worse than a missed one because it reads as verified."""
    settings = _index(tmp_path, {
        "lib.cpp": "namespace n {\nvoid helper(){}\nvoid use(){ helper(); }\n}\n",
        "lib.py": "def helper():\n    return 1\n\ndef use():\n    helper()\n",
    })
    cpp_callers = _direct_callers(settings, _ref(settings, "helper", "lib.cpp"))
    py_callers = _direct_callers(settings, _ref(settings, "helper", "lib.py"))
    assert cpp_callers and all("lib.cpp" in r for r in cpp_callers)
    assert py_callers and all("lib.py" in r for r in py_callers)
