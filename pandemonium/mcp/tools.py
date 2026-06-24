"""MCP tool implementations backed by a shared, long-lived context.

The MCP server is a long-running process, so we keep ONE Retriever / ContextPacker
(and thus one loaded embedding model) for the whole session instead of reopening per
call. All tools are read-only except `repo_reindex_changed`. Every call is audit-logged.
"""

from __future__ import annotations

import functools
import os
import time
from typing import List, Optional

from pandemonium import service
from pandemonium.indexer.auto_reindex import AutoReindexer
from pandemonium.logging.audit import AuditLog
from pandemonium.logging.trace import trace
from pandemonium.mapping import build_repo_map, render_repo_map
from pandemonium.models import SearchResult
from pandemonium.retrieval.context_packer import ContextPacker
from pandemonium.retrieval.hybrid_search import Retriever
from pandemonium.retrieval.symbol_search import lookup_symbol
from pandemonium.retrieval.tests_finder import find_tests
from pandemonium.session import SessionLedger


def _format_tagline(tags) -> str:
    if not tags:
        return ""
    bits = []
    for key in ("side_effects", "entrypoints", "domain"):
        vals = tags.get(key) or []
        if vals:
            bits.append(f"{key}={','.join(vals)}")
    return " | ".join(bits)


def _confidence_banner(assessment) -> str:
    """A one-line trust signal above low-confidence card lists (ROADMAP v2 Step 2). High
    confidence prints nothing — the edge is the token budget, so we only spend tokens when
    the agent needs to be warned NOT to anchor on these results."""
    if not assessment or assessment.get("confidence") != "low":
        return ""
    missing = ", ".join(assessment.get("missing_terms") or [])
    note = f"⚠ Low-confidence retrieval: {assessment.get('reason', '')}".rstrip()
    if assessment.get("fanned_out"):
        note += f"\n  (already fanned out to {assessment['fanned_out']})"
    if missing:
        note += (f"\n  These cards may not address: {missing}. Confirm with repo_get, or "
                 "grep a distinctive term before trusting them.")
    return note + "\n"


_EDITABLE_SCOPES = {"function", "method", "class", "struct", "interface", "enum"}


def _next_move(r: SearchResult, low_confidence: bool) -> str:
    """A terse, confidence-conditional next action (#8). ONE line, ≤2 actions — never a
    5-item menu (the token budget is the whole edge). High-confidence symbol cards point at
    the impact-first default; low-confidence cards point at verification instead."""
    if low_confidence:
        return "repo_get(ref) to verify — low-confidence result, don't anchor on it"
    if not r.is_complete_unit and r.parent_ref:
        return f"repo_get(ref) — partial block; auto-expands to complete unit {r.parent_ref}"
    is_symbol = (r.scope == "symbol") or (r.chunk_type in _EDITABLE_SCOPES)
    if is_symbol:
        return "repo_get(ref) to read; repo_impact(ref) before editing it"
    return "repo_get(ref) to read"


def _format_results(results: List[SearchResult], assessment=None) -> str:
    """Card list: ref + summary + tags, NO raw code. Each card ends in a terse, confidence-
    conditional next move (#8). Fetch code via repo_get(ref)."""
    if not results:
        return "No results. The repository may not be indexed yet (run repo_reindex_changed)."
    lines = []
    banner = _confidence_banner(assessment)
    if banner:
        lines.append(banner)
    low = bool(assessment and assessment.get("confidence") == "low")
    for i, r in enumerate(results, 1):
        ref = r.ref or f"{r.path}:{r.start_line}-{r.end_line}"
        partial = "" if r.is_complete_unit else " partial"  # cAST: a sub-block, not a whole unit
        # #7.4 size hint: line count lets the agent choose a cost-aware fetch (view=signature
        # for a big symbol) instead of blindly repo_get-ing the whole thing.
        loc = (r.end_line - r.start_line + 1) if (r.start_line and r.end_line) else 0
        size = f" ~{loc}L" if loc else ""
        lines.append(f"{i}. ref={ref} [{r.scope or r.chunk_type}{partial}{size}] score={r.score}")
        if r.summary:
            lines.append(f"   {r.summary}")
        tagline = _format_tagline(r.tags)
        if tagline:
            lines.append(f"   {tagline}")
        lines.append(f"   ({r.reason}) → {_next_move(r, low)}")
    return "\n".join(lines)


