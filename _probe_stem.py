"""Probe A: same-stem collision in resolve_call bare/name branches.

Two files share a stem ('util') in different dirs, each defines foo() and a
caller bar() that bare-calls foo(). Does the graph resolve each bar->foo to the
SAME file's foo (correct) and flag collisions, or pick hits[0] arbitrarily with
ambiguous=False?
"""
import tempfile, os
from pathlib import Path

from pandemonium.indexer import tree_sitter_parser as tsp
from pandemonium.graph import extract_edges, GraphIndex
from pandemonium.util import symbol_id_for, file_id_for, repo_id_for
from pandemonium.models import Symbol


class FakeStore:
    def __init__(self, sym_rows):
        self._syms = sym_rows
    def all_symbols(self, repo_id):
        return self._syms


def syms_for(path, fid, repo_id, src):
    parsed = tsp.parse_symbols(src.encode(), "python")
    out = []
    for ps in parsed:
        sid = symbol_id_for(fid, ps.qualified_name, ps.start_line)
        out.append({
            "id": sid, "repo_id": repo_id, "file_id": fid, "path": path,
            "language": "python", "name": ps.name,
            "qualified_name": ps.qualified_name, "symbol_type": ps.symbol_type,
            "start_line": ps.start_line, "end_line": ps.end_line,
        })
    return out, parsed


SRC_A = "def foo():\n    return 1\n\ndef bar():\n    return foo()\n"
SRC_B = "def foo():\n    return 2\n\ndef baz():\n    return foo()\n"

repo_id = "repo_test"
fid_a = file_id_for(repo_id, "pkg_a/util.py")
fid_b = file_id_for(repo_id, "pkg_b/util.py")

# Build Symbol objects for extract_edges (needs .id/.symbol_type/.start_line/.end_line)
def model_syms(path, fid):
    parsed = tsp.parse_symbols((SRC_A if path.startswith("pkg_a") else SRC_B).encode(), "python")
    ms = []
    for ps in parsed:
        sid = symbol_id_for(fid, ps.qualified_name, ps.start_line)
        ms.append(Symbol(sid, repo_id, fid, ps.symbol_type, ps.name,
                         ps.qualified_name, ps.signature, ps.start_line, ps.end_line, None, None))
    return ms

ms_a = model_syms("pkg_a/util.py", fid_a)
ms_b = model_syms("pkg_b/util.py", fid_b)

edges_a = extract_edges(SRC_A.encode(), "python", ms_a, fid_a, "pkg_a/util.py", repo_id)
edges_b = extract_edges(SRC_B.encode(), "python", ms_b, fid_b, "pkg_b/util.py", repo_id)

print("=== edges from pkg_a (bar calls foo) ===")
for e in edges_a:
    if e["relationship_type"] == "calls":
        print(f"  source={e['source_id'][:12]} -> {e['target_name']} recv={e['receiver']!r} kind={e['target_type']}")

# Build the resolver index over BOTH files' symbols
sym_rows_a, _ = syms_for("pkg_a/util.py", fid_a, repo_id, SRC_A)
sym_rows_b, _ = syms_for("pkg_b/util.py", fid_b, repo_id, SRC_B)
store = FakeStore(sym_rows_b + sym_rows_a)  # pkg_b FIRST -> hits[0] is pkg_b:foo
idx = GraphIndex(store, repo_id)

# Find pkg_a's bar() caller symbol dict and resolve its 'calls foo' edge
bar_caller = next(s for s in idx.by_id.values()
                  if s["name"] == "bar" and s["path"] == "pkg_a/util.py")
foo_a = next(s for s in idx.by_id.values()
             if s["name"] == "foo" and s["path"] == "pkg_a/util.py")
foo_b = next(s for s in idx.by_id.values()
             if s["name"] == "foo" and s["path"] == "pkg_b/util.py")

call_edge = next(e for e in edges_a if e["relationship_type"] == "calls"
                 and e["target_name"] == "foo")
res = idx.resolve_call(call_edge, bar_caller)
print("\n=== resolve pkg_a:bar -> foo() (bare call, both files have foo) ===")
print(f"  foo in pkg_a id={foo_a['id'][:12]}  foo in pkg_b id={foo_b['id'][:12]}")
for sid, conf, amb in res:
    which = "pkg_a" if sid == foo_a["id"] else ("pkg_b" if sid == foo_b["id"] else "??")
    print(f"  -> resolved to {which} (id={sid[:12]}) conf={conf} ambiguous={amb}")

# The CORRECT answer: pkg_a:bar must resolve to pkg_a:foo (same file). If it
# returns pkg_b:foo, that's a SILENT WRONG ANSWER (ambiguous=False, conf 0.8).
correct = all(sid == foo_a["id"] for sid, _, _ in res)
print(f"\n  RESULT: {'CORRECT (resolved to own-file foo)' if correct else 'WRONG -- resolved to a different file with same stem!'}")
