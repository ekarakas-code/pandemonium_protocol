"""Phase 6: multi-language symbol extraction + robustness matrix."""

from __future__ import annotations

from support import make_retriever, make_settings, reindex

from pandemonium import service
from pandemonium.indexer.tree_sitter_parser import parse_symbols


def _index(src, lang):
    syms = parse_symbols(src.encode("utf-8"), lang)
    return syms, {s.name: s for s in syms}, {(s.name, s.symbol_type) for s in syms}


def test_python_nested_is_function_not_method():
    src = "class A:\n    def m(self):\n        def inner():\n            return 1\n        return inner\n"
    syms, by, _ = _index(src, "python")
    assert by["A"].symbol_type == "class"
    assert by["m"].symbol_type == "method"
    assert by["inner"].symbol_type == "function"  # nested def is a local, not a method
    assert by["m"].start_line <= by["inner"].start_line <= by["m"].end_line


def test_cpp_namespace_class_method_and_free_function():
    src = (
        "namespace app {\n"
        "class Calculator {\n"
        "public:\n"
        "    int add(int a, int b) { return a + b; }\n"
        "};\n"
        "void run() {}\n"
        "template<typename T> T identity(T v) { return v; }\n"
        "}\n"
    )
    syms, _, names = _index(src, "cpp")
    qns = {s.qualified_name for s in syms}
    assert ("Calculator", "class") in names
    assert ("add", "method") in names          # name nested under function_declarator
    assert ("run", "function") in names         # free function (namespace is not class-like)
    assert ("identity", "function") in names    # template function
    assert "app.Calculator.add" in qns          # namespace + class in the qualified name


def test_csharp_class_interface_property():
    src = (
        "namespace App {\n"
        "  public class Calculator {\n"
        "    public int Add(int a, int b) { return a + b; }\n"
        "    public int Value { get; set; }\n"
        "  }\n"
        "  public interface IShape { double Area(); }\n"
        "}\n"
    )
    syms, _, names = _index(src, "c_sharp")
    qns = {s.qualified_name for s in syms}
    assert ("Calculator", "class") in names
    assert ("Add", "method") in names
    assert ("Value", "property") in names
    assert ("IShape", "interface") in names
    assert "App.Calculator.Add" in qns


def test_javascript_class_method_and_function():
    src = "class Calculator {\n  add(a, b) { return a + b; }\n}\nfunction run() {}\n"
    _, _, names = _index(src, "javascript")
    assert ("Calculator", "class") in names
    assert ("add", "method") in names
    assert ("run", "function") in names


def test_typescript_interface_class_type():
    src = (
        "interface IShape { area(): number; }\n"
        "class Calculator {\n  add(a: number, b: number): number { return a + b; }\n}\n"
        "function run(): void {}\n"
        "type Id = string;\n"
    )
    _, _, names = _index(src, "typescript")
    assert ("IShape", "interface") in names
    assert ("area", "method") in names
    assert ("Calculator", "class") in names
    assert ("add", "method") in names
    assert ("run", "function") in names
    assert ("Id", "type") in names


def test_unsupported_language_returns_empty():
    assert parse_symbols(b"SELECT 1;", "sql") == []


def test_multilang_end_to_end(tmp_path):
    """Index C++ and C# files, then resolve a C++ symbol by ref (live re-parse)."""
    (tmp_path / "calc.cpp").write_text(
        "namespace app {\nclass Calc {\npublic:\n  int add(int a, int b) { return a + b; }\n};\n}\n",
        encoding="utf-8")
    (tmp_path / "svc.cs").write_text(
        "namespace App { public class Svc { public int Run() { return 1; } } }",
        encoding="utf-8")
    settings = make_settings(tmp_path)
    reindex(settings, incremental=False)

    retriever = make_retriever(settings)
    try:
        results = retriever.search("add two integers")
    finally:
        retriever.close()
    assert any(r.path.endswith("calc.cpp") for r in results)

    # repo_get resolves a C++ method by path::qualified_name (re-parsed from disk).
    resolved = service.get(settings, "calc.cpp::app.Calc.add")
    assert resolved is not None
    assert "int add" in resolved.code


