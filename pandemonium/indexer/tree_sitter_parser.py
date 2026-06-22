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
        "abstract_class_declaration": _CLASS,        # abstract class — was a silent drop
        "interface_declaration": _IFACE,
        "internal_module": _NS,                       # namespace X {}
        "module": _NS,                                # module X {}
        "enum_declaration": _Def("enum"),
        "function_declaration": _FUNC,
        "method_definition": _METHOD,
        "method_signature": _METHOD,
        "abstract_method_signature": _METHOD,
        "type_alias_declaration": _Def("type"),
    }),
    "tsx": _LangSpec("tree_sitter_typescript", "language_tsx", {
        "class_declaration": _CLASS,
        "abstract_class_declaration": _CLASS,
        "interface_declaration": _IFACE,
        "internal_module": _NS,
        "module": _NS,
        "enum_declaration": _Def("enum"),
        "function_declaration": _FUNC,
        "method_definition": _METHOD,
        "method_signature": _METHOD,
        "abstract_method_signature": _METHOD,
        "type_alias_declaration": _Def("type"),
    }),
}


# Grammars driven by a custom extractor (see _CUSTOM_PARSERS) rather than a LANG_SPECS
# generic walk — their node shapes don't fit the `name`-field/single-def-node model:
#   dart  — names nested in function_signature; method body is a *sibling* function_body;
#           calls are postfix selector chains (no call_expression node).
#   html  — no defs; anchors are elements carrying an `id`.
#   css   — anchors are rule_sets named by their selector text.
_EXTRA_GRAMMARS: Dict[str, Tuple[str, str]] = {
    "dart": ("tree_sitter_dart", "language"),
    "html": ("tree_sitter_html", "language"),
    "css": ("tree_sitter_css", "language"),
}


def _grammar_ref(language: str) -> Tuple[str, str]:
    if language in _EXTRA_GRAMMARS:
        return _EXTRA_GRAMMARS[language]
    spec = LANG_SPECS[language]
    return spec.module, spec.attr


def _get_parser(language: str):
    if language not in _PARSER_CACHE:
        from tree_sitter import Language, Parser
        module, attr = _grammar_ref(language)
        grammar = importlib.import_module(module)
        _PARSER_CACHE[language] = Parser(Language(getattr(grammar, attr)()))
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


# Body nodes a declaration's signature stops BEFORE (so the stored signature is the full
# declaration head — params + return type — not just the first physical line, and not the
# body). Covers the def-body node across all generic-walk languages.
_BODY_NODE_TYPES = {
    "block", "statement_block", "function_body", "compound_statement",
    "declaration_list", "field_declaration_list", "class_body", "enum_body",
    "enum_member_declaration_list", "interface_body", "object_type", "extension_body",
}


def _signature(src: bytes, node) -> str:
    """The declaration head: from the first non-attribute child up to the body, whitespace-
    collapsed. Skips leading C# `attribute_list`s (so `[HttpGet]` no longer clobbers the
    signature) and spans multi-line signatures (params/return type preserved)."""
    start = node.start_byte
    for c in node.children:
        if c.type in ("attribute_list", "comment", "line_comment", "block_comment"):
            start = c.end_byte
        else:
            break
    end = node.end_byte
    for c in node.children:
        if c.type in _BODY_NODE_TYPES:
            end = c.start_byte
            break
    sig = " ".join(src[start:end].decode("utf-8", "replace").split())
    return sig or _first_line(src, node)


def _js_value_func_symbols(src: bytes, node, stack: List[str], in_class: bool):
    """`const f = () => {}` / `const f = function(){}` — arrow/function-expression bound to a
    declarator. The dominant modern JS/TS definition style; the generic walk misses it
    because the function lives in a `variable_declarator` value, not a function_declaration."""
    out: List[ParsedSymbol] = []
    for d in node.children:
        if d.type != "variable_declarator":
            continue
        val = d.child_by_field_name("value")
        if val is None or val.type not in (
                "arrow_function", "function", "function_expression", "generator_function"):
            continue
        nm = d.child_by_field_name("name")
        if nm is None:
            continue
        name = _text(src, nm)
        body = val.child_by_field_name("body")
        sig_end = body.start_byte if body is not None else val.end_byte
        sig = " ".join(src[d.start_byte:sig_end].decode("utf-8", "replace").split())
        out.append(ParsedSymbol(name, ".".join(stack + [name]),
                                "method" if in_class else "function",
                                d.start_point[0] + 1, val.end_point[0] + 1, sig))
    return out


