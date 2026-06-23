"""Relationship graph (Phase 9) — "related code".

Edges are extracted per file at index time and stored UNRESOLVED in the `relationships`
table: `(source_id, relationship_type, target_name, receiver, confidence)`. Names are
resolved to symbol_ids at QUERY time (see `repo_graph`), so each file's edges are
self-contained — reindexing one file never invalidates another's.

Only edges you can't derive are persisted: `calls`, `imports`, `inherits`. (`contains`
comes from `symbols.file_id`/`parent`; `tested_by` from `find_tests`.) Call edges carry
a receiver (`self` / a name / an expr) so the query-time resolver can disambiguate the
many same-named methods (e.g. six `.search`es in this repo) instead of name-colliding.

Extraction is per-language (see EDGE_SPECS + the Dart custom extractor): Python, C++, C#,
Dart, and JS/TS each contribute
their call/import/inherit node-types. Resolution is language-scoped — a call in one
language never resolves to a same-named symbol in another. Languages without a spec index
symbols normally but carry no edges (surfaced via `edges_available`).
"""

from __future__ import annotations

import hashlib
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from pandemonium.indexer import tree_sitter_parser as tsp
from pandemonium.indexer.language_detector import detect, is_parseable
from pandemonium.util import now_iso, sha256_text

CALLER_MIN_CONFIDENCE = 0.6  # below this, a call is too ambiguous to claim a specific caller

# Confidence by how well the receiver pins the callee (Python receiver kinds).
_CALL_CONF = {"self": 0.9, "name": 0.65, "expr": 0.5, "bare": 0.6}


def _edges_available(path: Optional[str]) -> bool:
    """Static call/import/inherit edges are extracted only for languages with an
    EDGE_SPECS entry (see extract_edges, which returns [] otherwise). Symbols still
    resolve in every language, but the graph is empty for languages without a spec.
    Surfaced so the agent never reads an empty graph/impact as 'nothing depends on this'."""
    return bool(path) and detect(path) in EDGE_LANGUAGES


def _eid(*parts: str) -> str:
    return "rel_" + hashlib.sha1("\x00".join(parts).encode("utf-8", "replace")).hexdigest()[:16]


def _row(repo_id, file_id, source_type, source_id, rel, target_name,
         receiver="", target_type="", confidence=1.0, origin="static",
         evidence=None, evidence_hash=None, created_at=None) -> dict:
    return {
        "id": _eid(source_id, rel, target_name, receiver),
        "repo_id": repo_id, "file_id": file_id, "source_type": source_type,
        "source_id": source_id, "relationship_type": rel, "target_type": target_type,
        "target_id": None, "target_name": target_name, "receiver": receiver,
        "confidence": confidence, "origin": origin, "evidence": evidence,
        "evidence_hash": evidence_hash, "created_at": created_at,
    }


def _affects_evidence_hash(src_hash: Optional[str], tgt_hash: Optional[str]) -> str:
    """Hash of the source+target span content. Stored when an `affects` hypothesis is
    ingested; if it differs from the current indexed hashes, the code changed since the
    hypothesis was formed and the edge needs revalidation."""
    return sha256_text(f"{src_hash or ''}|{tgt_hash or ''}")


def _callee(fn, src: bytes):
    """(name, receiver_kind, receiver_text) for a call's `function` node."""
    if fn is None:
        return None, "bare", ""
    if fn.type == "identifier":
        return tsp._text(src, fn), "bare", ""
    if fn.type == "attribute":
        obj = fn.child_by_field_name("object")
        attr = fn.child_by_field_name("attribute")
        if attr is None:
            return None, "bare", ""
        name = tsp._text(src, attr)
        recv = tsp._text(src, obj) if obj is not None else ""
        if recv == "self":
            kind = "self"
        elif obj is not None and obj.type == "identifier":
            kind = "name"   # a module or a local variable
        else:
            kind = "expr"   # self.x.y(), a()(), etc.
        return name, kind, recv
    return None, "bare", ""


def _import_names(node, src: bytes) -> List[str]:
    names: List[str] = []
    mod = node.child_by_field_name("module_name")
    for c in node.children:
        if not c.is_named:
            continue
        if mod is not None and c.start_byte == mod.start_byte and c.end_byte == mod.end_byte:
            continue  # the module path itself, not an imported name
        if c.type in ("dotted_name", "identifier"):
            names.append(tsp._text(src, c).split(".")[-1])
        elif c.type == "aliased_import":
            alias = c.child_by_field_name("alias")
            if alias is not None:
                names.append(tsp._text(src, alias))
    return names


def _class_bases(node, src: bytes) -> List[str]:
    sup = node.child_by_field_name("superclasses")
    if sup is None:
        return []
    out: List[str] = []
    for c in sup.children:
        if c.type in ("identifier", "attribute"):
            out.append(tsp._text(src, c).split(".")[-1])
        elif c.type == "subscript":  # Generic[T], Protocol[T], Sequence[int] -> base name
            val = c.child_by_field_name("value")
            if val is not None:
                out.append(tsp._text(src, val).split(".")[-1])
    return out


def _callee_py(call_node, src: bytes):
    """Python `call` node -> (name, receiver_kind, receiver_text)."""
    return _callee(call_node.child_by_field_name("function"), src)


# --- C++ extractors (verified against tree_sitter_cpp node fields) -----------
def _cpp_leaf_name(node, src: bytes) -> Optional[str]:
    """Bare callable name from a call's leaf node. Handles plain identifiers AND template
    calls (`fn<T>()` / `obj.m<T>()`), whose name lives in the `name` field of a
    template_function / template_method (so we never leak the `<T>` args into the name).
    Returns None for operators / destructors / type-construction (not a plain call)."""
    if node is None:
        return None
    if node.type in ("identifier", "field_identifier"):
        return tsp._text(src, node)
    if node.type in ("template_function", "template_method"):
        nm = node.child_by_field_name("name")
        return tsp._text(src, nm) if nm is not None else None
    return None


def _callee_cpp(call_node, src: bytes):
    """C++ `call_expression` -> (name, receiver_kind, receiver_text). Kinds:
    this (this->m()), qualified (Class::m() / a::b::c::f()), expr (obj.m()), bare (f()).

    Qualified calls are the universal C++ idiom (`rts::sim::systems::run(...)`) and parse
    right-nested: `qualified_identifier(scope=a, name=qualified_identifier(scope=b, ...))`.
    We descend the `name` chain to the deepest qualified_identifier — whose `scope` is the
    immediate receiver namespace/class (parts[-2]) and whose `name` is the leaf callee —
    so multi-level qualifiers resolve via `by_qual_suffix` instead of being dropped.
    Template calls (`fn<T>()`, `a::b::f<T>()`, `obj.m<T>()`) are handled at every leaf."""
    fn = call_node.child_by_field_name("function")
    if fn is None:
        return None, "bare", ""
    if fn.type in ("identifier", "template_function"):
        name = _cpp_leaf_name(fn, src)
        return (name, "bare", "") if name else (None, "bare", "")
    if fn.type == "field_expression":
        field = fn.child_by_field_name("field")
        name = _cpp_leaf_name(field, src)
        if name is None:
            return None, "bare", ""
        arg = fn.child_by_field_name("argument")
        if arg is not None and arg.type == "this":
            return name, "this", "this"
        return name, "expr", tsp._text(src, arg) if arg is not None else ""
    if fn.type == "qualified_identifier":
        node = fn
        while True:
            nm = node.child_by_field_name("name")
            if nm is not None and nm.type == "qualified_identifier":
                node = nm  # descend the a::b::c::... chain to its deepest level
            else:
                break
        name = _cpp_leaf_name(node.child_by_field_name("name"), src)
        if name is None:
            return None, "bare", ""  # ns::Type{}, destructor, operator — not a plain call
        scope = node.child_by_field_name("scope")
        recv = tsp._text(src, scope).split("::")[-1] if scope is not None else ""
        return name, "qualified", recv
    return None, "bare", ""


