"""Step 8 — C++ header->cpp doc merge (offline, deterministic).

Two layers:
  * `cpp_decl_docs` unit tests — the header declaration doc-miner walks the namespace/class
    stack, ignores definitions (bodies) and the most-vexing-parse, and canonicalizes names
    so they meet a `.cpp` out-of-line definition's qualified_name.
  * end-to-end (FakeEmbedder) — indexing a header/cpp pair MERGES the header doc onto the
    out-of-line definition's stored summary and stamps a decl-site ref, gated by
    `indexing.cpp_header_merge`, and repo_get surfaces the decl-site.

The RETRIEVAL payoff (the doc lifts the buried def under the real model) is measured by
`evals/run_eval.py --cppmerge`; here we lock the deterministic mechanism only.
"""

from __future__ import annotations

from support import make_settings, reindex

from pandemonium import service
from pandemonium.indexer.tree_sitter_parser import cpp_decl_docs
from pandemonium.storage.sqlite_store import SqliteStore
from pandemonium.util import repo_id_for

_HPP = """\
#pragma once
namespace sim {
class World {
public:
    /// Defers an entity's destruction until the current frame has fully completed, so the
    /// systems iterating over the world are never mutated in the middle of a pass.
    void queueDeath(unsigned id);

    /// Number of entities currently alive.
    unsigned livingCount() const;

    int columns_;  // a member variable, not a function — must NOT become a decl-doc entry

    /// An inline-defined accessor: defined here, so its doc is already local.
    unsigned doomedCount() const { return 7; }
};
}  // namespace sim
"""

_CPP_NS_BLOCK = """\
#include "world.hpp"
namespace sim {
void World::queueDeath(unsigned id) {
    (void)id;
}
unsigned World::livingCount() const {
    return 0;
}
}  // namespace sim
"""

# Alternative definition style: fully-qualified out-of-line def, no enclosing namespace block.
_CPP_FULLY_QUALIFIED = """\
#include "world.hpp"
void sim::World::queueDeath(unsigned id) {
    (void)id;
}
"""

# A definition that carries its OWN doc comment (the def's doc must win over the header's).
_CPP_LOCAL_DOC = """\
#include "world.hpp"
namespace sim {
/// Locally documented: appends the id to the pending teardown buffer.
void World::queueDeath(unsigned id) {
    (void)id;
}
}  // namespace sim
"""


# --- cpp_decl_docs unit tests ------------------------------------------------
def test_decl_docs_class_method_canonical_name():
    docs = cpp_decl_docs(_HPP.encode("utf-8"))
    assert "sim.World.queueDeath" in docs           # namespace + class + leaf, '::'-normalized
    window, line = docs["sim.World.queueDeath"]
    assert any("Defers an entity's destruction" in ln for ln in window)
    assert isinstance(line, int) and line >= 1


def test_decl_docs_skips_member_variables_and_inline_definitions():
    docs = cpp_decl_docs(_HPP.encode("utf-8"))
    # A member variable has no function_declarator -> not a decl-doc entry.
    assert "sim.World.columns_" not in docs
    # An inline-DEFINED method is a function_definition (skipped: it keeps its local doc).
    assert "sim.World.doomedCount" not in docs


def test_decl_docs_free_and_template_functions():
    src = (
        "namespace util {\n"
        "/// Clamps a value into the inclusive range.\n"
        "int clamp(int v, int lo, int hi);\n"
        "/// Returns its argument unchanged.\n"
        "template<typename T> T identity(T v);\n"
        "}\n"
    ).encode("utf-8")
    docs = cpp_decl_docs(src)
    assert "util.clamp" in docs                      # free function declaration
    assert "util.identity" in docs                   # template_declaration wrapper passed through
    assert any("Clamps a value" in ln for ln in docs["util.clamp"][0])