def _csharp_field_symbols(src: bytes, node, stack: List[str], symbol_type: str):
    """C# `field_declaration` / `event_field_declaration` -> one symbol per declarator
    (`int a, b;` -> `a` and `b`). Data members were entirely invisible before."""
    out: List[ParsedSymbol] = []
    vd = _find_descendant(node, "variable_declaration")
    if vd is None:
        return out
    sig = _signature(src, node)
    for c in vd.children:
        if c.type == "variable_declarator":
            nm = _child_of_type(c, "identifier")
            if nm is not None:
                name = _text(src, nm)
                out.append(ParsedSymbol(name, ".".join(stack + [name]), symbol_type,
                                        node.start_point[0] + 1, node.end_point[0] + 1, sig))
    return out


def _py_assignment_symbols(src: bytes, node):
    """Module-level `MAX = 10` / `CONFIG: dict = {}` / `Vec = list[int]` / `T = TypeVar(...)`
    -> constant | variable | type symbols. Python's module data/type-alias API surface was
    silently dropped (no def node-type)."""
    out: List[ParsedSymbol] = []
    asg = _child_of_type(node, "assignment")
    if asg is None:
        return out
    left = asg.child_by_field_name("left")
    if left is None or left.type != "identifier":
        return out  # skip tuple/attribute/subscript targets
    name = _text(src, left)
    right = asg.child_by_field_name("right")
    rt = right.type if right is not None else ""
    if rt == "subscript" or (rt == "call" and _text(src, right).split("(")[0].rsplit(".", 1)[-1]
                             in ("TypeVar", "NewType", "ParamSpec", "TypeAlias")):
        stype = "type"
    elif name.isupper():
        stype = "constant"
    else:
        stype = "variable"
    sig = " ".join(_text(src, node).split())
    out.append(ParsedSymbol(name, name, stype, node.start_point[0] + 1,
                            node.end_point[0] + 1, sig[:160]))
    return out


def parse_symbols(source: bytes, language: str) -> List[ParsedSymbol]:
    """Extract symbols with line ranges. Unsupported languages return []."""
    custom = _CUSTOM_PARSERS.get(language)
    if custom is not None:
        try:
            return custom(source)
        except Exception:
            return []
    if language not in LANG_SPECS:
        return []
    try:
        tree = _get_parser(language).parse(source)
    except Exception:
        return []

    spec = LANG_SPECS[language]
    symbols: List[ParsedSymbol] = []
    is_js = language in ("javascript", "typescript", "tsx")

    def walk(node, stack: List[str], in_class: bool) -> None:
        for child in node.children:
            t = child.type

            # Python: decorated def/class — emit the inner def but span the decorators
            # (start line) and surface them in the signature (@property/@staticmethod/...).
            if language == "python" and t == "decorated_definition":
                inner = _child_of_type(child, "function_definition", "class_definition")
                d = spec.defs.get(inner.type) if inner is not None else None
                if d is not None:
                    name = _extract_name(source, inner, d.name) or "<anonymous>"
                    stype = "method" if (d.symbol_type == "function" and in_class) else d.symbol_type
                    body = _child_of_type(inner, *_BODY_NODE_TYPES)
                    sig_end = body.start_byte if body is not None else inner.end_byte
                    sig = " ".join(source[child.start_byte:sig_end].decode("utf-8", "replace").split())
                    symbols.append(ParsedSymbol(name, ".".join(stack + [name]), stype,
                                                child.start_point[0] + 1, inner.end_point[0] + 1, sig))
                    walk(inner, stack + [name], d.class_like if d.container else False)
                    continue
                walk(child, stack, in_class)
                continue

            # JS/TS: arrow / function-expression bound to const/let (the modern style).
            if is_js and t in ("lexical_declaration", "variable_declaration"):
                vfs = _js_value_func_symbols(source, child, stack, in_class)
                symbols.extend(vfs)
                if not vfs:
                    walk(child, stack, in_class)
                continue

            # C#: fields and events — one symbol per declarator.
            if language == "c_sharp" and t in ("field_declaration", "event_field_declaration"):
                symbols.extend(_csharp_field_symbols(
                    source, child, stack, "event" if t.startswith("event") else "field"))
                continue

            # Python: module-level constants / variables / type aliases.
            if language == "python" and not stack and not in_class and t == "expression_statement":
                pas = _py_assignment_symbols(source, child)
                if pas:
                    symbols.extend(pas)
                    continue

            d = spec.defs.get(t)
            if d is None:
                walk(child, stack, in_class)  # pass-through (bodies, blocks, ...)
                continue
            name = _extract_name(source, child, d.name) or "<anonymous>"
            stype = "method" if (d.symbol_type == "function" and in_class) else d.symbol_type
            symbols.append(ParsedSymbol(
                name, ".".join(stack + [name]), stype,
                child.start_point[0] + 1, child.end_point[0] + 1, _signature(source, child)))
            walk(child, stack + [name], d.class_like if d.container else False)

    walk(tree.root_node, [], False)
    return symbols


