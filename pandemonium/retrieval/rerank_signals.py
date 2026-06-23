"""Structural rerank SIGNALS for retrieval — OBSERVE-ONLY (Patch 2).

Pure, read-only classifiers over a `SearchResult` (plus the call graph for the delegator
signal). They are surfaced by `evals/run_eval.py --signals` so the three planned fix signals —
thin dispatch DELEGATOR, code-vs-PROSE, and constant DENSITY — can be SEEN on real queries
BEFORE any of them is wired into scoring (`hybrid_search._base_search`). Nothing here changes
ranking; `run()` / `--gate` are untouched.

Design law (see evals/RESULTS.md): every threshold below is TUNABLE and must be validated on
EXTERNAL repos via `--tasks` crossover — never tuned to the 15-query dogfood gold. The signals
themselves key off UNIVERSAL structure (call-graph delegation, `language`, `symbol_type`), not
repo-specific names, so they generalize across codebases.
"""

from __future__ import annotations

# Prose = natural-language files with no code symbols. `language` is set by
# language_detector, so this is schema-native and cross-language.
PROSE_LANGUAGES = frozenset({"markdown", "text", "rst", "asciidoc"})
# Real implementation kinds a wrapper would forward TO (and that deserve "full" visibility).
CODE_SYMBOL_KINDS = frozenset({"function", "method", "class", "interface", "enum",
                               "struct", "delegate", "event"})
# Data-ish symbols emitted by the deeper extraction (module constants / type aliases).
DATA_SYMBOL_KINDS = frozenset({"constant", "variable", "type"})

# --- TUNABLE thresholds (calibrate on external repos, NOT the dogfood gold) ---
THIN_DELEGATOR_LOC = 15   # a forwarder body rarely exceeds this many lines
BULK_CONSTANT_LOC = 8     # a constant longer than this is bulk DATA (GOLD/MODELS), not config
DELEGATOR_MAX_DEPTH = 4   # follow a wrapper -> wrapper -> implementation chain this deep

# Plumbing/util callee NAMES that are setup/IO/config/logging — NOT the forwarded
# implementation. Universal verbs (not repo-specific); after receiver-aware resolution these
# are the only false-positives left, so a small stoplist suffices. TUNABLE / ablatable.
PLUMBING_CALLEES = frozenset({
    "load", "get", "set", "log", "close", "open", "print", "echo", "secho", "style",
    "section", "append", "add", "join", "format", "write", "read", "dumps", "loads",
    "confirm", "prompt", "warning", "error", "info", "debug", "now_iso", "build_ref",
})


def _loc(result) -> int:
    try:
        return int(result.end_line) - int(result.start_line) + 1
    except (TypeError, ValueError):
        return 0


def content_type(result) -> str:
    """code | prose | data | generated — from FREE fields (path, language, chunk_type)."""
    path = result.path or ""
    if "/_assets/" in path or path.endswith((".min.js", ".min.css")):
        return "generated"
    if (result.language or "") in PROSE_LANGUAGES:
        return "prose"
    if (result.chunk_type or "") in DATA_SYMBOL_KINDS:
        return "data"
    return "code"


def search_visibility(result) -> str:
    """full | low_priority | graph_only — the constant/generated DENSITY policy (Patch 5).

    Code symbols and prose stay 'full' here (prose is demoted by the code-vs-prose signal, not
    by visibility). Only generated assets and bulk data constants are demoted as search cards;
    a SMALL config constant (e.g. an embedding model name / dim) stays discoverable."""
    ct = content_type(result)
    if ct == "generated":
        return "graph_only"
    if ct == "data":
        if (result.chunk_type or "") in {"constant", "variable"} and _loc(result) > BULK_CONSTANT_LOC:
            return "graph_only"     # bulk data list/table (GOLD, MODELS, TASKS, DEFAULTS-as-blob)
        return "low_priority"       # small config constant / type alias — keep findable
    return "full"


def _looks_test(path: str) -> bool:
    p = path or ""
    base = p.rsplit("/", 1)[-1]
    return p.startswith("tests/") or "/tests/" in p or base.startswith("test_") or base.endswith("_test.py")


def _sym_loc(sym) -> int:
    return int(sym.get("end_line") or 0) - int(sym.get("start_line") or 0) + 1


# A delegator forwards to at most this many implementations (1 = pure forward; 2 covers the
# common fetch+render wrapper). A symbol calling MORE distinct implementations is doing real
# work — it is a LEAF, not a forwarder. This is what stops the chain at find_tests/build_repo_map
# instead of chasing into their helper calls.
DELEGATOR_MAX_IMPL_CALLEES = 2
FORWARD_TARGET_KINDS = frozenset({"function", "method"})  # delegate TO logic, not constructors