def _format_symbols(matches: List[dict]) -> str:
    if not matches:
        return "No matching symbol."
    lines = []
    for m in matches:
        lines.append(f"{m['name']} [{m['type']}] {m['path']}:{m['start_line']}-{m['end_line']}")
        if m["signature"]:
            lines.append(f"   {m['signature']}")
        if m["summary"]:
            lines.append(f"   {m['summary']}")
    return "\n".join(lines)


class ToolContext:
    # Tool calls are traced (START / OK|FAILED + elapsed) to stderr — Claude Code captures
    # server stderr into its mcp-logs, so a hang is followable live — and a completion
    # record (`mcp_tool_done`) is appended to audit.log. Audit previously logged only the
    # ENTRY of each call, which is exactly why the 2026-06-20 import deadlock was invisible
    # (entry recorded, nothing after).
    _TRACED_TOOLS = (
        "repo_map", "repo_search", "repo_symbol", "repo_get", "repo_context_pack",
        "repo_find_tests", "repo_reindex_changed", "repo_session", "repo_graph",
        "repo_impact", "repo_edit_plan", "repo_logic_map", "repo_brief", "repo_changed",
        "repo_check",
    )

    def __init__(self, settings, embedder=None):
        self.settings = settings
        self.audit = AuditLog(settings.audit_log_path)
        self.session_id = f"mcp-{os.getpid()}"
        self.ledger = SessionLedger.open(settings, self.session_id)
        self._retriever: Optional[Retriever] = None
        self._graph_index = None  # cached repo-wide GraphIndex; rebuilt only after a reindex
        self._packer: Optional[ContextPacker] = None
        # A pre-warmed embedder (loaded single-threaded before serving) is reused here so the
        # heavy first `import sentence_transformers` never fires on the event-loop thread
        # while LanceDB's background thread runs — that race deadlocks the native loader.
        self._embedder = embedder
        # Auto-indexer (server self-heal): read tools refresh the index for files changed
        # mid-session BEFORE serving, so edits show up without a manual repo_reindex_changed.
        # Reuses the warm embedder; runs synchronously (no watcher thread). Config-gated.
        sec = settings.section("retrieval")
        self._auto = (AutoReindexer(settings, embedder=embedder,
                      min_interval=float(sec.get("auto_reindex_min_interval", 2.0)))
                      if sec.get("auto_reindex", True) else None)
        if self._auto is not None:
            self._auto.prime()
        self._install_tracing()

    def _auto_refresh(self) -> None:
        """Self-heal the index before a read tool runs (debounced; no-op if nothing changed)."""
        if self._auto is None:
            return
        try:
            stats = self._auto.maybe_refresh()
            if stats and (stats.indexed or stats.deleted):
                trace(f"auto-reindex: indexed={stats.indexed} deleted={stats.deleted} → reset")
                self._reset()
        except Exception as e:  # never let a refresh break the tool call
            trace(f"auto-reindex skipped: {e!r}")

    def _install_tracing(self) -> None:
        """Wrap each tool method on the instance with START/OK/FAILED + elapsed logging.
        Done on the instance (not the class) so the MCP tool *schema* — built from the
        thin server.py wrappers — is untouched."""
        for name in self._TRACED_TOOLS:
            raw = getattr(self, name)

            @functools.wraps(raw)
            def wrapper(*args, _name=name, _raw=raw, **kwargs):
                # Self-heal the index for mid-session edits before any read tool (not the
                # write tool, which reindexes itself, nor the memory-only session ledger, nor
                # repo_check — auto-reindexing would absorb the very edit delta it inspects).
                if _name not in ("repo_reindex_changed", "repo_session", "repo_check"):
                    self._auto_refresh()
                trace(f"tool {_name} START")
                t0 = time.perf_counter()
                try:
                    result = _raw(*args, **kwargs)
                    ms = (time.perf_counter() - t0) * 1000.0
                    trace(f"tool {_name} OK in {ms:.0f}ms")
                    self.audit.log("mcp_tool_done", tool=_name, ms=round(ms, 1), ok=True)
                    return result
                except Exception as e:
                    ms = (time.perf_counter() - t0) * 1000.0
                    trace(f"tool {_name} FAILED in {ms:.0f}ms: {e!r}")
                    self.audit.log("mcp_tool_done", tool=_name, ms=round(ms, 1),
                                   ok=False, error=repr(e))
                    raise

            setattr(self, name, wrapper)

    @property
    def retriever(self) -> Retriever:
        if self._retriever is None:
            self._retriever = Retriever(self.settings, embedder=self._embedder)
        return self._retriever

    @property
    def graph_index(self):
        """Repo-wide GraphIndex (name->symbol resolution maps), cached across
        graph/impact/edit_plan/logic_map/brief calls instead of rebuilt from all_symbols
        every time. Built once; invalidated by _reset() after a reindex. Self-contained after
        construction (never reads the store again), so reuse across calls is safe."""
        if self._graph_index is None:
            from pandemonium.graph import GraphIndex
            self._graph_index = GraphIndex(self.retriever.sqlite, self.retriever.repo_id)
        return self._graph_index

    @property
    def packer(self) -> ContextPacker:
        if self._packer is None:
            self._packer = ContextPacker(self.settings, retriever=self.retriever)
        return self._packer

    def _reset(self) -> None:
        if self._retriever is not None:
            self._retriever.close()
        self._retriever = None
        self._packer = None
        self._graph_index = None  # symbols may have changed -> rebuild lazily next use

    # -- tools --------------------------------------------------------------
    def repo_map(self, mode: str = "default") -> str:
        self.audit.log("mcp_tool", tool="repo_map", mode=mode)
        return render_repo_map(build_repo_map(self.settings, self.retriever.sqlite, mode=mode))

    def repo_search(self, query: str, top_k: int = 10, mode: str = "") -> str:
        self.audit.log("mcp_tool", tool="repo_search", query=query, mode=mode)
        results, assessment = self.retriever.search_assessed(query, top_k=top_k,
                                                             mode=mode or None)
        self.ledger.record_query(query)
        self.ledger.record_returned_refs([r.ref for r in results if r.ref])
        return _format_results(results, assessment)

    def repo_symbol(self, symbol_name: str) -> str:
        self.audit.log("mcp_tool", tool="repo_symbol", name=symbol_name)
        matches = lookup_symbol(self.retriever.sqlite, self.retriever.repo_id, symbol_name)
        return _format_symbols(matches)

    def repo_get(self, ref: str, expand: str = "exact", view: str = "full") -> str:
        self.audit.log("mcp_tool", tool="repo_get", ref=ref, expand=expand, view=view)
        refetched = self.ledger.already_fetched(ref)  # #7.5: consult BEFORE recording this fetch
        self.ledger.record_fetch(ref)
        from pandemonium import refs
        repo_id = self.retriever.repo_id
        row = self.retriever.sqlite.chunk_by_ref(repo_id, ref)
        # cAST delivery contract: a partial ast_block child auto-upgrades to its complete
        # parent (expand="block" opts out) so the agent never reasons from half a unit.
        resolved = refs.resolve_with_upgrade(
            self.settings.repo_root, ref, row,
            fetch_row=lambda r: self.retriever.sqlite.chunk_by_ref(repo_id, r),
            expand=expand, view=view)
        if resolved is None:
            return f"Could not resolve ref: {ref}"
        view_note = "" if resolved.view in ("full", None) else f" view={resolved.view}"
        head = (f"{resolved.path}:{resolved.start_line}-{resolved.end_line} "
                f"[{resolved.scope}] expand={resolved.expand}{view_note}")
        notes = []
        if resolved.stale:
            self.ledger.record_stale(ref)
            notes.append("stale: file changed since indexing — content differs, re-fetch/reindex")
        if resolved.ambiguous:
            notes.append("ambiguous: multiple symbols share this name; best-effort pick")
        if resolved.resolved_by == "fingerprint":
            notes.append("re-found by body after a likely rename — the ref name is outdated")
        if resolved.note:
            notes.append(resolved.note)
        if resolved.truncated:  # #7.4: the max_lines clamp used to be silent
            shown = resolved.end_line - resolved.start_line + 1
            notes.append(f"truncated: showing {shown} of {shown + resolved.truncated} lines "
                         f"(max_lines cap) — narrow with view=lines:a-b")
        if refetched:  # #7.5: re-fetch awareness
            notes.append("re-fetch: you already fetched this ref earlier this session")
        # #8: content-blind secret redaction on the OUTPUT path (never relay raw secrets to the model)
        from pandemonium.secret_filter import redact_secrets
        code, n_secrets = redact_secrets(resolved.code)
        if n_secrets:
            notes.append(f"redacted {n_secrets} secret-like value(s) from the output")
        if notes:
            head += "  (" + "; ".join(notes) + ")"
        if resolved.decl_ref:  # C++ out-of-line def: also declared in a sibling header
            head += f"\n  declared in {resolved.decl_ref}"
        return f"{head}\n\n{code}"

    def repo_context_pack(self, task: str, token_budget: int = 4000, mode: str = "") -> str:
        self.audit.log("mcp_tool", tool="repo_context_pack", task=task,
                       budget=token_budget, mode=mode)
        return self.packer.build(task, token_budget=token_budget, mode=mode or None)

    def repo_find_tests(self, target: str) -> str:
        self.audit.log("mcp_tool", tool="repo_find_tests", target=target)
        found = find_tests(self.retriever.sqlite, self.retriever.repo_id, target)
        return "\n".join(f"- {p}" for p in found) or "No related tests found."

    def repo_reindex_changed(self) -> str:
        self.audit.log("mcp_tool", tool="repo_reindex_changed")
        from pandemonium.indexer.index_runner import run_index
        stats = run_index(self.settings, incremental=True, compact=False)
        self._reset()  # force stores to reopen so subsequent reads see fresh data
        msg = (f"Reindexed: indexed={stats.indexed} skipped={stats.skipped} "
               f"deleted={stats.deleted} symbols={stats.symbols} chunks={stats.chunks}")
        if stats.skipped_too_large:
            msg += (f"\nwarning: {stats.skipped_too_large} file(s) skipped for exceeding "
                    f"indexing.max_file_bytes — they are NOT indexed.")
        return msg

    def repo_session(self, action: str = "get", field: str = "", value: str = "",
                     ref: str = "") -> str:
        self.audit.log("mcp_tool", tool="repo_session", action=action)
        if action == "resume":
            from pandemonium.session import latest_prior_ledger, render_resume
            led, mtime = latest_prior_ledger(self.settings, self.session_id)
            if led is None:
                return "No prior session to resume."
            last = None
            if mtime:
                import datetime
                last = datetime.datetime.fromtimestamp(mtime).isoformat(timespec="minutes")
            return render_resume(self.settings, led, last_active=last)
        if action == "note" and field and value:
            from pandemonium.session import LEDGER_FIELDS
            if field not in LEDGER_FIELDS:
                # record_fact/add silently drop unknown fields — don't report a dropped
                # write as success (that would be confident-wrong + silent memory loss).
                return (f"unknown field '{field}' — nothing recorded. Valid fields: "
                        + ", ".join(LEDGER_FIELDS))
            if ref:  # anchor the fact so resume can re-validate it (Step 7)
                from pandemonium.indexer.hasher import read_file
                from pandemonium import refs as refs_mod
                path = refs_mod.parse_ref(ref)[0]
                read = read_file(self.settings.repo_root / path)
                self.ledger.record_fact(field, value, ref=ref,
                                        file_hash=(read[2] if read else None))
                return f"recorded to {field} (anchored to {ref})"
            self.ledger.add(field, value)
            return f"recorded to {field}"
        return self.ledger.render()

    def repo_graph(self, ref: str, evidence: bool = False) -> str:
        self.audit.log("mcp_tool", tool="repo_graph", ref=ref, evidence=evidence)
        from pandemonium.graph import render_graph, repo_graph
        g = repo_graph(self.settings, ref, graph=self.graph_index)
        if g:  # remember confidently-resolved edges ("A -> B" == A calls B)
            self.ledger.record_edges(
                [f"{g['ref']} -> {c['ref']}" for c in g.get("callees", [])]
                + [f"{c['ref']} -> {g['ref']}" for c in g.get("callers", [])])
        return render_graph(g, show_evidence=evidence) if g else f"Ref not found in the graph: {ref}"

    def repo_impact(self, ref: str) -> str:
        self.audit.log("mcp_tool", tool="repo_impact", ref=ref)
        from pandemonium.graph import render_impact, repo_impact
        g = repo_impact(self.settings, ref, graph=self.graph_index)
        if g:  # direct callers are confident edges into this ref
            self.ledger.record_edges([f"{d} -> {g['ref']}" for d in g.get("direct", [])])
        return render_impact(g) if g else f"Ref not found in the graph: {ref}"

    def repo_check(self, target: str = "") -> str:
        self.audit.log("mcp_tool", tool="repo_check", target=target)
        if not self.settings.section("retrieval").get("breakage_check", False):
            from pandemonium.breakage import render_breakage
            return render_breakage({"status": "disabled"})
        from pandemonium.breakage import breakage_check, render_breakage
        result = breakage_check(self.settings, target or None, graph=self.graph_index)
        for r in result.get("removed", []) + result.get("signature_changed", []):
            self.ledger.record_edges([f"{c['ref']} -> {r['ref']}" for c in r.get("callers", [])])
        return render_breakage(result)

    def repo_edit_plan(self, ref: str) -> str:
        self.audit.log("mcp_tool", tool="repo_edit_plan", ref=ref)
        from pandemonium.graph import edit_plan, render_edit_plan
        p = edit_plan(self.settings, ref, graph=self.graph_index)
        if p:  # record the confident caller edges, like repo_impact
            self.ledger.record_edges([f"{d} -> {p['ref']}" for d in p["callers_direct"]])
        return render_edit_plan(p) if p else f"Ref not found in the graph: {ref}"

    def repo_logic_map(self, topic: str) -> str:
        self.audit.log("mcp_tool", tool="repo_logic_map", topic=topic)
        from pandemonium.graph import render_logic_map, repo_logic_map
        g = repo_logic_map(self.settings, topic, graph=self.graph_index, retriever=self.retriever)
        return render_logic_map(g) if g else "No matches for that topic."

    def repo_brief(self, task: str) -> str:
        self.audit.log("mcp_tool", tool="repo_brief", task=task)
        from pandemonium.brief import render_brief, repo_brief
        # Reuse the shared retriever so the embedding model isn't reloaded per call.
        b = repo_brief(self.settings, task, retriever=self.retriever, graph=self.graph_index)
        self.ledger.record_query(task)
        if b.get("anchored") and b.get("verified"):  # record confident caller edges
            v = b["verified"]
            anchor = v["anchor"]
            self.ledger.record_edges(
                [f"{c} -> {anchor}" for c in v.get("callers_production", [])]
                + [f"{c} -> {anchor}" for c in v.get("callers_test", [])])
        return render_brief(b)

    def repo_changed(self, refs: str = "") -> str:
        self.audit.log("mcp_tool", tool="repo_changed")
        ref_list = [x.strip() for x in refs.replace(",", " ").split() if x.strip()] or None
        rows = service.staleness(self.settings, ref_list)
        stale = [r for r in rows if r["stale"]]
        self.ledger.record_stale_refs([r["ref"] for r in stale])
        if not stale:
            return f"All {len(rows)} checked file(s) are current (in sync with the index)."
        return ("STALE (changed since indexing — re-fetch or reindex):\n"
                + "\n".join(f"- {r['ref']}  [{r['state']}]" for r in stale))