# --- Custom extractors for irregular grammars (dart / html / css) ------------
def _child_of_type(node, *types):
    for c in node.children:
        if c.type in types:
            return c
    return None


def _named_field(src: bytes, node) -> Optional[str]:
    nm = node.child_by_field_name("name")
    return _text(src, nm) if nm is not None else None


def _dart_sig_name(src: bytes, node) -> Optional[str]:
    """Name for a Dart *_signature node. Joins its identifier children (field `name` OR
    unnamed) with '.': plain `m`->'m', named ctor `P.named`->'P.named', and factory
    `factory P.f`->'P.f' (whose identifiers carry NO `name` field). Return types are
    `type_identifier`/`void_type`, not `identifier`, so they're excluded. None = no name
    (e.g. operator signatures), which the caller drops."""
    parts: List[str] = []
    for i, c in enumerate(node.children):
        if c.type == "identifier" and node.field_name_for_child(i) in ("name", None):
            parts.append(_text(src, c))
    return ".".join(parts) if parts else None


_DART_SIG = ("function_signature", "getter_signature", "setter_signature",
             "constructor_signature", "factory_constructor_signature",
             "constant_constructor_signature", "operator_signature")
_DART_CTOR = ("constructor_signature", "factory_constructor_signature",
              "constant_constructor_signature")
_DART_CONTAINERS = {"class_definition": "class", "mixin_declaration": "mixin",
                    "extension_declaration": "extension", "enum_declaration": "enum"}


def _dart_field_names(src: bytes, node) -> List[str]:
    """Variable/field names under a (possibly nested) declaration: the identifiers inside
    initialized_identifier / static_final_declaration leaves."""
    out: List[str] = []

    def rec(n):
        if n.type in ("initialized_identifier", "static_final_declaration"):
            idc = _child_of_type(n, "identifier")
            if idc is not None:
                out.append(_text(src, idc))
            return
        for c in n.children:
            rec(c)

    rec(node)
    return out