def test_decl_docs_nested_class_keeps_full_path():
    """A class nested inside another class is wrapped in a field_declaration; its members
    must mine as Outer.Inner.method (full path), and a same-leaf OUTER method must NOT
    collide with the nested one (the wrong-doc hazard the review caught)."""
    src = (
        "class Outer {\n"
        "    class Inner {\n"
        "        /// Resets the inner cursor to the start.\n"
        "        void reset();\n"
        "    };\n"
        "    /// Resets the outer pipeline.\n"
        "    void reset();\n"
        "};\n"
    ).encode("utf-8")
    docs = cpp_decl_docs(src)
    assert "Outer.Inner.reset" in docs                # nested path preserved
    assert "Outer.reset" in docs                       # outer method distinct, no collision
    assert any("inner cursor" in ln for ln in docs["Outer.Inner.reset"][0])
    assert any("outer pipeline" in ln for ln in docs["Outer.reset"][0])


def test_decl_docs_overloads_are_suppressed():
    """Overloads collapse to one canonical name; rather than merge the first overload's doc
    onto a sibling overload's definition (confidently wrong), the name is SUPPRESSED."""
    src = (
        "namespace n {\n"
        "class C {\n"
        "    /// Adds one integer.\n"
        "    void add(int x);\n"
        "    /// Adds one floating value.\n"
        "    void add(double x);\n"
        "    /// The only declaration of clear.\n"
        "    void clear();\n"
        "};\n"
        "}\n"
    ).encode("utf-8")
    docs = cpp_decl_docs(src)
    assert "n.C.add" not in docs                        # overloaded -> suppressed, not guessed
    assert "n.C.clear" in docs                          # the unambiguous one still merges


def test_decl_docs_no_vexing_parse_phantoms():
    """A `Widget w(x);` inside an inline body parses as a declaration-with-function_declarator;
    because we never recurse into a function_definition, it must NOT leak as a phantom decl."""
    src = (
        "struct S {\n"
        "    void run() {\n"
        "        Widget w(make());\n"   # most-vexing-parse: looks like a function decl
        "    }\n"
        "};\n"
    ).encode("utf-8")
    docs = cpp_decl_docs(src)
    assert all(not k.endswith(".w") for k in docs)
    assert "S.run" not in docs                       # run() is a definition, skipped


# --- end-to-end merge --------------------------------------------------------
def _def_symbol(settings, qn_suffix: str) -> dict:
    """The stored symbol row whose normalized qualified_name ends with `qn_suffix`."""
    store = SqliteStore(settings.sqlite_path)
    store.create_schema()
    try:
        for r in store.all_symbols(repo_id_for(settings.repo_root)):
            if (r["qualified_name"] or "").replace("::", ".").endswith(qn_suffix):
                return dict(r)
    finally:
        store.close()
    return {}


def _pair(tmp_path, cpp_body=_CPP_NS_BLOCK):
    (tmp_path / "world.hpp").write_text(_HPP, encoding="utf-8")
    (tmp_path / "world.cpp").write_text(cpp_body, encoding="utf-8")
    return make_settings(tmp_path)


def test_merge_on_puts_header_doc_on_definition_summary(tmp_path):
    settings = _pair(tmp_path)
    reindex(settings, incremental=False)
    row = _def_symbol(settings, "World.queueDeath")
    assert row, "out-of-line definition symbol not indexed"
    # The header doc's distinctive words (absent from the .cpp) reached the def's summary.
    assert "destruction" in (row["summary"] or "")
    assert "frame" in (row["summary"] or "")
    # And the decl-site ref points back into the header.
    assert row["decl_ref"] and "world.hpp" in row["decl_ref"]


def test_merge_off_leaves_definition_bare(tmp_path):
    settings = _pair(tmp_path)
    settings.data["indexing"]["cpp_header_merge"] = False
    reindex(settings, incremental=False)
    row = _def_symbol(settings, "World.queueDeath")
    assert row
    assert "destruction" not in (row["summary"] or "")
    assert not row["decl_ref"]