def _impl_callees(sym, store, idx):
    """Receiver-RESOLVED (collision-proof) project function/method callees of a symbol DICT,
    minus plumbing names, test files, and self. Using graph `_callees_of` (not name matching)
    is what removes the v0 `get`/`load`/`log` false-positives; restricting to function/method
    drops class-constructor noise (SqliteStore, Retriever, ...)."""
    from pandemonium.graph import _callees_of  # lazy: avoid any import cycle, gate the cost
    callees, _ambig = _callees_of(store, idx, sym)
    out = {}
    for c in callees:
        if c["name"] in PLUMBING_CALLEES or c["id"] == sym["id"]:
            continue
        tgt = idx.by_id.get(c["id"])
        if tgt and tgt.get("symbol_type") in FORWARD_TARGET_KINDS and not _looks_test(tgt.get("path", "")):
            out[tgt["id"]] = tgt
    return list(out.values())


def _is_forwarder(sym, store, idx) -> bool:
    """A thin function/method that forwards to 1-2 implementations — not a multi-call worker."""
    if sym.get("symbol_type") not in ("function", "method"):
        return False
    if not (0 < _sym_loc(sym) <= THIN_DELEGATOR_LOC):
        return False
    return 1 <= len(_impl_callees(sym, store, idx)) <= DELEGATOR_MAX_IMPL_CALLEES


def delegation_leaves(sym, store, idx):
    """Follow forwarder->forwarder forwards from a wrapper symbol DICT to the LEAF
    implementations — each reachable callee that is NOT itself a forwarder (a multi-call
    worker, a non-thin function, or a non-function). e.g. cli::tests -> service.tests ->
    tests_finder.find_tests: find_tests calls several helpers, so it is the LEAF, not chased."""
    leaves, seen = {}, {sym["id"]}
    stack = [(sym, 0)]
    while stack:
        cur, depth = stack.pop()
        for tgt in _impl_callees(cur, store, idx):
            if tgt["id"] in seen:
                continue
            seen.add(tgt["id"])
            if depth < DELEGATOR_MAX_DEPTH and _is_forwarder(tgt, store, idx):
                stack.append((tgt, depth + 1))  # still a forwarder -> keep following
            else:
                leaves[tgt["id"]] = tgt          # leaf implementation
    return list(leaves.values())


def delegator_leaves(result, store, idx):
    """If `result` is a thin dispatch wrapper, the LEAF implementation symbol DICTs it forwards
    to (receiver-resolved, chain-followed to the real work) — else []. `idx` is a
    `graph.GraphIndex` (build it ONCE per search). OBSERVE-ONLY here; Patch 3b promotes these
    for implementation-intent queries."""
    if not result.symbol_id:
        return []
    sym = idx.by_id.get(result.symbol_id)
    if sym is None or not _is_forwarder(sym, store, idx):
        return []
    return delegation_leaves(sym, store, idx)


# --- Query intent: the OVERFIT-PRONE heuristic. Keep it SOFT + ablatable; judge ONLY on the
# external crossover, never add a keyword to make a dogfood query pass. Entrypoint is checked
# first so "cli index command" keeps the wrapper; impl-intent prefers the implementation. ---
_ENTRYPOINT_HINTS = ("cli", "command", "endpoint", "route", "handler", "mcp", "api", "subcommand")
_PROSE_HINTS = ("docs", "documentation", "readme", "overview", "design", "rationale",
                "guide", "explain")
_IMPL_HINTS = ("implemented", "built", "generated", "detect", "computed", "logic", "algorithm",
               "how does", "how are", "where is", "where does", "function", "class", "method")


def query_intent(query: str) -> str:
    """entrypoint | prose | code | mixed — a deliberately conservative keyword heuristic."""
    q = (query or "").lower()
    if any(h in q for h in _ENTRYPOINT_HINTS):
        return "entrypoint"
    if any(h in q for h in _PROSE_HINTS):
        return "prose"
    if any(h in q for h in _IMPL_HINTS):
        return "code"
    return "mixed"


# --- Patch 4/5 penalties (multiplicative, soft — NOT hard filters). TUNABLE; default config
# keeps the reranker OFF, so these only ever apply when explicitly enabled + measured. ---
PROSE_PENALTY = 0.5        # Patch 4: a prose card can't outrank a code symbol on a code query
GRAPH_ONLY_PENALTY = 0.4   # Patch 5: bulk-data / generated constant cards are not discovery hits


def apply_penalties(results, query, *, prose=True, density=True):
    """Soft, deterministic structural rerank of a result POOL (Patch 4 + 5). Mutates each
    result's score by a multiplicative factor and re-sorts (stable chunk_id tiebreak, matching
    `hybrid_search`). Patch 4 demotes PROSE only on a CODE-intent query (docs stay first-class
    for doc-intent / mixed queries); Patch 5 demotes graph_only cards (bulk data constants +
    generated assets) on any query — an exact-name lookup never reaches here (short-circuited).
    Caller GATES this (config `retrieval.rerank`, off by default) and excludes the channel
    baselines + scope-filtered searches, so those stay byte-identical."""
    code_intent = query_intent(query) == "code"
    for r in results:
        factor = 1.0
        if prose and code_intent and content_type(r) == "prose":
            factor *= PROSE_PENALTY
        if density and search_visibility(r) == "graph_only":
            factor *= GRAPH_ONLY_PENALTY
        if factor != 1.0:
            r.score = round(r.score * factor, 4)
    results.sort(key=lambda x: (-x.score, x.chunk_id))
    return results