def _includes_cpp(node, src: bytes) -> List[str]:
    """`#include "foo.h"` / `<vector>` -> ['foo'] / ['vector'] (header stem)."""
    path_node = node.child_by_field_name("path")
    if path_node is None:
        return []
    raw = tsp._text(src, path_node).strip().strip('"').strip("<>").strip()
    if not raw:
        return []
    stem = raw.rsplit("/", 1)[-1]
    for ext in (".hpp", ".hh", ".h", ".hxx"):
        if stem.endswith(ext):
            stem = stem[: -len(ext)]
            break
    return [stem] if stem else []


def _bases_cpp(node, src: bytes) -> List[str]:
    """Base classes from a class/struct `base_class_clause` (skips access specifiers)."""
    clause = node.child_by_field_name("base_class_clause")
    if clause is None:
        for c in node.children:
            if c.type == "base_class_clause":
                clause = c
                break
    if clause is None:
        return []
    return [tsp._text(src, c).split("::")[-1] for c in clause.children
            if c.type in ("type_identifier", "qualified_identifier", "template_type")]


# --- C# extractors -----------------------------------------------------------
def _cs_name(node, src: bytes) -> Optional[str]:
    """Bare callable name from a C# call leaf. Handles generic invocations
    (`M<int>()` / `obj.M<int>()`): a `generic_name` keeps the name in its first
    `identifier` child with the `<...>` in a sibling `type_argument_list`, so we never
    leak the type args into the name (which would stop it resolving)."""
    if node is None:
        return None
    if node.type == "identifier":
        return tsp._text(src, node)
    if node.type == "generic_name":
        ident = next((c for c in node.children if c.type == "identifier"), None)
        return tsp._text(src, ident) if ident is not None else None
    return None


def _callee_csharp(call_node, src: bytes):
    """C# `invocation_expression` -> (name, kind, receiver). this.M()->this;
    Type.M()->qualified (static/nested); obj.M()->expr; M()->bare. Generic forms
    (`M<T>()`, `obj.M<T>()`) resolve to the bare name `M`. Also handles `new T()`
    (`object_creation_expression`) -> the constructed type as a bare call, so a type's
    constructor surfaces its instantiation sites."""
    if call_node.type == "object_creation_expression":
        ty = call_node.child_by_field_name("type")
        if ty is None:
            return None, "bare", ""
        name = _cs_name(ty, src)
        if name is None:  # qualified_name (Ns.Type) / nested
            name = tsp._text(src, ty).split(".")[-1].split("<")[0]
        return (name, "bare", "") if name else (None, "bare", "")
    fn = call_node.child_by_field_name("function")
    if fn is None:
        return None, "bare", ""
    if fn.type in ("identifier", "generic_name"):
        name = _cs_name(fn, src)
        return (name, "bare", "") if name else (None, "bare", "")
    if fn.type == "member_access_expression":
        name = _cs_name(fn.child_by_field_name("name"), src)
        if name is None:
            return None, "bare", ""
        expr = fn.child_by_field_name("expression")
        if expr is not None and expr.type == "this":
            return name, "this", "this"
        if expr is not None and expr.type == "identifier":
            return name, "qualified", tsp._text(src, expr)
        return name, "expr", tsp._text(src, expr) if expr is not None else ""
    return None, "bare", ""


def _imports_csharp(node, src: bytes) -> List[str]:
    """`using System.Text;` -> ['System.Text'] (namespace), file-scope import edge."""
    names = [tsp._text(src, c) for c in node.children
             if c.type in ("identifier", "qualified_name")]
    return names[-1:] if names else []


def _bases_csharp(node, src: bytes) -> List[str]:
    """Base class + interfaces from a `base_list` (C# mixes both). Generic args are stripped
    (`IComparable<C>` -> `IComparable`) so the edge resolves to the base symbol."""
    clause = next((c for c in node.children if c.type == "base_list"), None)
    if clause is None:
        return []
    return [tsp._text(src, c).split(".")[-1].split("<")[0] for c in clause.children
            if c.type in ("identifier", "qualified_name", "generic_name")]


# --- JavaScript / TypeScript extractors --------------------------------------
def _callee_js(call_node, src: bytes):
    """JS/TS `call_expression` -> (name, kind, receiver). this.m()->this;
    obj.m()->expr; m()->bare. Also `new T()` (`new_expression`) -> the constructed type
    as a bare call, so a class surfaces its instantiation sites."""
    if call_node.type == "new_expression":
        ctor = call_node.child_by_field_name("constructor")
        if ctor is None:
            return None, "bare", ""
        if ctor.type == "identifier":
            return tsp._text(src, ctor), "bare", ""
        if ctor.type == "member_expression":
            prop = ctor.child_by_field_name("property")
            return (tsp._text(src, prop), "expr", "") if prop is not None else (None, "bare", "")
        return None, "bare", ""
    fn = call_node.child_by_field_name("function")
    if fn is None:
        return None, "bare", ""
    if fn.type == "identifier":
        return tsp._text(src, fn), "bare", ""
    if fn.type == "member_expression":
        prop = fn.child_by_field_name("property")
        if prop is None:
            return None, "bare", ""
        name = tsp._text(src, prop)
        obj = fn.child_by_field_name("object")
        if obj is not None and obj.type == "this":
            return name, "this", "this"
        return name, "expr", tsp._text(src, obj) if obj is not None else ""
    return None, "bare", ""