# --- Multi-language graph edges (Phase 9 extension) --------------------------
def _index_dir(tmp_path, files: dict):
    for name, body in files.items():
        (tmp_path / name).write_text(body, encoding="utf-8")
    settings = make_settings(tmp_path)
    reindex(settings, incremental=False)
    return settings


def _ref(settings, name: str, path_sub: str):
    for r in service.symbol(settings, name, 50):
        if path_sub in r["path"]:
            return r["path"] + "::" + r["qualified_name"]
    return None


def _callee_refs(settings, ref) -> set:
    g = service.graph_for(settings, ref)
    return {c["ref"] for c in g["callees"]} if g else set()


def test_cpp_graph_edges(tmp_path):
    """C++ call/import/inherit edges: bare, this->, Class:: (incl. out-of-line def)."""
    settings = _index_dir(tmp_path, {"shape.cpp": (
        '#include "shape.h"\n'
        "namespace geo {\n"
        "class Shape { public: void describe() { compute(); } void compute(); };\n"
        "class Circle : public Shape { public: double area() { return helper(); } };\n"
        "double helper() { return 1.0; }\n"
        "void Shape::compute() { geo::helper(); }\n"
        "}\n")})

    # bare compute() in describe -> the out-of-line geo.Shape::compute definition.
    describe = _ref(settings, "describe", "shape.cpp")
    assert any(r.endswith("geo.Shape::compute") for r in _callee_refs(settings, describe))

    # the out-of-line method itself resolves its qualified call geo::helper().
    compute = _ref(settings, "compute", "shape.cpp")
    assert any(r.endswith("geo.helper") for r in _callee_refs(settings, compute))

    # inheritance (base_class_clause) + #include import + edges flag.
    circle = service.graph_for(settings, _ref(settings, "Circle", "shape.cpp"))
    assert "Shape" in circle["inherits"]
    assert "shape" in circle["imports"]          # #include "shape.h" -> header stem
    assert circle["edges_available"] is True

    # Members include BOTH inline and out-of-line (Class::method) definitions.
    shape = service.graph_for(settings, _ref(settings, "Shape", "shape.cpp"))
    assert any(r.endswith("geo.Shape::compute") for r in shape["members"])  # out-of-line


def test_cpp_nested_namespace_and_template_calls(tmp_path):
    """#1: nested-namespace qualified calls (a::b::c::fn()) and template calls (fn<T>())
    must produce call edges. Before the fix, _callee_cpp dropped both — hiding the real
    production callers (the universal `rts::sim::systems::fn(...)` idiom resolved to 0)."""
    settings = _index_dir(tmp_path, {"sim.cpp": (
        "namespace rts { namespace sim { namespace systems {\n"
        "  void runSeparation(int w) {}\n"
        "  template<typename T> T identity(T v) { return v; }\n"
        "  void driver() { runSeparation(9); identity<int>(5); }\n"
        "}}}\n"
        "namespace app {\n"
        "struct World {\n"
        "  void tick() {\n"
        "    rts::sim::systems::runSeparation(1);\n"   # nested-namespace qualified call
        "    rts::sim::systems::identity<int>(2);\n"   # nested-namespace template call
        "  }\n"
        "};\n"
        "}\n")})

    tick_callees = _callee_refs(settings, _ref(settings, "tick", "sim.cpp"))
    assert any(r.endswith("rts.sim.systems.runSeparation") for r in tick_callees)
    assert any(r.endswith("rts.sim.systems.identity") for r in tick_callees)

    # A bare template call (identity<int>()) also resolves within the file.
    driver_callees = _callee_refs(settings, _ref(settings, "driver", "sim.cpp"))
    assert any(r.endswith("rts.sim.systems.identity") for r in driver_callees)

    # The headline direction: World::tick surfaces as a CONFIDENT caller of the
    # nested-namespace target (previously the impact map missed it entirely).
    imp = service.impact_for(settings, _ref(settings, "runSeparation", "sim.cpp"))
    assert any("World.tick" in r for r in imp["direct"])


