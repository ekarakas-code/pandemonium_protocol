"""Post-edit static breakage check (gated: retrieval.breakage_check, default OFF).

Reports a FLOOR of compiler-catchable breakage from edits NOT yet indexed: call-sites whose
callee was removed/renamed, call-sites of a signature that changed, and dangling imports of a
deleted module/symbol. It is a LOWER BOUND and declares IN-BAND what it cannot see — dynamic
dispatch / reflection, framework-registered or string-keyed calls, cross-language calls
(resolution is language-scoped), files this index doesn't cover, and the symmetric case where
an edited function now MIS-calls an unchanged callee (its new edges aren't indexed yet). A bare
"no breakage" would be exactly the confident-wrong output the project forbids, so the
not-covered block is emitted on every result (clean, dirty, or empty).

Reuses GraphIndex.resolve_call / _callers_of — rebuilds no resolution.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pandemonium.graph import (GraphIndex, _callers_of, _confirm_grep, _edges_available,
                               _sym_ref)
from pandemonium.indexer.language_detector import detect, is_parseable
from pandemonium.indexer.tree_sitter_parser import parse_symbols
from pandemonium.refs import _match_by_fingerprint, _read_lines, parse_ref
from pandemonium.storage.sqlite_store import SqliteStore
from pandemonium.util import repo_id_for, signature_hash_for

# The fixed limits block. ALWAYS rendered — a result without it could read as a guarantee.
_NOT_COVERED = (
    "> This is a LOWER BOUND of static, compiler-catchable breakage — it does NOT prove the "
    "code compiles. Not covered: dynamic dispatch / reflection, framework-registered or "
    "string-keyed calls; cross-language calls (resolution is language-scoped); files this index "
    "doesn't cover or can't parse (listed below as unanalyzable); and the symmetric case where "
    "an edited function now MIS-calls an unchanged callee (its new call edges aren't indexed "
    "yet — re-run after `repo_reindex_changed`). Most reliable on the CLI or with "
    "retrieval.auto_reindex=false.")


def _norm(qn: Optional[str]) -> str:
    return (qn or "").replace("::", ".")


def breakage_check(settings, target: Optional[str] = None, graph: "GraphIndex" = None) -> dict:
    """Analyze breakage from un-indexed edits. `target` empty => all files changed since the
    index; `target` a 'path::Qualified.Name' or 'path' ref => just that symbol/file. `graph` is
    an optional pre-built GraphIndex (the MCP path passes its cached one)."""
    repo_id = repo_id_for(settings.repo_root)

    scope_qname = None
    if target:
        path, scope_qname, _, _ = parse_ref(target)
        changed_paths, deleted_paths = [path], []
    else:
        from pandemonium import service  # lazy: avoid import cycle
        ch = service.detect_changes(settings)
        changed_paths, deleted_paths = ch["changed"], ch["deleted"]
        if not changed_paths and not deleted_paths:
            return {"status": "empty"}

    own_store = graph is None
    store = SqliteStore(settings.sqlite_path) if own_store else graph.store
    if own_store:
        store.create_schema()
    try:
        idx = GraphIndex(store, repo_id) if own_store else graph  # the BEFORE snapshot
        removed, signature_changed, unanalyzable = [], [], []
        edges_available = {}

        for path in changed_paths:
            old_syms = [s for s in idx.by_id.values() if s["path"] == path]
            if scope_qname:
                target_qn = _norm(scope_qname)
                old_syms = [s for s in old_syms if s.get("res_qname") == target_qn]
            if not old_syms:
                continue
            edges_available[path] = _edges_available(path)
            language = detect(path)
            lines = _read_lines(settings.repo_root, path)
            if lines is None or not is_parseable(language):
                unanalyzable.append(path)
                continue
            live = parse_symbols("\n".join(lines).encode("utf-8", "replace"), language)
            live_by_qn: dict = {}
            for ls in live:
                live_by_qn.setdefault(_norm(ls.qualified_name), []).append(ls)

            for s in old_syms:
                cands = live_by_qn.get(s.get("res_qname") or _norm(s.get("qualified_name")), [])
                if not cands:
                    fate = "removed"
                    fp = s.get("fingerprint")
                    if fp:
                        m = _match_by_fingerprint(live, lines, fp)
                        if m is not None:
                            fate = f"renamed -> {m.qualified_name or m.name}"
                    callers, possible = _callers_of(store, idx, s)
                    if callers or possible:
                        removed.append({"ref": _sym_ref(s), "fate": fate,
                                        "callers": callers, "callers_possible": possible})
                    continue
                stored_sh = s.get("signature_hash")
                if stored_sh and not any(signature_hash_for(c.signature) == stored_sh
                                         for c in cands):
                    callers, possible = _callers_of(store, idx, s)
                    if callers or possible:
                        signature_changed.append(
                            {"ref": _sym_ref(s), "old_sig": s.get("signature") or "",
                             "new_sig": cands[0].signature or "",
                             "callers": callers, "callers_possible": possible})

        dangling, seen = [], set()

        def _add_import(name: str, why: str):
            for e in store.edges_by_target_name(repo_id, name, "imports"):
                src = idx.by_id.get(e["source_id"])
                ip = src["path"] if src else None
                if ip and (ip, name) not in seen:
                    seen.add((ip, name))
                    dangling.append({"importer_path": ip, "name": name, "why": why,
                                     "confirm": _confirm_grep(name, ip)})

        for dp in deleted_paths:
            _add_import(Path(dp).stem, "module/file deleted from the index")
        for r in removed:
            bare = _norm(r["ref"].split("::", 1)[-1]).split(".")[-1] if "::" in r["ref"] else ""
            if bare:
                _add_import(bare, "imported symbol removed/renamed")

        return {"status": "ok", "changed_files": changed_paths, "deleted_files": deleted_paths,
                "removed": removed, "signature_changed": signature_changed,
                "dangling_imports": dangling, "unanalyzable": unanalyzable,
                "edges_available": edges_available}
    finally:
        if own_store:
            store.close()


def _caller_lines(callers: list, possible: list) -> list:
    out = []
    for c in callers:
        out.append(f"  - {c['ref']}  ~{c['confidence']} — {c.get('evidence', '')}")
    if possible:
        out.append(f"  possible (unverified — grep to confirm) ({len(possible)}):")
        for c in possible:
            out.append(f"  - {c['ref']}  ~{c['confidence']}")
            if c.get("confirm"):
                out.append(f"    confirm: {c['confirm']}")
    return out


def render_breakage(result: dict) -> str:
    status = result.get("status")
    if status == "disabled":
        return "Breakage check disabled (set retrieval.breakage_check: true)."
    if status == "empty":
        return ("# Breakage check (FLOOR) — no changed files relative to the index\n"
                "> Nothing to check. If you just edited, the auto-reindexer may have already "
                "absorbed the change (retrieval.auto_reindex). This primitive only sees edits "
                "NOT yet indexed; run it on the CLI or with auto_reindex=false for a reliable "
                "post-edit check.")

    removed = result.get("removed", [])
    sig = result.get("signature_changed", [])
    dangling = result.get("dangling_imports", [])
    unanalyzable = result.get("unanalyzable", [])
    n_changed = len(result.get("changed_files", [])) + len(result.get("deleted_files", []))
    out = [f"# Breakage check (FLOOR) — {n_changed} changed file(s) since index", _NOT_COVERED]

    if not (removed or sig or dangling):
        out.append(f"\nNo static breakage found among {n_changed} changed file(s).")

    if removed:
        out.append(f"\n## Removed/renamed callees — call-sites now dangling ({len(removed)})")
        for r in removed:
            out.append(f"- {r['ref']}  [{r['fate']}]  — was called by:")
            out += _caller_lines(r["callers"], r["callers_possible"])
    if sig:
        out.append(f"\n## Signature changed — call-sites to re-check ({len(sig)})")
        for s in sig:
            out.append(f"- {s['ref']}\n    old: `{s['old_sig']}`\n    new: `{s['new_sig']}`"
                       f"\n  (arg count is not stored — each call-site is 'likely break, verify')")
            out += _caller_lines(s["callers"], s["callers_possible"])
    if dangling:
        out.append(f"\n## Dangling imports ({len(dangling)})")
        for d in dangling:
            out.append(f"- {d['importer_path']} imports `{d['name']}` — {d['why']} "
                       f"(name-based; verify it isn't re-exported)\n    confirm: {d['confirm']}")
    if unanalyzable:
        out.append(f"\n## Unanalyzable changed files ({len(unanalyzable)}) — NOT a clean pass")
        out += [f"- {p} (not parseable / language without static edges)" for p in unanalyzable]
    return "\n".join(out)