def test_merge_works_for_fully_qualified_definition_style(tmp_path):
    """`void sim::World::queueDeath(...)` (no enclosing namespace block) canonicalizes to the
    same key as the class-member declaration, so the merge still fires."""
    settings = _pair(tmp_path, cpp_body=_CPP_FULLY_QUALIFIED)
    reindex(settings, incremental=False)
    row = _def_symbol(settings, "World.queueDeath")
    assert row
    assert "destruction" in (row["summary"] or "")
    assert row["decl_ref"] and "world.hpp" in row["decl_ref"]


def test_decl_ref_set_even_when_definition_has_local_doc(tmp_path):
    """The decl-site backlink is independent of whose doc won: a definition that carries its
    OWN doc keeps it (more specific) but STILL records decl_ref to the header declaration."""
    settings = _pair(tmp_path, cpp_body=_CPP_LOCAL_DOC)
    reindex(settings, incremental=False)
    row = _def_symbol(settings, "World.queueDeath")
    assert row
    assert "teardown buffer" in (row["summary"] or "")    # the def's own doc wins
    assert "destruction" not in (row["summary"] or "")     # header doc did NOT override it
    assert row["decl_ref"] and "world.hpp" in row["decl_ref"]  # backlink still recorded


def test_repo_get_surfaces_decl_ref(tmp_path):
    settings = _pair(tmp_path)
    reindex(settings, incremental=False)
    row = _def_symbol(settings, "World.queueDeath")
    ref = "world.cpp::" + row["qualified_name"]
    resolved = service.get(settings, ref)
    assert resolved is not None
    assert resolved.decl_ref and "world.hpp" in resolved.decl_ref


def test_mcp_repo_get_renders_declared_in_line(tmp_path):
    """The user-facing deliverable: the MCP repo_get OUTPUT actually shows the decl-site line
    (a typo in the render f-string/guard would pass every data-level assertion but not this)."""
    from pandemonium.mcp.tools import ToolContext
    from support import make_retriever
    settings = _pair(tmp_path)
    reindex(settings, incremental=False)
    row = _def_symbol(settings, "World.queueDeath")
    ctx = ToolContext(settings)
    ctx._retriever = make_retriever(settings)  # FakeEmbedder — no model load
    try:
        out = ctx.repo_get("world.cpp::" + row["qualified_name"])
    finally:
        if ctx._retriever is not None:
            ctx._retriever.close()
    assert "declared in" in out and "world.hpp" in out


def test_merge_nested_class_member_end_to_end(tmp_path):
    """The nested-class fix end-to-end: a member of a class nested in another class, defined
    out-of-line as Outer::Inner::method, still gets its header doc + decl_ref merged."""
    (tmp_path / "p.hpp").write_text(
        "#pragma once\n"
        "class Outer {\n"
        "    class Inner {\n"
        "        /// Rebuilds the spatial partition after a teleport event.\n"
        "        void rebuild();\n"
        "    };\n"
        "};\n", encoding="utf-8")
    (tmp_path / "p.cpp").write_text(
        "#include \"p.hpp\"\n"
        "void Outer::Inner::rebuild() {\n"
        "}\n", encoding="utf-8")
    settings = make_settings(tmp_path)
    reindex(settings, incremental=False)
    row = _def_symbol(settings, "Outer.Inner.rebuild")
    assert row, "nested out-of-line definition symbol not indexed"
    assert "spatial partition" in (row["summary"] or "")
    assert row["decl_ref"] and "p.hpp" in row["decl_ref"]


def test_merge_via_src_include_mirror(tmp_path):
    """The other common layout: src/foo.cpp <-> include/foo.hpp. The sibling lookup must find
    the header across the mirror, and the decl-site ref must name the include/ path."""
    (tmp_path / "src").mkdir()
    (tmp_path / "include").mkdir()
    (tmp_path / "include" / "world.hpp").write_text(_HPP, encoding="utf-8")
    (tmp_path / "src" / "world.cpp").write_text(_CPP_NS_BLOCK, encoding="utf-8")
    settings = make_settings(tmp_path)
    reindex(settings, incremental=False)
    row = _def_symbol(settings, "World.queueDeath")
    assert row
    assert "destruction" in (row["summary"] or "")
    assert row["decl_ref"] and "include/world.hpp" in row["decl_ref"]