def _imports_js(node, src: bytes) -> List[str]:
    """`import {x} from './m.js'` -> ['m'] (module path stem), file-scope import edge."""
    src_node = node.child_by_field_name("source")
    if src_node is None:
        return []
    raw = tsp._text(src, src_node).strip().strip('"').strip("'").strip("`")
    stem = raw.rsplit("/", 1)[-1]
    for ext in (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"):
        if stem.endswith(ext):
            stem = stem[: -len(ext)]
            break
    return [stem] if stem else []


def _bases_js(node, src: bytes) -> List[str]:
    """extends/implements types from a `class_heritage` (covers JS `extends B` and TS
    `extends B implements I`)."""
    clause = next((c for c in node.children if c.type == "class_heritage"), None)
    if clause is None:
        return []
    out: List[str] = []

    def walk(n) -> None:
        if n.type == "type_arguments":
            return  # don't descend into generic args: `extends Container<Item>` -> Container, not Item
        if n.type in ("identifier", "type_identifier"):
            out.append(tsp._text(src, n).split(".")[-1])
            return
        for c in n.children:
            walk(c)

    for c in clause.children:
        walk(c)
    return out


# --- Dart extractors ---------------------------------------------------------
# Dart's grammar (tree-sitter-dart 0.1.0) has no `call_expression`: a call is a postfix
# chain `<receiver> selector(.name)? selector(argument_part)`, and a CASCADE call is
# `<target> cascade_section[cascade_selector(.name) argument_part]`. So calls are found by
# scanning `argument_part` nodes and reading the call name from the preceding selector (or
# the cascade_selector). Inherits come from class_definition's superclass (extends + `with`
# mixins) / interfaces (implements); imports from the library_import URI stem.
_DART_CONF = {"this": 0.9, "expr": 0.5, "bare": 0.6}


def _dart_callee(arg_part, src: bytes):
    """`argument_part` -> (name, kind, receiver). this.h()->this; a.b()->expr; g()->bare;
    cascade `t..m()`->expr (recv=t, Flutter builder pattern); chained `a.b().c()`->c with
    recv falling back to the chain base rather than the intermediate call's `()`."""
    par = arg_part.parent
    if par is None:
        return None, "bare", ""

    # Cascade: target..name(args)  ->  cascade_section[cascade_selector(name), argument_part]
    if par.type == "cascade_section":
        csel = _dart_first(par, "cascade_selector")
        ident = _dart_first(csel, "identifier") if csel is not None else None
        if ident is None:
            return None, "bare", ""
        name = tsp._text(src, ident)
        gp = par.parent
        base = next((c for c in gp.children if c.is_named), None) if gp is not None else None
        if base is not None and base.type == "this":
            return name, "this", "this"
        return name, "expr", tsp._text(src, base) if base is not None else ""

    if par.type != "selector":
        return None, "bare", ""
    chain = par.parent
    if chain is None:
        return None, "bare", ""
    kids = [c for c in chain.children if c.is_named]
    try:
        idx = kids.index(par)
    except ValueError:
        return None, "bare", ""
    if idx == 0:
        return None, "bare", ""
    prev = kids[idx - 1]
    if prev.type == "selector":  # method call: <recv> .name (...)
        asel = _dart_first(prev, "unconditional_assignable_selector",
                           "conditional_assignable_selector")
        ident = _dart_first(asel, "identifier") if asel is not None else None
        if ident is None:
            return None, "bare", ""
        name = tsp._text(src, ident)
        recv = kids[idx - 2] if idx - 2 >= 0 else None
        if recv is not None and recv.type in ("identifier", "this"):
            return (name, "this", "this") if recv.type == "this" else (name, "expr", tsp._text(src, recv))
        # chained call (recv would be an intermediate `()` selector) -> use the chain base
        base = next((k for k in kids[:idx] if k.type in ("identifier", "this")), None)
        if base is not None and base.type == "this":
            return name, "this", "this"
        return name, "expr", tsp._text(src, base) if base is not None else ""
    if prev.type == "identifier":  # bare call: g(...)
        return tsp._text(src, prev), "bare", ""
    return None, "bare", ""


def _dart_first(node, *types):
    if node is None:
        return None
    return next((c for c in node.children if c.type in types), None)


def _imports_dart(node, src: bytes) -> List[str]:
    """`import 'package:p/foo.dart';` -> ['foo'] (URI basename stem, drops .dart)."""
    uri = tsp._find_descendant(node, "uri")
    if uri is None:
        return []
    raw = tsp._text(src, uri).strip().strip("'").strip('"').strip()
    if not raw:
        return []
    stem = raw.replace(":", "/").rsplit("/", 1)[-1]
    if stem.endswith(".dart"):
        stem = stem[:-5]
    return [stem] if stem else []


def _bases_dart(node, src: bytes) -> List[str]:
    """extends + `with` mixins (superclass field) and implements (interfaces field)."""
    out: List[str] = []
    sup = node.child_by_field_name("superclass")
    if sup is not None:
        for c in sup.children:
            if c.type == "type_identifier":
                out.append(tsp._text(src, c).split(".")[-1])
            elif c.type == "mixins":
                out.extend(tsp._text(src, mc).split(".")[-1]
                           for mc in c.children if mc.type == "type_identifier")
    iface = node.child_by_field_name("interfaces")
    if iface is not None:
        out.extend(tsp._text(src, c).split(".")[-1]
                   for c in iface.children if c.type == "type_identifier")
    return out


def _extract_edges_dart(source: bytes, symbols, file_id: str, repo_id: str) -> List[dict]:
    """Dart edges via a custom walk (no generic call_types node — see _dart_callee)."""
    try:
        tree = tsp._get_parser("dart").parse(source)
    except Exception:
        return []
    funcs = [s for s in symbols if s.symbol_type in ("function", "method")]
    classes: Dict[int, object] = {}
    for s in symbols:
        if s.symbol_type == "class":
            classes.setdefault(s.start_line, s)

    def enclosing(line: int):
        best = None
        for s in funcs:
            if s.start_line <= line <= s.end_line and (best is None or s.start_line > best.start_line):
                best = s
        return best

    rows: List[dict] = []
    seen = set()

    def add(row: dict) -> None:
        if row["id"] not in seen:
            seen.add(row["id"])
            rows.append(row)

    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        t = n.type
        if t == "library_import":
            for nm in _imports_dart(n, source):
                add(_row(repo_id, file_id, "file", file_id, "imports", nm, target_type="module"))
        elif t == "class_definition":
            cls = classes.get(n.start_point[0] + 1)
            if cls is not None:
                for base in _bases_dart(n, source):
                    add(_row(repo_id, file_id, "symbol", cls.id, "inherits", base,
                             target_type="class"))
        elif t == "argument_part":
            name, kind, recv = _dart_callee(n, source)
            if name:
                enc = enclosing(n.start_point[0] + 1)
                if enc is not None:
                    add(_row(repo_id, file_id, "symbol", enc.id, "calls", name, recv, kind,
                             _DART_CONF.get(kind, 0.5)))
        for c in n.children:
            stack.append(c)
    return rows


@dataclass
class _EdgeSpec:
    """Per-language static-edge extraction. Adding a language = one entry + its three
    extractor helpers. Resolution (GraphIndex.resolve_call) is language-scoped, so a
    spec never lets a call in language A resolve to a symbol in language B."""

    call_types: tuple
    callee: Callable            # (call_node, src) -> (name, kind, receiver_text)
    import_types: tuple
    imports: Callable           # (node, src) -> list[str]
    class_types: tuple
    bases: Callable             # (class_node, src) -> list[str]
    conf: Dict[str, float]      # receiver kind -> confidence


EDGE_SPECS: Dict[str, _EdgeSpec] = {
    "python": _EdgeSpec(
        ("call",), _callee_py,
        ("import_statement", "import_from_statement"), _import_names,
        ("class_definition",), _class_bases,
        _CALL_CONF),
    "cpp": _EdgeSpec(
        ("call_expression",), _callee_cpp,
        ("preproc_include",), _includes_cpp,
        ("class_specifier", "struct_specifier"), _bases_cpp,
        {"this": 0.9, "qualified": 0.75, "expr": 0.5, "bare": 0.6}),
    "c_sharp": _EdgeSpec(
        ("invocation_expression", "object_creation_expression"), _callee_csharp,
        ("using_directive",), _imports_csharp,
        ("class_declaration", "record_declaration", "struct_declaration"), _bases_csharp,
        {"this": 0.9, "qualified": 0.75, "expr": 0.5, "bare": 0.6}),
    "javascript": _EdgeSpec(
        ("call_expression", "new_expression"), _callee_js,
        ("import_statement",), _imports_js,
        ("class_declaration",), _bases_js,
        {"this": 0.9, "qualified": 0.75, "expr": 0.5, "bare": 0.6}),
    "typescript": _EdgeSpec(
        ("call_expression", "new_expression"), _callee_js,
        ("import_statement",), _imports_js,
        ("class_declaration", "abstract_class_declaration"), _bases_js,
        {"this": 0.9, "qualified": 0.75, "expr": 0.5, "bare": 0.6}),
    "tsx": _EdgeSpec(
        ("call_expression", "new_expression"), _callee_js,
        ("import_statement",), _imports_js,
        ("class_declaration", "abstract_class_declaration"), _bases_js,
        {"this": 0.9, "qualified": 0.75, "expr": 0.5, "bare": 0.6}),
}

# Languages whose graph edges are extracted (drives the honesty notice for the rest).
# Dart is included via its custom extractor (not an EDGE_SPECS entry). html/css are
# deliberately absent — they get symbols but the honest "edges not extracted" notice.
EDGE_LANGUAGES = set(EDGE_SPECS) | {"dart"}


def extract_edges(source: bytes, language: str, symbols, file_id: str, path: str,
                  repo_id: str) -> List[dict]:
    """Return relationship rows (ready for SqliteStore.insert_relationships). Dispatches
    on the per-language EDGE_SPECS entry; languages without a spec extract no edges."""
    if language == "dart":
        return _extract_edges_dart(source, symbols, file_id, repo_id)
    spec = EDGE_SPECS.get(language)
    if spec is None or not is_parseable(language):
        return []
    try:
        tree = tsp._get_parser(language).parse(source)
    except Exception:
        return []

    funcs = [s for s in symbols if s.symbol_type in ("function", "method")]

    def enclosing(line: int):
        best = None
        for s in funcs:
            if s.start_line <= line <= s.end_line and (best is None or s.start_line > best.start_line):
                best = s
        return best

    def class_at(line: int):
        for s in symbols:
            if s.symbol_type in ("class", "struct", "interface") and s.start_line == line:
                return s
        return None

    rows: List[dict] = []
    seen = set()

    def add(row: dict) -> None:
        if row["id"] not in seen:
            seen.add(row["id"])
            rows.append(row)

    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        t = n.type
        if t in spec.call_types:
            name, kind, recv = spec.callee(n, source)
            if name:
                enc = enclosing(n.start_point[0] + 1)
                if enc is not None:
                    add(_row(repo_id, file_id, "symbol", enc.id, "calls", name, recv,
                             kind, spec.conf.get(kind, 0.5)))
        elif t in spec.import_types:
            for nm in spec.imports(n, source):
                add(_row(repo_id, file_id, "file", file_id, "imports", nm,
                         target_type="module"))
        elif t in spec.class_types:
            cls = class_at(n.start_point[0] + 1)
            if cls is not None:
                for base in spec.bases(n, source):
                    add(_row(repo_id, file_id, "symbol", cls.id, "inherits", base,
                             target_type="class"))
        for c in n.children:
            stack.append(c)

    return rows


# ---------------------------------------------------------------------------
# Query-time resolution (names -> symbols), receiver-aware.
# ---------------------------------------------------------------------------
class GraphIndex:
    """In-memory symbol index for resolving edge target names to symbols. Cheap to
    build (hundreds of rows) and rebuilt per query — never persisted resolved."""

    def __init__(self, store, repo_id: str):
        self.store = store
        self.repo_id = repo_id
        # Original-qname lookup (language-agnostic) for entry-point ref resolution.
        self.by_qname: dict = {}
        # Resolution maps are LANGUAGE-SCOPED so a call in language A can never resolve
        # to a same-named symbol in language B. Keys carry the language.
        self.by_id: dict = {}
        self.by_name: dict = {}          # (lang, res_name) -> [sym]
        self.by_qname_res: dict = {}     # (lang, res_qname) -> [sym]  (self/this)
        self.by_qual_suffix: dict = {}   # (lang, parent, res_name) -> [sym]  (qualified)
        self.by_file_and_name: dict = {}  # (lang, stem, res_name) -> [sym]
        self.by_file_id_and_name: dict = {}  # (file_id, res_name) -> [sym]  (precise same-file)
        self._stem: dict = {}
        for row in store.all_symbols(repo_id):
            s = dict(row)
            lang = s.get("language") or ""
            qn = s.get("qualified_name") or ""
            # C++ out-of-line defs come back as `Class::method`; normalize `::`->`.` so the
            # resolver and the symbol agree. res_name is the bare callable name.
            res_qn = qn.replace("::", ".")
            res_name = res_qn.rsplit(".", 1)[-1] if res_qn else (s.get("name") or "")
            s["language"], s["res_qname"], s["res_name"] = lang, res_qn, res_name
            self.by_id[s["id"]] = s
            self.by_qname.setdefault(qn, []).append(s)
            self.by_name.setdefault((lang, res_name), []).append(s)
            if res_qn:
                self.by_qname_res.setdefault((lang, res_qn), []).append(s)
                parts = res_qn.split(".")
                if len(parts) >= 2:
                    self.by_qual_suffix.setdefault((lang, parts[-2], res_name), []).append(s)
            stem = Path(s["path"]).stem
            self._stem[s["file_id"]] = stem
            self.by_file_and_name.setdefault((lang, stem, res_name), []).append(s)
            self.by_file_id_and_name.setdefault((s["file_id"], res_name), []).append(s)

    def stem_of(self, file_id: str) -> Optional[str]:
        return self._stem.get(file_id)

    def collision_count(self, lang: str, name: str) -> int:
        """How many symbols share this bare name in this language — i.e. how badly a
        name-only (`obj.method()`) call collides when it can't be resolved by receiver."""
        return len(self.by_name.get((lang, name), []))

    def high_collision_threshold(self) -> int:
        """Data-driven cutoff for 'this name is too common to resolve by name alone',
        derived from the repo's own `by_name` bucket sizes (NOT a hardcoded denylist):
        the 90th-percentile size among names that actually collide (bucket >= 2), floored
        at a small constant so tiny repos never over-suppress. Names whose bucket meets or
        exceeds this are pure noise in the ambiguous-callee dump."""
        sizes = sorted(len(v) for v in self.by_name.values() if len(v) >= 2)
        if not sizes:
            return 1 << 30  # nothing collides -> suppress nothing
        p90 = sizes[min(len(sizes) - 1, (len(sizes) * 9) // 10)]
        return max(8, p90)

    def resolve_call(self, edge, caller) -> List[tuple]:
        """-> [(symbol_id, confidence, ambiguous, evidence)]. Receiver-aware AND
        language-scoped: self/this -> caller's class; module/qualified -> by scope; bare ->
        the caller's OWN file (precise, by file_id); else by name. A call only ever resolves
        to a symbol in the caller's language, and any name collision is flagged ambiguous
        rather than silently returning an arbitrary same-named hit. The 4th element is a
        short EVIDENCE string (#9): WHY it resolved at this confidence — so an agent can tell
        a `this`-resolved edge from a bare name-collision without trusting the number."""
        name = edge["target_name"]
        kind = edge["target_type"] or "bare"
        recv = edge["receiver"] or ""
        lang = (caller.get("language") or "") if caller else ""

        if kind in ("self", "this"):
            cq = (caller.get("res_qname") or "") if caller else ""
            cls = cq.rsplit(".", 1)[0] if "." in cq else None
            if cls:
                hits = self.by_qname_res.get((lang, f"{cls}.{name}"))
                if hits:
                    return [(hits[0]["id"], 0.9, False,
                             f"receiver '{recv or kind}' → caller's class {cls}")]
        if kind == "qualified" and recv:  # C++ Class::m() / ns::f()
            hits = self.by_qual_suffix.get((lang, recv, name)) or []
            if len(hits) == 1:
                return [(hits[0]["id"], 0.8, False, f"qualified '{recv}::' scope, unique")]
            if len(hits) > 1:
                return [(h["id"], 0.4, True, f"qualified '{recv}::' → {len(hits)} overloads")
                        for h in hits]
        if kind == "name" and recv:  # Python mod.m(): module file stem == recv
            # Two files can share a stem (pkg_a/util.py, pkg_b/util.py) -> a stem match is
            # NOT unique. Resolve confidently only when exactly one file owns the name.
            hits = self.by_file_and_name.get((lang, recv, name)) or []
            if len(hits) == 1:
                return [(hits[0]["id"], 0.8, False, f"module '{recv}' owns one '{name}'")]
            if len(hits) > 1:
                return [(h["id"], 0.4, True, f"stem '{recv}' shared by {len(hits)} files")
                        for h in hits]
        if kind == "bare" and caller:
            # A bare call resolves to a definition in the caller's OWN file (by file_id,
            # not the lossy stem — which collides across same-stem files in other dirs).
            hits = self.by_file_id_and_name.get((caller["file_id"], name)) or []
            if len(hits) == 1:
                return [(hits[0]["id"], 0.8, False, "bare call → definition in caller's file")]
            if len(hits) > 1:
                return [(h["id"], 0.4, True, f"{len(hits)} defs of '{name}' in caller's file")
                        for h in hits]

        hits = self.by_name.get((lang, name), [])
        if len(hits) == 1:
            return [(hits[0]["id"], 0.6, False, f"unique name match for '{name}' (no receiver)")]
        if len(hits) > 1:
            return [(h["id"], 0.35, True, f"name '{name}' collides with {len(hits)} symbols")
                    for h in hits]  # collision -> low + ambiguous
        return []


def _sym_ref(s) -> str:
    return f"{s['path']}::{s['qualified_name']}"


def ingest_affects(settings, shard_paths) -> int:
    """Merge LLM-inferred affects shards [{source, target, confidence, evidence}] and
    store as `affects` edges (origin=llm_inferred). Drops edges whose source/target don't
    resolve to real symbols. Returns the number stored.

    DORMANT / out-of-band: this is the ONLY writer of `affects` edges, and it is an API only
    — not wired to any CLI command or MCP tool, and no skill teaches an agent to produce
    shards. So in normal operation no `affects` edges exist and every read path
    (repo_graph/edit_plan/brief) renders them empty. Kept as a building block until a
    first-class produce->ingest workflow is built; see docs/ARCHITECTURE.md."""
    import json

    from pandemonium.storage.sqlite_store import SqliteStore
    from pandemonium.util import repo_id_for

    store = SqliteStore(settings.sqlite_path)
    store.create_schema()
    repo_id = repo_id_for(settings.repo_root)
    try:
        idx = GraphIndex(store, repo_id)
        valid = {_sym_ref(s) for s in idx.by_id.values()}
        rows = []
        for p in shard_paths:
            try:
                edges = json.load(open(p, encoding="utf-8"))
            except (ValueError, OSError):
                continue
            if not isinstance(edges, list):
                continue
            for e in edges:
                src, tgt = e.get("source"), e.get("target")
                if not src or not tgt or tgt not in valid or src == tgt:
                    continue
                chunk = store.chunk_by_ref(repo_id, src)
                if chunk is None or not chunk["symbol_id"]:
                    continue
                tgt_chunk = store.chunk_by_ref(repo_id, tgt)
                ev_hash = _affects_evidence_hash(
                    chunk["content_hash"],
                    tgt_chunk["content_hash"] if tgt_chunk is not None else None)
                rows.append(_row(repo_id, chunk["file_id"], "symbol", chunk["symbol_id"],
                                 "affects", tgt, confidence=float(e.get("confidence", 0.5)),
                                 origin="llm_inferred", evidence=str(e.get("evidence", "")),
                                 evidence_hash=ev_hash, created_at=now_iso()))
        store.insert_relationships(rows)
        store.commit()
        return len(rows)
    finally:
        store.close()


def _resolve_target(idx: "GraphIndex", path: str, qname: Optional[str]):
    target = None
    for s in (idx.by_qname.get(qname or "", []) if qname else []):
        if s["path"] == path:
            return s
        if target is None:
            target = s
    return target


def _confirm_grep(name: str, path: str) -> str:
    """M2 (ROADMAP v2): a one-shot exact-text confirmation an agent can run as-is to verify
    an unverified edge, instead of relying on it to remember to grep. Greps the call name in
    the file that holds the call site — the call-graph's own offer to close the loop. Both
    args are shell-quoted so a path with spaces (common on Windows) stays a single argument."""
    return f"grep -nF {shlex.quote(name)} {shlex.quote(path)}"


def _callees_of(store, idx: "GraphIndex", target) -> tuple:
    callees, ambiguous_calls = [], []
    lang = target.get("language") or ""
    for e in store.out_edges(target["id"], "calls"):
        tname = e["target_name"]
        for sid, conf, ambiguous, evidence in idx.resolve_call(e, target):
            tgt = idx.by_id.get(sid)
            if not tgt or sid == target["id"]:
                continue
            rec = {"id": sid, "ref": _sym_ref(tgt), "name": tname,
                   "via": e["receiver"] or tname, "confidence": round(conf, 2),
                   "collisions": idx.collision_count(lang, tname), "evidence": evidence}
            if ambiguous:  # unverified -> offer the one-shot confirmation (M2)
                rec["confirm"] = _confirm_grep(tname, target["path"])
                ambiguous_calls.append(rec)
            else:
                callees.append(rec)
    return callees, ambiguous_calls


def _split_ambiguous_callees(callees_ambig: list, threshold: int) -> tuple:
    """Partition the ambiguous-callee bucket into the ones worth listing and a single
    summary of the high-collision noise (`.size()`, `.begin()`, `.counters()` — names so
    common that a name-only resolve carries ~zero signal). Returns (kept, summary|None).
    `summary` is {count, names_count, examples} for one collapsed render line."""
    kept, suppressed = [], []
    for c in callees_ambig:
        (suppressed if c.get("collisions", 0) >= threshold else kept).append(c)
    if not suppressed:
        return kept, None
    # Examples = the most-colliding distinct names, for a human-readable hint.
    by_name: Dict[str, int] = {}
    for c in suppressed:
        nm = c.get("name") or _short_ref(c["ref"])
        by_name[nm] = max(by_name.get(nm, 0), c.get("collisions", 0))
    examples = [nm for nm, _ in sorted(by_name.items(), key=lambda kv: -kv[1])[:3]]
    return kept, {"count": len(suppressed), "names_count": len(by_name),
                  "examples": examples}


CALLER_POSSIBLE_MIN = 0.4  # below this a caller hit is too weak to even list as 'possible'


def _callers_of(store, idx: "GraphIndex", target) -> tuple:
    """-> (callers, callers_possible). `callers` resolve confidently to EXACTLY the target
    (unambiguous, conf >= CALLER_MIN_CONFIDENCE). `callers_possible` is the honest residual
    the old code silently discarded: the target is among an ambiguous set, or the call
    resolved with 0.4 <= conf < 0.6 — real-but-unverified callers an agent should grep to
    confirm. A source that resolves confidently never also appears as possible."""
    confident: dict = {}
    possible: dict = {}
    tname = target.get("res_name") or target["name"]
    for e in store.edges_by_target_name(idx.repo_id, tname, "calls"):
        src = idx.by_id.get(e["source_id"])
        if not src:
            continue
        for sid, conf, ambiguous, evidence in idx.resolve_call(e, src):
            if sid != target["id"]:
                continue
            rec = {"id": src["id"], "ref": _sym_ref(src), "confidence": round(conf, 2),
                   "evidence": evidence}
            if not ambiguous and conf >= CALLER_MIN_CONFIDENCE:
                cur = confident.get(src["id"])
                if cur is None or rec["confidence"] > cur["confidence"]:
                    confident[src["id"]] = rec
            elif ambiguous or conf >= CALLER_POSSIBLE_MIN:  # name-collision (any conf) or 0.4-0.6
                rec["confirm"] = _confirm_grep(tname, src["path"])  # one-shot confirm (M2)
                cur = possible.get(src["id"])
                if cur is None or rec["confidence"] > cur["confidence"]:
                    possible[src["id"]] = rec
    callers = list(confident.values())
    callers_possible = [p for sid, p in possible.items() if sid not in confident]
    return callers, callers_possible


def _split_prod_test(refs: List[str]) -> tuple:
    """Partition refs into (production, test) by their file path — so impact leads with
    the real blast radius and never buries production callers under test ones."""
    from pandemonium.retrieval.tests_finder import is_test_path
    prod, test = [], []
    for r in refs:
        path = r.split("::", 1)[0].split(":", 1)[0]
        (test if is_test_path(path) else prod).append(r)
    return prod, test


def _similar_for(store, lance, repo_id: str, target, k: int = 5) -> list:
    """Vector neighbours of a symbol's descriptor — grounded 'similar implementations'
    (no LLM). Excludes the symbol itself and same-file hits."""
    chunk = store.chunk_by_ref(repo_id, _sym_ref(target))
    if chunk is None:
        return []
    vec = lance.vector_for(chunk["id"])
    if vec is None:
        return []
    hits = lance.search(vec, limit=k + 12)
    meta = store.get_chunks([cid for cid, _ in hits])
    out, seen = [], set()
    self_ref = _sym_ref(target)
    for cid, score in hits:
        row = meta.get(cid)
        if row is None or row["path"] == target["path"]:
            continue
        r = row["ref"] or f"{row['path']}:{row['start_line']}-{row['end_line']}"
        if r in seen or r == self_ref:
            continue
        seen.add(r)
        out.append({"ref": r, "similarity": round(1.0 + score, 3)})  # 1 - L2 (approx cosine)
        if len(out) >= k:
            break
    return out


def repo_graph(settings, ref: str, graph=None) -> Optional[dict]:
    """Related code for a ref: callees, callers, imports, inheritance, members, tests,
    similar implementations (vector), and any LLM-inferred 'affects' hypotheses."""
    from pandemonium.retrieval.tests_finder import find_tests
    from pandemonium.storage.lancedb_store import LanceStore
    from pandemonium.storage.sqlite_store import SqliteStore
    from pandemonium.util import repo_id_for
    from pandemonium import refs as refs_mod

    path, qname, _, _ = refs_mod.parse_ref(ref)
    store = SqliteStore(settings.sqlite_path)
    store.create_schema()
    repo_id = repo_id_for(settings.repo_root)
    try:
        idx = graph if graph is not None else GraphIndex(store, repo_id)
        target = _resolve_target(idx, path, qname)
        if target is None:
            return None

        callees, callees_ambig = _callees_of(store, idx, target)
        callees_ambig, callees_suppressed = _split_ambiguous_callees(
            callees_ambig, idx.high_collision_threshold())
        callers, callers_possible = _callers_of(store, idx, target)
        imports = [e["target_name"] for e in store.out_edges(target["file_id"], "imports")]
        inherits = [e["target_name"] for e in store.out_edges(target["id"], "inherits")]
        # Match on the normalized qname so out-of-line C++ members (`Class::m` ->
        # `Class.m`) are included; emit the raw ref via _sym_ref.
        tqn = (target.get("res_qname") or target["qualified_name"] or "")
        members = [_sym_ref(s) for s in idx.by_id.values()
                   if (s.get("res_qname") or "").startswith(tqn + ".")]
        tests = find_tests(store, repo_id, target.get("res_name") or target["name"])
        affects = []
        for e in store.out_edges(target["id"], "affects"):
            stored_hash = e["evidence_hash"] if "evidence_hash" in e.keys() else None
            tgt_chunk = store.chunk_by_ref(repo_id, e["target_name"])
            cur_hash = _affects_evidence_hash(
                target["content_hash"],
                tgt_chunk["content_hash"] if tgt_chunk is not None else None)
            affects.append({
                "ref": e["target_name"], "confidence": e["confidence"],
                "needs_revalidation": bool(stored_hash) and cur_hash != stored_hash})

        try:
            lance = LanceStore(settings.lancedb_path, read_only=True)
            similar = _similar_for(store, lance, repo_id, target, k=5)
        except Exception:
            similar = []

        return {"ref": _sym_ref(target), "type": target["symbol_type"],
                "callees": callees, "callees_ambiguous": callees_ambig,
                "callees_suppressed": callees_suppressed,
                "callers": callers, "callers_possible": callers_possible,
                "imports": sorted(set(imports)),
                "inherits": inherits, "members": members[:30], "tests": tests,
                "similar": similar, "affects": affects,
                "edges_available": _edges_available(target["path"])}
    finally:
        store.close()


def _edge_line(c: dict, show_evidence: bool) -> str:
    """One edge as a render line: `ref  ~conf`, plus its evidence (#9) when asked, plus the
    one-shot confirm command (M2) whenever the edge carries one (i.e. it's unverified)."""
    line = f"{c['ref']}  ~{c['confidence']}"
    if show_evidence and c.get("evidence"):
        line += f"  — {c['evidence']}"
    if c.get("confirm"):
        line += f"\n    confirm: {c['confirm']}"
    return line


def render_graph(g: dict, show_evidence: bool = False) -> str:
    if not g:
        return "Ref not found in the graph."
    out = [f"# Graph: {g['ref']}  [{g['type']}]"]
    if g.get("edges_available") is False:
        out.append(
            "\n> Note: call/import/inherit edges are extracted for **Python, C++, C#, Dart, "
            "and JS/TS**. This file's language isn't among them, so empty Calls / Called-by / "
            "Imports below mean edges were never extracted — NOT that none exist. Don't "
            "read this as 'no callers'.")

    def section(title, items):
        out.append(f"\n## {title} ({len(items)})")
        if items:
            out.extend(f"- {it}" for it in items[:30])
        else:
            out.append("- (none)")

    section("Calls (callees)", [_edge_line(c, show_evidence) for c in g["callees"]])
    section("Called by (callers)", [_edge_line(c, show_evidence) for c in g["callers"]])
    possible = g.get("callers_possible") or []
    if possible:
        out.append(f"\n## Possible callers (unverified — grep to confirm) ({len(possible)})")
        out.extend(f"- {_edge_line(c, show_evidence)}" for c in possible[:15])
    if g["inherits"]:
        section("Inherits", g["inherits"])
    if g.get("imports"):
        section("Imports", g["imports"])
    if g.get("similar"):
        section("Similar implementations (vector — suggestive, not verified)",
                [f"{s['ref']}  ~{s['similarity']}" for s in g["similar"]])
    if g.get("affects"):
        out.append(f"\n## Affects (LLM-inferred hypotheses) ({len(g['affects'])})")
        for a in g["affects"][:15]:
            flag = (" — STALE: code changed since inferred, re-run affects"
                    if a.get("needs_revalidation") else "")
            out.append(f"- {a['ref']}  ~{a['confidence']} (hypothesis){flag}")
    if g["members"]:
        section("Members", g["members"])
    if g["tests"]:
        section("Tests (name-matched — confirm relevance)", g["tests"])
    if g["callees_ambiguous"]:
        out.append(f"\n## Ambiguous calls (low confidence, name-collision) "
                   f"({len(g['callees_ambiguous'])})")
        for c in g["callees_ambiguous"][:15]:
            line = f"- {c['via']} -> {_edge_line(c, show_evidence)}"
            out.append(line)
    sup = g.get("callees_suppressed")
    if sup:
        eg = ", ".join(sup["examples"])
        out.append(f"\n_+{sup['count']} high-collision name-only call(s) across "
                   f"{sup['names_count']} common name(s) suppressed (e.g. {eg}) — "
                   f"too ambiguous to resolve by name; grep if needed._")
    return "\n".join(out)


def repo_impact(settings, ref: str, depth: int = 2, graph=None) -> Optional[dict]:
    """What may be affected if `ref` changes: transitive callers (BFS over the resolved
    caller relation, up to `depth` hops), the files they live in, and related tests.
    Conservative by design — only confidently-resolved callers, so it under-claims rather
    than misleads (impact compounds error at every hop)."""
    from pandemonium.retrieval.tests_finder import find_tests
    from pandemonium.storage.sqlite_store import SqliteStore
    from pandemonium.util import repo_id_for
    from pandemonium import refs as refs_mod

    path, qname, _, _ = refs_mod.parse_ref(ref)
    store = SqliteStore(settings.sqlite_path)
    store.create_schema()
    repo_id = repo_id_for(settings.repo_root)
    try:
        idx = graph if graph is not None else GraphIndex(store, repo_id)
        target = _resolve_target(idx, path, qname)
        if target is None:
            return None

        impacted: dict = {}  # id -> {ref, hop}
        visited = {target["id"]}
        frontier = [target]
        possible_direct: list = []
        for hop in range(1, max(1, depth) + 1):
            nxt = []
            for sym in frontier:
                confident, possible = _callers_of(store, idx, sym)
                if sym is target:  # honest residual at the direct level only
                    possible_direct = [p["ref"] for p in possible]
                for c in confident:  # BFS stays conservative: confident callers only
                    if c["id"] not in visited:
                        visited.add(c["id"])
                        impacted[c["id"]] = {"ref": c["ref"], "hop": hop}
                        nxt.append(idx.by_id[c["id"]])
            frontier = nxt
            if not frontier:
                break

        direct = [v["ref"] for v in impacted.values() if v["hop"] == 1]
        indirect = [v["ref"] for v in impacted.values() if v["hop"] > 1]
        affected_files = sorted({idx.by_id[i]["path"] for i in impacted}
                                | {target["path"]})
        tests = find_tests(store, repo_id, target.get("res_name") or target["name"])
        direct_prod, direct_test = _split_prod_test(direct)
        possible_prod, possible_test = _split_prod_test(possible_direct)
        return {"ref": _sym_ref(target), "direct": direct, "indirect": indirect,
                "direct_production": direct_prod, "direct_test": direct_test,
                "possible": possible_direct,
                "possible_production": possible_prod, "possible_test": possible_test,
                "affected_files": affected_files, "tests": tests,
                "call_name": target.get("res_name") or target["name"],  # for M2 confirm
                "edges_available": _edges_available(target["path"])}
    finally:
        store.close()


def render_impact(g: dict) -> str:
    if not g:
        return "Ref not found in the graph."
    out = [f"# Impact of changing {g['ref']}"]
    if g.get("edges_available") is False:
        out.append(
            "\n> Note: impact analysis covers **Python, C++, C#, Dart, and JS/TS**. This file's "
            "language isn't among them, so an empty result means caller edges were never "
            "extracted — NOT that nothing depends on it. Verify manually before assuming "
            "it's safe to change.")
    out.append(f"\n## Directly affected — callers ({len(g['direct'])})")
    prod = g.get("direct_production", g["direct"])
    test = g.get("direct_test", [])
    if prod or test:
        if prod:  # production first — the real blast radius
            out.append(f"### Production ({len(prod)})")
            out += [f"- {r}" for r in prod]
        if test:
            out.append(f"### Test ({len(test)})")
            out += [f"- {r}" for r in test]
    else:
        out.append("- (none confidently resolved)")
    if g["indirect"]:
        out.append(f"\n## Indirectly affected — transitive ({len(g['indirect'])})")
        out += [f"- {r}" for r in g["indirect"][:30]]
    possible = g.get("possible") or []
    if possible:
        call_name = g.get("call_name")

        def _poss(r):  # M2: each unverified caller carries its own one-shot confirmation.
            line = f"- {r}"
            if call_name:
                line += f"\n    confirm: {_confirm_grep(call_name, r.split('::', 1)[0])}"
            return line

        out.append(f"\n## Possible callers (unverified — grep to confirm) ({len(possible)})")
        pprod = g.get("possible_production", possible)
        ptest = g.get("possible_test", [])
        if pprod:
            out.append(f"### Production ({len(pprod)})")
            out += [_poss(r) for r in pprod[:15]]
        if ptest:
            out.append(f"### Test ({len(ptest)})")
            out += [_poss(r) for r in ptest[:15]]
    out.append(f"\n## Affected files ({len(g['affected_files'])})")
    out += [f"- {p}" for p in g["affected_files"]]
    if g["tests"]:
        out.append(f"\n## Tests to run (name-matched — confirm relevance) ({len(g['tests'])})")
        out += [f"- {t}" for t in g["tests"]]
    out.append("\n_Conservative: only confidently-resolved callers; verify with the "
               "tests above._")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Edit plan — compose impact + graph + tests into a ranked "how to change this".
# ---------------------------------------------------------------------------
def _dedup_keep(seq: List[str]) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _plan_tests(g: dict, imp: dict, direct: List[str]) -> List[str]:
    """Name-matched tests UNION callers that live in test files — so the plan never claims
    'no tests' while listing test callers in the impact sections."""
    from pandemonium.retrieval.tests_finder import is_test_path
    name_tests = imp.get("tests") or g.get("tests") or []
    caller_tests = [r for r in (direct + imp.get("indirect", []))
                    if is_test_path(r.split("::", 1)[0].split(":", 1)[0])]
    return _dedup_keep(list(name_tests) + caller_tests)


def _edit_risks(settings, ref: str, g: dict, imp: dict, tests: List[str]) -> List[str]:
    """Surface what makes this edit risky, from data already computed."""
    import json

    from pandemonium import refs as refs_mod
    from pandemonium.storage.sqlite_store import SqliteStore
    from pandemonium.util import repo_id_for

    risks: List[str] = []
    if not g.get("edges_available", True):
        risks.append("Graph edges aren't extracted for this file's language (covered: "
                     "Python, C++, C#, Dart, JS/TS) — the caller list is NOT reliable. Search "
                     "for callers manually before changing behavior.")
    direct = imp.get("direct", [])
    if len(direct) >= 5:
        risks.append(f"High fan-in: {len(direct)} direct callers — a behavior change is "
                     "widely felt.")
    elif direct:
        risks.append(f"{len(direct)} direct caller(s) to keep compatible.")
    if imp.get("indirect"):
        risks.append(f"Transitive reach: {len(imp['indirect'])} more symbol(s) across "
                     f"{len(imp.get('affected_files', []))} file(s).")
    if not tests:
        risks.append("No tests found (by name or among callers) — locate or add coverage "
                     "before changing behavior.")
    if g.get("affects"):
        stale = sum(1 for a in g["affects"] if a.get("needs_revalidation"))
        msg = f"{len(g['affects'])} LLM-inferred coupling hypothesis(es) to verify"
        if stale:
            msg += f" ({stale} STALE — re-run affects)"
        risks.append(msg + ".")
    store = SqliteStore(settings.sqlite_path)
    store.create_schema()
    try:
        row = store.chunk_by_ref(repo_id_for(settings.repo_root), ref)
    finally:
        store.close()
    if row is not None and row["tags"]:
        try:
            parsed = json.loads(row["tags"])
        except (ValueError, TypeError):
            parsed = {}
        se = parsed.get("side_effects") if isinstance(parsed, dict) else None
        if isinstance(se, (list, tuple)) and se:
            risks.append("Has side effects (" + ", ".join(str(x) for x in se[:4]) +
                         ") — external behavior may change.")
    resolved = refs_mod.resolve_from_row(settings.repo_root, ref, row)
    if resolved is not None and resolved.ambiguous:
        risks.append("Ref is AMBIGUOUS (multiple same-named symbols) — confirm the right "
                     "one via repo_get before editing.")
    if resolved is not None and resolved.stale:
        risks.append("Indexed copy is STALE (file changed since indexing) — reindex first.")
    return risks


def edit_plan(settings, ref: str, graph=None) -> Optional[dict]:
    """A ranked change plan for `ref`, composed from repo_impact + repo_graph +
    find_tests: the primary target, direct callers to keep compatible, transitive reach,
    tests to update, dependencies to read, coupling hypotheses, risks, and a suggested
    fetch order. The answer to 'I'm about to change this — what do I need first?'."""
    g = repo_graph(settings, ref, graph=graph)
    if g is None:
        return None
    imp = repo_impact(settings, ref, graph=graph) or {}
    target = g["ref"]
    direct = imp.get("direct", [])
    deps = [c["ref"] for c in g.get("callees", [])]
    tests = _plan_tests(g, imp, direct)
    return {
        "ref": target, "type": g["type"], "primary": target,
        "members": g.get("members", []),
        "callers_direct": direct,
        "callers_transitive": imp.get("indirect", []),
        "affected_files": imp.get("affected_files", []),
        "tests": tests,
        "dependencies": deps,
        "affects": g.get("affects", []),
        "edges_available": g.get("edges_available", True),
        "risks": _edit_risks(settings, ref, g, imp, tests),
        "fetch_order": _dedup_keep([target] + direct[:8] + tests[:8]),
    }


def render_edit_plan(p: dict) -> str:
    if not p:
        return "Ref not found — can't plan an edit."
    out = [f"# Edit plan: {p['ref']}  [{p['type']}]"]
    if p.get("edges_available") is False:
        out.append("\n> This file's language has no graph edges (covered: Python, C++, C#, "
                   "JS/TS): caller/impact data below is incomplete. Treat the caller list "
                   "as a floor, not a ceiling.")
    out.append("\n## 1. Primary target")
    out.append(f"- {p['primary']}")
    if p.get("members"):
        out.append("  members: " + ", ".join(p["members"][:10]))
    out.append(f"\n## 2. Direct callers to keep compatible ({len(p['callers_direct'])})")
    out += [f"- {r}" for r in p["callers_direct"][:20]] or ["- (none confidently resolved)"]
    if p["callers_transitive"]:
        out.append(f"\n## Transitively affected ({len(p['callers_transitive'])})")
        out += [f"- {r}" for r in p["callers_transitive"][:15]]
    out.append(f"\n## 3. Tests to update / run ({len(p['tests'])})")
    out += [f"- {t}" for t in p["tests"][:20]] or ["- (none found by name — add coverage)"]
    if p["dependencies"]:
        out.append(f"\n## 4. Depends on — callees you may need to read ({len(p['dependencies'])})")
        out += [f"- {d}" for d in p["dependencies"][:15]]
    if p.get("affects"):
        out.append("\n## Coupling hypotheses (LLM-inferred — verify, don't trust)")
        for a in p["affects"][:10]:
            flag = " — STALE" if a.get("needs_revalidation") else ""
            out.append(f"- {a['ref']}  ~{a['confidence']}{flag}")
    out.append("\n## Risks")
    out += [f"- {r}" for r in p["risks"]] or ["- (low — looks like a local change)"]
    out.append("\n## Suggested fetch order")
    out += [f"{i}. {r}" for i, r in enumerate(p["fetch_order"], 1)]
    out.append("\n_Composed from repo_impact + repo_graph + repo_find_tests; callers are "
               "conservative (extracted-language edges only). Fetch the target + direct callers + tests "
               "before editing._")
    return "\n".join(out)


def repo_logic_map(settings, topic: str, top_k: int = 12, graph=None) -> Optional[dict]:
    """Conceptual flow for a topic: the relevant symbols (semantic search), the domains
    and files they live in, and how they call each other — a grounded 'logic map'."""
    from pandemonium.retrieval.hybrid_search import Retriever

    retriever = Retriever(settings)
    try:
        results = retriever.search(topic, top_k=top_k)
        if not results:
            return None
        store, repo_id = retriever.sqlite, retriever.repo_id
        idx = graph if graph is not None else GraphIndex(store, repo_id)
        refs = {r.ref for r in results if r.ref}

        domain_count: dict = {}
        connections: list = []
        for r in results:
            for d in (r.tags or {}).get("domain", []):
                domain_count[d] = domain_count.get(d, 0) + 1
            if r.qualified_name:
                target = _resolve_target(idx, r.path, r.qualified_name)
                if target:
                    callees, _ = _callees_of(store, idx, target)
                    for c in callees:
                        if c["ref"] in refs and c["ref"] != r.ref:
                            connections.append((r.ref, c["ref"]))

        by_file: dict = {}
        for r in results:
            by_file.setdefault(r.path, []).append(
                {"ref": r.ref or f"{r.path}:{r.start_line}-{r.end_line}",
                 "summary": r.summary or ""})
        domains = sorted(domain_count.items(), key=lambda x: -x[1])[:8]
        return {"topic": topic, "files": by_file, "domains": domains,
                "connections": connections}
    finally:
        retriever.close()


def _short_ref(ref: str) -> str:
    return ref.split("::")[-1] if "::" in ref else ref


def render_logic_map(g: dict) -> str:
    if not g:
        return "No matches for that topic."
    out = [f"# Logic map: {g['topic']}"]
    if g["domains"]:
        out.append("\n## Domains involved")
        out += [f"- {d} ({n})" for d, n in g["domains"]]
    out.append(f"\n## Files & symbols ({len(g['files'])})")
    for path, syms in g["files"].items():
        out.append(f"\n### {path}")
        out += [f"- {_short_ref(s['ref'])}: {s['summary']}"[:150] for s in syms[:8]]
    if g["connections"]:
        out.append(f"\n## Flow within the topic — who calls whom ({len(g['connections'])})")
        out += [f"- {_short_ref(a)} -> {_short_ref(b)}" for a, b in g["connections"][:20]]
    return "\n".join(out)