def _parse_dart(source: bytes) -> List[ParsedSymbol]:
    """Dart symbols. Containers: class / mixin / extension / enum (members scoped under them).
    Functions/methods/ctors/getters/setters/operators come from a `*_signature` node; its body
    is the FOLLOWING sibling `function_body` (range end). Also emits top-level vars/consts,
    class fields, typedefs, enum constants. Bodies aren't recursed (Dart locals are omitted)."""
    tree = _get_parser("dart").parse(source)
    symbols: List[ParsedSymbol] = []

    def emit_func(sig, body, stack: List[str], in_class: bool) -> None:
        name = _dart_sig_name(source, sig)
        if not name and sig.type == "operator_signature":
            txt = _text(source, sig)
            after = txt.split("operator", 1)[1].split("(")[0].strip() if "operator" in txt else ""
            name = ("operator" + after) if after else None
        if not name:
            return
        end = (body.end_point[0] + 1) if body is not None else sig.end_point[0] + 1
        stype = "method" if in_class else "function"
        if sig.type in _DART_CTOR:
            # name is "Class" (default) or "Class.named"/"Class.f"; qn = enclosing class + the
            # ctor tail so we get Class.Class / Class.named, never the doubled Class.Class.named.
            head, _, tail = name.partition(".")
            ctor = tail or head
            qn = ".".join(stack + [ctor])
            name = ctor
        else:
            qn = ".".join(stack + [name])
        symbols.append(ParsedSymbol(name, qn, stype, sig.start_point[0] + 1, end,
                                    _first_line(source, sig)))

    def emit(name, qn, stype, node):
        symbols.append(ParsedSymbol(name, qn, stype, node.start_point[0] + 1,
                                    node.end_point[0] + 1, _first_line(source, node)))

    def walk(node, stack: List[str], in_class: bool) -> None:
        kids = node.children
        for i, child in enumerate(kids):
            t = child.type
            if t in _DART_CONTAINERS:
                name = (_named_field(source, child)
                        or (_child_of_type(child, "identifier") is not None
                            and _text(source, _child_of_type(child, "identifier")))
                        or "<anonymous>")
                emit(name, ".".join(stack + [name]), _DART_CONTAINERS[t], child)
                body = _child_of_type(child, "class_body", "extension_body", "enum_body")
                if body is not None:
                    walk(body, stack + [name], True)
            elif t == "type_alias":
                ti = _child_of_type(child, "type_identifier")
                if ti is not None:
                    nm = _text(source, ti)
                    emit(nm, ".".join(stack + [nm]), "type", child)
            elif t == "static_final_declaration_list":   # top-level const/final
                for nm in _dart_field_names(source, child):
                    emit(nm, ".".join(stack + [nm]), "field" if in_class else "constant", child)
            elif t == "initialized_identifier_list":      # top-level var
                for nm in _dart_field_names(source, child):
                    emit(nm, ".".join(stack + [nm]), "field" if in_class else "variable", child)
            elif t == "declaration" and in_class:          # class field(s) OR a wrapped ctor
                fnames = _dart_field_names(source, child)
                if fnames:
                    for nm in fnames:
                        emit(nm, ".".join(stack + [nm]), "field", child)
                else:
                    walk(child, stack, in_class)  # e.g. constructor_signature wrapped in a declaration
            elif t == "enum_constant":
                idc = _child_of_type(child, "identifier")
                if idc is not None:
                    nm = _text(source, idc)
                    emit(nm, ".".join(stack + [nm]), "constant", child)
            elif t == "method_signature":
                sig = _child_of_type(child, *_DART_SIG) or child
                nxt = kids[i + 1] if i + 1 < len(kids) else None
                body = nxt if (nxt is not None and nxt.type == "function_body") else None
                emit_func(sig, body, stack, in_class)
            elif t in _DART_SIG:
                nxt = kids[i + 1] if i + 1 < len(kids) else None
                body = nxt if (nxt is not None and nxt.type == "function_body") else None
                emit_func(child, body, stack, in_class)
            else:
                walk(child, stack, in_class)

    walk(tree.root_node, [], False)
    return symbols


def _parse_html(source: bytes) -> List[ParsedSymbol]:
    """HTML anchors: one symbol per element carrying an `id` attribute, named `#<id>` so it
    is searchable/fetchable. Other elements stay text-only (no structural symbol)."""
    tree = _get_parser("html").parse(source)
    symbols: List[ParsedSymbol] = []

    def walk(node) -> None:
        if node.type == "element":
            tag = _child_of_type(node, "start_tag", "self_closing_tag")
            if tag is not None:
                tname_node = _child_of_type(tag, "tag_name")
                tname = _text(source, tname_node) if tname_node is not None else "element"
                for attr in tag.children:
                    if attr.type != "attribute":
                        continue
                    an = _child_of_type(attr, "attribute_name")
                    if an is not None and _text(source, an).lower() == "id":
                        qv = _child_of_type(attr, "quoted_attribute_value", "attribute_value")
                        if qv is not None:
                            idv = _text(source, qv).strip().strip('"').strip("'")
                            if idv:
                                ref = f"#{idv}"
                                symbols.append(ParsedSymbol(
                                    ref, ref, "element", node.start_point[0] + 1,
                                    node.end_point[0] + 1, f'<{tname} id="{idv}">'))
                        break
        for c in node.children:
            walk(c)

    walk(tree.root_node)
    return symbols


def _parse_css(source: bytes) -> List[ParsedSymbol]:
    """CSS anchors: one symbol per rule_set, named by its (whitespace-normalized) selector
    text (e.g. `.box`, `#main a:hover`). Searchable/fetchable; no graph edges."""
    tree = _get_parser("css").parse(source)
    symbols: List[ParsedSymbol] = []

    def walk(node) -> None:
        if node.type == "rule_set":
            sels = _child_of_type(node, "selectors")
            name = " ".join(_text(source, sels).split()) if sels is not None else "<rule>"
            if name:
                symbols.append(ParsedSymbol(name, name, "rule", node.start_point[0] + 1,
                                            node.end_point[0] + 1, name))
        for c in node.children:
            walk(c)

    walk(tree.root_node)
    return symbols


_CUSTOM_PARSERS = {"dart": _parse_dart, "html": _parse_html, "css": _parse_css}


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