def test_graph_resolution_is_language_scoped(tmp_path):
    """A C++ call must never resolve to a same-named Python symbol, and vice versa."""
    settings = _index_dir(tmp_path, {
        "lib.cpp": "namespace n {\nvoid helper(){}\nvoid use(){ helper(); }\n}\n",
        "lib.py": "def helper():\n    return 1\n\ndef use():\n    helper()\n",
    })
    cpp_imp = service.impact_for(settings, _ref(settings, "helper", "lib.cpp"))
    py_imp = service.impact_for(settings, _ref(settings, "helper", "lib.py"))
    # each helper's caller set stays entirely within its own language/file.
    assert cpp_imp["affected_files"] == ["lib.cpp"]
    assert py_imp["affected_files"] == ["lib.py"]
    assert cpp_imp["direct"] and all("lib.cpp" in r for r in cpp_imp["direct"])
    assert py_imp["direct"] and all("lib.py" in r for r in py_imp["direct"])


def test_csharp_graph_edges(tmp_path):
    settings = _index_dir(tmp_path, {"A.cs": (
        "using System.Text;\n"
        "namespace N { class B { public void Run(){} }\n"
        "class A : B { void M(){ this.Helper(); Run(); } void Helper(){} } }")})
    callees = _callee_refs(settings, _ref(settings, "M", "A.cs"))
    assert any(r.endswith("N.A.Helper") for r in callees)   # this.Helper()
    assert any(r.endswith("N.B.Run") for r in callees)      # bare Run() in same file
    a = service.graph_for(settings, _ref(settings, "A", "A.cs"))
    assert "B" in a["inherits"]
    assert any("System" in i for i in a["imports"])         # using System.Text;


def test_csharp_generic_calls_resolve(tmp_path):
    """C#: generic invocations — `M<T>()` (bare) and `this.M<T>()` (member) — now produce
    call edges to the bare method. Before the fix the bare generic was dropped and the
    member form carried a `M<T>` name that never resolved (the C# twin of the C++ #1 fix)."""
    settings = _index_dir(tmp_path, {"A.cs": (
        "namespace N {\n"
        "  class A {\n"
        "    void Helper<T>(T x) {}\n"
        "    void Run() { this.Helper<int>(5); Helper<string>(\"a\"); }\n"
        "  }\n"
        "}\n")})
    callees = _callee_refs(settings, _ref(settings, "Run", "A.cs"))
    assert any(r.endswith("N.A.Helper") for r in callees)


def test_csharp_enum_and_delegate_are_symbols(tmp_path):
    """C#: enums and delegates are extracted as named symbols (searchable / fetchable)."""
    settings = _index_dir(tmp_path, {"types.cs": (
        "namespace N {\n"
        "  enum Color { Red, Green }\n"
        "  delegate int Op(int a);\n"
        "}\n")})
    enum = service.symbol(settings, "Color")
    deleg = service.symbol(settings, "Op")
    assert any(s["type"] == "enum" and s["qualified_name"] == "N.Color" for s in enum)
    assert any(s["type"] == "delegate" and s["qualified_name"] == "N.Op" for s in deleg)


def test_dotnet_file_types_detected():
    """.NET project/markup/view files are recognized so they're indexed and searchable."""
    from pandemonium.indexer.language_detector import detect
    assert detect("App.csproj") == "xml"
    assert detect("Directory.Build.props") == "xml"
    assert detect("Index.razor") == "html"
    assert detect("Page.cshtml") == "html"
    assert detect("MainWindow.xaml") == "xml"
    assert detect("App.sln") == "text"


def test_js_ts_graph_edges(tmp_path):
    settings = _index_dir(tmp_path, {
        "m.ts": ("import {z} from './dep';\n"
                 "class B { run(){} }\n"
                 "class A extends B { go(){ this.help(); run(); } help(){} }"),
        "q.js": "class A extends Base { go(){ this.help(); } help(){} }",
    })
    cts = _callee_refs(settings, _ref(settings, "go", "m.ts"))
    assert any(r.endswith("A.help") for r in cts)           # this.help()
    assert any(r.endswith("B.run") for r in cts)            # bare run()
    ats = service.graph_for(settings, _ref(settings, "A", "m.ts"))
    assert "B" in ats["inherits"] and "dep" in ats["imports"]
    # JavaScript (extends + this.method()).
    assert any(r.endswith("A.help") for r in _callee_refs(settings, _ref(settings, "go", "q.js")))
    assert "Base" in service.graph_for(settings, _ref(settings, "A", "q.js"))["inherits"]
