"""Spec-driven tree-sitter symbol extractor (Phase 6: multi-language).

Each language is a small `LangSpec`: the grammar accessor + a map of definition
node-types -> (symbol_type, container?, class_like?, name strategy). One generic walk
handles them all:
  - container defs (class/struct/interface/namespace) push onto the qualified-name
    stack; class-like containers make their direct function members "method"s.
  - names come from the `name` field, except C++ functions whose name is nested under
    `function_declarator`.

Adding a language = one `LangSpec` entry + the grammar package. Lines are 1-based.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

_PARSER_CACHE: dict = {}


@dataclass
class ParsedSymbol:
    name: str
    qualified_name: str
    symbol_type: str  # class | struct | interface | namespace | function | method | property | type
    start_line: int
    end_line: int
    signature: str


@dataclass
class _Def:
    symbol_type: str
    container: bool = False   # pushes a qualified-name scope
    class_like: bool = False  # direct function members become methods
    name: str = "field"       # "field" -> child_by_field_name('name'); "cpp_func"


@dataclass
class _LangSpec:
    module: str
    attr: str
    defs: Dict[str, _Def]


_CLASS = _Def("class", container=True, class_like=True)
_STRUCT = _Def("struct", container=True, class_like=True)
_IFACE = _Def("interface", container=True, class_like=True)
_NS = _Def("namespace", container=True, class_like=False)
_FUNC = _Def("function")
_METHOD = _Def("method")
_PROP = _Def("property")

LANG_SPECS: Dict[str, _LangSpec] = {
    "python": _LangSpec("tree_sitter_python", "language", {
        "class_definition": _CLASS,
        "function_definition": _FUNC,  # reclassified to method inside a class
    }),
    "cpp": _LangSpec("tree_sitter_cpp", "language", {
        "class_specifier": _CLASS,
        "struct_specifier": _STRUCT,
        "namespace_definition": _NS,
        "function_definition": _Def("function", name="cpp_func"),
    }),
    "c_sharp": _LangSpec("tree_sitter_c_sharp", "language", {
        "namespace_declaration": _NS,
        "file_scoped_namespace_declaration": _NS,
        "class_declaration": _CLASS,
        "record_declaration": _CLASS,
        "struct_declaration": _STRUCT,
        "interface_declaration": _IFACE,
        "enum_declaration": _Def("enum"),        # named type (members not enumerated)
        "delegate_declaration": _Def("delegate"),  # named function-pointer type
        "method_declaration": _METHOD,
        "constructor_declaration": _METHOD,
        "property_declaration": _PROP,
    }),
    "javascript": _LangSpec("tree_sitter_javascript", "language", {
        "class_declaration": _CLASS,
        "function_declaration": _FUNC,
        "method_definition": _METHOD,
    }),
    "typescript": _LangSpec("tree_sitter_typescript", "language_typescript", {
        "class_declaration": _CLASS,
        "interface_declaration": _IFACE,
        "function_declaration": _FUNC,
        "method_definition": _METHOD,
        "method_signature": _METHOD,
        "type_alias_declaration": _Def("type"),
    }),
    "tsx": _LangSpec("tree_sitter_typescript", "language_tsx", {
        "class_declaration": _CLASS,
        "interface_declaration": _IFACE,
        "function_declaration": _FUNC,
        "method_definition": _METHOD,
        "method_signature": _METHOD,
        "type_alias_declaration": _Def("type"),
    }),
}


def _get_parser(language: str):
    if language not in _PARSER_CACHE:
        from tree_sitter import Language, Parser
        spec = LANG_SPECS[language]
        grammar = importlib.import_module(spec.module)
        _PARSER_CACHE[language] = Parser(Language(getattr(grammar, spec.attr)()))
    return _PARSER_CACHE[language]


def _text(src: bytes, node) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _first_line(src: bytes, node) -> str:
    blob = _text(src, node)
    first = blob.splitlines()[0] if blob else ""
    return first.strip()


def _find_descendant(node, node_type: str):
    for child in node.children:
        if child.type == node_type:
            return child
        found = _find_descendant(child, node_type)
        if found is not None:
            return found
    return None


def _extract_name(src: bytes, node, strategy: str) -> Optional[str]:
    if strategy == "cpp_func":
        decl = _find_descendant(node, "function_declarator")
        if decl is not None:
            nm = decl.child_by_field_name("declarator")
            if nm is not None:
                return _text(src, nm).strip()
        return None
    nm = node.child_by_field_name("name")
    return _text(src, nm) if nm is not None else None


def parse_symbols(source: bytes, language: str) -> List[ParsedSymbol]:
    """Extract symbols with line ranges. Unsupported languages return []."""
    if language not in LANG_SPECS:
        return []
    try:
        tree = _get_parser(language).parse(source)
    except Exception:
        return []

    spec = LANG_SPECS[language]
    symbols: List[ParsedSymbol] = []

    def walk(node, stack: List[str], in_class: bool) -> None:
        for child in node.children:
            d = spec.defs.get(child.type)
            if d is None:
                walk(child, stack, in_class)  # pass-through (bodies, blocks, ...)
                continue
            name = _extract_name(source, child, d.name) or "<anonymous>"
            qn = ".".join(stack + [name])
            stype = "method" if (d.symbol_type == "function" and in_class) else d.symbol_type
            symbols.append(ParsedSymbol(
                name, qn, stype, child.start_point[0] + 1, child.end_point[0] + 1,
                _first_line(source, child)))
            if d.container:
                walk(child, stack + [name], d.class_like)
            else:
                walk(child, stack + [name], False)  # nested defs are locals, not methods

    walk(tree.root_node, [], False)
    return symbols


# --- C++ header declaration doc-mining (Step 8: header↔cpp merge) -------------
# A C++ function is often DECLARED (with its Doxygen/`///` doc) in a `.hpp` and DEFINED
# out-of-line in a `.cpp` (`void World::queueDeath() {...}`), where the definition carries
# no comment. parse_symbols only emits the definition, so its embedded descriptor collapses
# to the bare signature and the header's doc — the real semantic signal — is lost. This pass
# mines a header's function DECLARATIONS for their doc comments WITHOUT emitting them as
# symbols (that would add by_name/by_qname collisions and regress resolve_call — see
# IMPROVEMENTS.md "Bigger bets"); the indexer merges the doc onto the matching definition.
_CPP_DECL_CONTAINERS = {"namespace_definition", "class_specifier", "struct_specifier",
                        "union_specifier"}
_CPP_DECL_NODES = ("declaration", "field_declaration")
_DOC_WINDOW = 12  # lines scanned above a declaration for its leading doc block


def cpp_decl_docs(source: bytes) -> Dict[str, Tuple[List[str], int]]:
    """Map each C++ function DECLARATION's canonical qualified name to
    (doc-window lines, 1-based declaration line). The canonical name is the
    namespace/class stack joined with '.' plus the leaf name, with any '::' normalized to
    '.', so it matches a `.cpp` definition's `qualified_name` after the same normalization
    (`Class::method` and a member decl under `class Class` both canonicalize identically).

    Declarations with a body (`function_definition`) are skipped — they are defined where
    they live and keep their own local doc; we never recurse into a definition body (its
    local `Widget w(x);` most-vexing-parse statements would otherwise leak as phantom
    declarations). Wrapper nodes (`template_declaration`, `linkage_specification`) are
    passed through so a templated / `extern "C"` declaration still reaches the handler. A
    NESTED type (a class/struct/union declared inside another class) is wrapped in a
    `field_declaration`/`declaration`, so we detect and recurse into it (pushing its name)
    rather than mis-reading its first method as a leaf decl of the outer scope.

    Overloaded names (>1 declaration sharing one canonical name — params are dropped, as on
    the definition side) are SUPPRESSED, not guessed: merging the first overload's doc/
    decl-site onto a different overload's definition would be confidently wrong, so those
    definitions degrade to the honest bare signature instead."""
    if "cpp" not in LANG_SPECS:
        return {}
    try:
        tree = _get_parser("cpp").parse(source)
    except Exception:
        return {}
    lines = source.decode("utf-8", "replace").splitlines()
    out: Dict[str, Tuple[List[str], int]] = {}
    ambiguous: set = set()

    def leaf_name(node) -> Optional[str]:
        fd = _find_descendant(node, "function_declarator")
        if fd is None:
            return None  # a variable / non-function declaration — ignore
        nm = fd.child_by_field_name("declarator")
        return _text(source, nm).strip() if nm is not None else None

    def walk(node, stack: List[str]) -> None:
        for child in node.children:
            t = child.type
            if t == "function_definition":
                continue  # has a body: not a header-only decl; do NOT recurse (vexing parse)
            if t in _CPP_DECL_CONTAINERS:
                nm = child.child_by_field_name("name")
                name = _text(source, nm) if nm is not None else "<anonymous>"
                walk(child, stack + [name])
                continue
            if t in _CPP_DECL_NODES:
                # A nested class/struct/union is wrapped in a field_declaration/declaration;
                # recurse into the type so its members mine as Outer.Inner.method.
                nested = next((c for c in child.children
                               if c.type in _CPP_DECL_CONTAINERS), None)
                if nested is not None:
                    nm = nested.child_by_field_name("name")
                    name = _text(source, nm) if nm is not None else "<anonymous>"
                    walk(nested, stack + [name])
                    continue
                name = leaf_name(child)
                if name:
                    qn = ".".join(stack + [name]).replace("::", ".")
                    if qn in out:
                        ambiguous.add(qn)  # overloaded -> suppress (see docstring)
                    else:
                        line0 = child.start_point[0]
                        out[qn] = (lines[max(0, line0 - _DOC_WINDOW):line0], line0 + 1)
                continue  # a (non-type) declaration holds no nested decls we track
            walk(child, stack)  # pass-through: template_declaration, linkage_specification, ...

    walk(tree.root_node, [])
    for qn in ambiguous:
        out.pop(qn, None)
    return out
