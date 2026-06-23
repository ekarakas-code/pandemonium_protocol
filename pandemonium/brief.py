"""repo_brief — the capstone synthesis tool (ROADMAP v2, Step 5).

One entrypoint for "I'm about to work on <task>": it interprets the task, names the
LIKELY targets, sketches the call flow, and — for the one target it can anchor on —
returns the VERIFIED graph facts (impact, callers, tests, staleness, risks) plus a
confidence-gated next action.

Governing principle (ROADMAP v2): **a confident-wrong brief is worse than no brief.** So
the output is HARD-SEPARATED into two blocks that never blur:

  * **HEURISTIC** — task interpretation, likely targets, likely call flow. These are
    GUESSES from semantic search; the render says so in the header and per section.
  * **VERIFIED** — graph facts about the chosen *anchor* symbol. Framed as "verified
    about the anchor", NOT "proof the anchor is the right target". Only confidently-
    resolved graph edges go here; *possible* callers and *name-matched-only* tests are
    explicitly demoted to their own ⚠ sections — they are guesses, not verified.

The pivot from heuristic to verified is the ANCHOR: the top likely target, chosen only
when confidence is high enough to justify presenting an authoritative impact block. The
behavioral fork is binary — at LOW/NONE confidence the verified block is **withheld** (a
verified block on a guessed-wrong symbol is exactly the "confident liar" the principle
forbids); the brief degrades to ranked candidates + a disambiguation action. That refusal
is the feature, not a gap.

Anchor confidence (display: high | medium | low | none) is led by, in order: staleness has
veto power via a post-hoc cap (clean HIGH is never shown over an index whose anchor file
changed); an exact symbol match is strongest UNLESS several symbols share that exact name
(then it's ambiguous, not confident); the Step-2 retrieval assessment; whether the anchor's
language even has extracted graph edges (an empty impact from a no-edge language is NOT
"safe"); and how much of the query's own domain vocabulary the anchor carries. The hybrid
score margin to the next ANCHORABLE candidate is a *soft tiebreak only* (hybrid scores
aren't calibrated), never a standalone threshold.

No LLM, no network beyond the embedding query the search already runs.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from pandemonium.retrieval import confidence

# Display tiers at which we ANCHOR (show a verified block). low/none withhold it.
_ANCHORED = {"high", "medium"}
_EDITABLE_SCOPES = {"function", "method", "class", "struct", "interface", "enum"}
# Soft tiebreak only: a clear margin can lift MEDIUM -> HIGH, but margin alone never
# decides a tier (hybrid scores are uncalibrated; see hybrid_search docstring).
_MARGIN_CLEAR = 0.10


def _ref_path(ref: str) -> str:
    """The file path of a ref, whether it's `path::Qualified.Name` or `path:line-range`."""
    return (ref or "").split("::", 1)[0].split(":", 1)[0]


def _is_symbol_card(r) -> bool:
    """A result we can ANCHOR on: it resolves to a graph symbol (has a qualified_name +
    ref and a symbol-ish scope), so edit_plan/impact can run against it. File/block cards
    can't be anchored — you can't compute a caller graph for half a file."""
    return bool(r.qualified_name and r.ref) and (
        r.scope == "symbol" or (r.chunk_type in _EDITABLE_SCOPES))


def _pick_anchor(results) -> Tuple[Optional[object], int]:
    """The first result that is a resolvable symbol, with its rank. Conservative by
    design: we anchor on the top *symbol*, not the top hit (which may be a file card)."""
    for i, r in enumerate(results):
        if _is_symbol_card(r):
            return r, i
    return None, -1


def _coverage(anchor, qset: set) -> Tuple[float, set]:
    """Fraction of the query's domain terms the ANCHOR ITSELF carries (its name, summary,
    tags, qualified_name). Step-2's assessment judges the top-5 collectively; this asks
    the sharper question — does the symbol we're about to call authoritative actually look
    like the thing the user asked about? `qset` MUST already be split to result_terms'
    granularity (see repo_brief), or a compound query term can never intersect."""
    if not qset:
        return 0.0, set()
    have = qset & confidence.result_terms(anchor)
    return len(have) / len(qset), have


def _anchor_confidence(assessment: dict, anchor, qset: set, margin: float,
                       edges_available: bool, exact_collisions: int = 1
                       ) -> Tuple[str, List[str]]:
    """-> (tier, reasons). Tier in {high, medium, low, none}. Order of precedence:
    exact-symbol match (UNLESS the name collides across several symbols → ambiguous, not
    confident) > no-edges-for-language > low retrieval confidence > empty query terms >
    the anchor's own domain-term coverage (with score margin as a soft MEDIUM->HIGH
    tiebreak). Staleness is applied as a post-hoc cap in repo_brief, not here."""
    if anchor is None:
        return "none", ["no resolvable symbol in the top results to anchor on"]
    if assessment.get("reason") == "exact symbol match":
        if exact_collisions > 1:
            return "medium", [f"{exact_collisions} symbols share this exact name — "
                              "confirm which one before trusting the impact"]
        return "high", ["the query is an exact symbol name — no interpretation needed"]
    if not edges_available:
        return "low", ["graph edges aren't extracted for this language, so impact can't "
                       "be verified — the target stays a guess"]
    if assessment.get("confidence") == "low":
        return "low", ["retrieval is low-confidence: "
                       + (assessment.get("reason") or "top hits don't cover the query")]
    if not qset:
        return "low", ["the query has no distinctive terms to match against the anchor"]
    cov, have = _coverage(anchor, qset)
    reasons = [f"anchor carries {len(have)}/{len(qset)} of the query's domain term(s)"]
    if cov >= 0.999:
        return "high", reasons
    if cov >= 0.5:
        if margin >= _MARGIN_CLEAR:
            reasons.append(f"and clearly out-scores the next candidate (+{margin:.2f})")
            return "high", reasons
        return "medium", reasons
    reasons.append("— the top symbol doesn't carry most of the query's terms")
    return "low", reasons


def _interpret(results, domains, anchor) -> str:
    """A shallow, explicitly-heuristic paraphrase (no LLM): what the top match suggests +
    the domains involved. Lives in the HEURISTIC block; it is a guess by construction."""
    bits: List[str] = []
    summ = (anchor.summary if anchor is not None else None) or (
        results[0].summary if results else None)
    if summ:
        bits.append(f"likely about {summ.rstrip('.')}")
    if domains:
        bits.append("domains: " + ", ".join(d for d, _ in domains))
    return "; ".join(bits) or "no confident interpretation — see candidates below"


def _call_flow(retriever, results) -> List[Tuple[str, str]]:
    """Who-calls-whom WITHIN the heuristic result set. The node SET is a guess (semantic
    search), but each arrow is a graph-resolved call edge — so this belongs in the
    heuristic block with that exact caveat. Computed from the hits we already pulled +
    one GraphIndex, never a second search (token budget is the edge)."""
    from pandemonium.graph import GraphIndex, _callees_of, _resolve_target

    refs = {r.ref for r in results if r.ref}
    if len(refs) < 2:
        return []
    try:
        idx = GraphIndex(retriever.sqlite, retriever.repo_id)
    except Exception:
        return []
    edges: List[Tuple[str, str]] = []
    seen: set = set()
    for r in results:
        if not r.qualified_name:
            continue
        target = _resolve_target(idx, r.path, r.qualified_name)
        if target is None:
            continue
        callees, _ = _callees_of(retriever.sqlite, idx, target)
        for c in callees:
            key = (r.ref, c["ref"])
            if c["ref"] in refs and c["ref"] != r.ref and key not in seen:
                seen.add(key)
                edges.append(key)
    return edges[:20]


def _heuristic_block(results, assessment: dict, retriever, anchor) -> dict:
    domain_count: dict = {}
    for r in results:
        for d in (r.tags or {}).get("domain", []):
            domain_count[d] = domain_count.get(d, 0) + 1
    domains = sorted(domain_count.items(), key=lambda kv: -kv[1])[:6]
    likely = [{"ref": r.ref or f"{r.path}:{r.start_line}-{r.end_line}",
               "scope": r.scope or r.chunk_type, "score": r.score,
               "summary": r.summary or "", "reason": r.reason or ""}
              for r in results[:5]]
    return {"interpretation": _interpret(results, domains, anchor),
            "domains": domains, "likely_targets": likely,
            "assessment": assessment, "call_flow": _call_flow(retriever, results)}


def _partition_tests(plan: dict) -> Tuple[List[str], List[str]]:
    """Split the plan's tests into VERIFIED (a confident caller lives in the test file) vs
    NAME-MATCHED-ONLY (a guess). Compared at FILE granularity on purpose: caller refs are
    `path::qname` and find_tests names are bare `path`, so a naive ref-equality filter
    would silently never subtract — leaving a verified test ALSO labelled unverified. One
    definition, consumed for both the verified block and the unverified ⚠ section."""
    from pandemonium.retrieval.tests_finder import is_test_path

    callers = plan.get("callers_direct", []) + plan.get("callers_transitive", [])
    caller_tests = [r for r in callers if is_test_path(_ref_path(r))]
    caller_paths = {_ref_path(r) for r in caller_tests}
    name_only = [t for t in plan.get("tests", []) if _ref_path(t) not in caller_paths]
    return caller_tests, name_only


def _verified_block(settings, plan: dict, anchor, caller_tests: List[str]) -> dict:
    """Graph facts about the anchor — and ONLY the verified ones. `callers_direct` /
    `callers_transitive` are confidently resolved (impact is conservative by design);
    name-matched-only tests are partitioned out (they live in the unverified section).
    Staleness re-validates the anchor + every affected file against the live index."""
    from pandemonium.graph import _split_prod_test
    from pandemonium import service

    prod, test = _split_prod_test(plan["callers_direct"])
    paths = list(dict.fromkeys([_ref_path(anchor.ref)] + plan.get("affected_files", [])))
    rows = service.staleness(settings, paths)
    stale_paths = [s["path"] for s in rows if s["stale"]]
    return {"anchor": plan["ref"], "type": plan["type"],
            "edges_available": plan.get("edges_available", True),
            "callers_production": prod, "callers_test": test,
            "callers_transitive": plan["callers_transitive"],
            "verified_tests": caller_tests,
            "dependencies": plan.get("dependencies", []),
            "risks": plan.get("risks", []),
            "affected_files": plan.get("affected_files", []),
            "stale_paths": stale_paths, "stale_total": len(rows)}


def _suggested_action(label: str, anchored: bool, anchor, results, anchor_stale: bool) -> str:
    if anchored:
        stale = (" The anchor's file is STALE — repo_reindex_changed before relying on this."
                 if anchor_stale else "")
        if label == "high":
            return ("Edit-ready. Fetch the anchor + direct callers + tests in the order "
                    f"below, then edit. For *possible* (unverified) callers, run "
                    f"repo_impact({anchor.ref}).{stale}")
        return (f"Likely target is {anchor.ref}, but confirm it first (repo_get / "
                "repo_symbol) before relying on the impact below — those facts are about "
                f"that symbol, not proof it's the right one.{stale}")
    if anchor is not None:
        return ("Target not confidently identified — verified impact is WITHHELD to avoid "
                "anchoring on a guess. Disambiguate: repo_search a more distinctive term, "
                f"or repo_get({anchor.ref}) to check the top candidate, then "
                "repo_edit_plan(<chosen ref>).")
    if results:
        return ("No symbol could be anchored (top hits are files/blocks). Pick a candidate "
                "above, repo_get it to confirm, then repo_edit_plan(<ref>).")
    return ("Nothing matched — the repo may be unindexed (repo_reindex_changed) or the "
            "task needs rephrasing with a more distinctive term.")


def repo_brief(settings, task: str, retriever=None, graph=None) -> dict:
    """Compose a trust-separated brief for a freeform task. See module docstring."""
    from pandemonium.graph import edit_plan
    from pandemonium.retrieval.hybrid_search import Retriever

    own = retriever is None
    retriever = retriever or Retriever(settings)
    try:
        results, assessment = retriever.search_assessed(task)
        # Match result_terms' granularity: split each (stopword-filtered) query token the
        # SAME way result_terms does (camelCase/snake), or a compound term like
        # `update_transform` can never intersect the anchor's split terms. Stopwords stay
        # stripped (query_terms first), so the coverage denominator isn't padded with noise.
        # RESIDUAL (Step-2-rooted, documented): confidence.assess + rerank_by_coverage carry
        # this SAME asymmetry (un-split query_terms vs split result_terms) and are gate-locked,
        # so a compound term that ALSO trips a name-cluster still yields assess=low BEFORE this
        # coverage runs → the brief withholds. That C++ case folds into the owed C++ retrieval
        # fixture (see RESULTS.md Step 2), not a brief-layer override of the tuned detector.
        qterms = confidence.query_terms(task)
        qset = set().union(*(confidence.terms_of(t) for t in qterms)) if qterms else set()

        anchor, idx = _pick_anchor(results)
        # Margin to the next ANCHORABLE symbol (a file/block card isn't a rival anchor; on
        # the fanout path raw score and list position are decoupled, so scan forward).
        nxt = (next((r for r in results[idx + 1:] if _is_symbol_card(r)), None)
               if anchor is not None else None)
        margin = round(anchor.score - nxt.score, 4) if (anchor is not None and nxt) else 0.0
        # Exact-name collision count: N symbols sharing the queried name make an "exact
        # match" ambiguous, not a confident anchor — without this the withhold fork is
        # defeated (an arbitrary one of N gets a confident "Edit-ready").
        exact_collisions = (
            sum(1 for r in results
                if anchor is not None and r.symbol_name
                and r.symbol_name == anchor.symbol_name)
            if assessment.get("reason") == "exact symbol match" else 1)

        plan = edit_plan(settings, anchor.ref, graph=graph) if anchor is not None else None
        if anchor is not None and plan is None:
            label, reasons = "low", ["the top candidate didn't resolve in the call graph"]
        else:
            edges_avail = bool(plan.get("edges_available", True)) if plan else True
            label, reasons = _anchor_confidence(assessment, anchor, qset, margin,
                                                edges_avail, exact_collisions)
        anchored = (label in _ANCHORED) and plan is not None

        verified: Optional[dict] = None
        unverified_tests: List[str] = []
        anchor_stale = False
        if anchored:
            caller_tests, unverified_tests = _partition_tests(plan)
            verified = _verified_block(settings, plan, anchor, caller_tests)
            anchor_stale = _ref_path(anchor.ref) in verified["stale_paths"]
            # Never present clean HIGH "verified" facts computed against a STALE index —
            # same reliability class as the no-edges withhold, so cap the confidence.
            if label == "high" and anchor_stale:
                label = "medium"
                reasons = ["the anchor's file changed since indexing — reindex; the impact "
                           "below may be stale"] + reasons

        fetch_order = (plan["fetch_order"] if (anchored and plan)
                       else [r.ref for r in results[:5] if r.ref])
        return {
            "task": task,
            "anchor": anchor.ref if anchor is not None else None,
            "anchor_confidence": label,
            "anchor_confidence_reasons": reasons,
            "anchored": anchored,
            "heuristic": _heuristic_block(results, assessment, retriever, anchor),
            "verified": verified,
            "unverified_tests": unverified_tests,
            "fetch_order": fetch_order,
            "suggested_action": _suggested_action(label, anchored, anchor, results,
                                                  anchor_stale),
        }
    finally:
        if own:
            retriever.close()


# ---------------------------------------------------------------------------
# Render — hard structural separation of HEURISTIC (guesses) and VERIFIED (graph).
# ---------------------------------------------------------------------------
def _short(ref: str) -> str:
    from pandemonium.graph import _short_ref
    return _short_ref(ref)


def render_brief(b: dict) -> str:
    if not b:
        return "Could not build a brief."
    out = [f"# Brief: {b['task']}", ""]
    out.append(f"**Anchor confidence: {b['anchor_confidence'].upper()}** — "
               + "; ".join(b["anchor_confidence_reasons"]))
    if b["anchored"]:
        out.append(f"Likely target (guess): `{b['anchor']}`")

    # --- HEURISTIC block (guesses) -----------------------------------------
    h = b["heuristic"]
    out.append("\n## ⚠ Heuristic — interpretation & likely targets "
               "(GUESSES — verify before trusting)")
    out.append(f"\n**Interpretation:** {h['interpretation']}")
    a = h.get("assessment") or {}
    if a.get("confidence") == "low":
        miss = ", ".join(a.get("missing_terms") or [])
        line = f"\n⚠ Low-confidence retrieval: {a.get('reason', '')}".rstrip()
        if miss:
            line += f"\n  Query term(s) no top hit covers: {miss}."
        out.append(line)
    out.append(f"\n### Likely targets — ranked guesses ({len(h['likely_targets'])})")
    if h["likely_targets"]:
        for i, t in enumerate(h["likely_targets"], 1):
            out.append(f"{i}. ref={t['ref']} [{t['scope']}] score={t['score']}")
            if t["summary"]:
                out.append(f"   {t['summary']}")
    else:
        out.append("- (no candidates — try repo_reindex_changed or a distinctive term)")
    if h["domains"]:
        out.append("\n**Domains involved:** "
                   + ", ".join(f"{d} ({n})" for d, n in h["domains"]))
    if h["call_flow"]:
        out.append(f"\n### Likely call flow ({len(h['call_flow'])}) — symbols chosen "
                   "heuristically; arrows are graph-resolved")
        out += [f"- {_short(x)} -> {_short(y)}" for x, y in h["call_flow"]]

    # --- VERIFIED block (graph facts about the anchor) ---------------------
    out.append("\n## ✓ Verified — graph facts about the anchor "
               "(NOT proof it's the right target)")
    v = b["verified"]
    if v is None:
        out.append("\n_Verified impact WITHHELD: the target isn't confidently identified, "
                   "so no caller/impact analysis is shown — a verified block on a guessed-"
                   "wrong symbol would be a confident lie. Disambiguate first (see next "
                   "action)._")
    else:
        out.append(f"\nAnchor: `{v['anchor']}` [{v['type']}]")
        if _ref_path(v["anchor"]) in v["stale_paths"]:
            out.append("\n> ⚠ The anchor's file changed since indexing — these graph facts "
                       "are computed against a STALE index; reindex before trusting them.")
        if not v["edges_available"]:
            out.append("\n> This language has no extracted graph edges — the caller/impact "
                       "facts below are NOT reliable; treat them as a floor, not a ceiling.")
        out.append("\n### Impact — confidently-resolved callers")
        if v["callers_production"]:
            out.append(f"**Production ({len(v['callers_production'])}):**")
            out += [f"- {r}" for r in v["callers_production"][:15]]
        if v["callers_test"]:
            out.append(f"**Test ({len(v['callers_test'])}):**")
            out += [f"- {r}" for r in v["callers_test"][:10]]
        if not v["callers_production"] and not v["callers_test"]:
            out.append("- (none confidently resolved) — an empty list means no *confident* "
                       "caller edge, NOT proof that nothing depends on it.")
        if v["callers_transitive"]:
            out.append(f"\n**Transitive ({len(v['callers_transitive'])}):**")
            out += [f"- {r}" for r in v["callers_transitive"][:10]]
        if v["verified_tests"]:
            out.append("\n### Tests proven related (a confident caller lives in them) "
                       f"({len(v['verified_tests'])})")
            out += [f"- {t}" for t in v["verified_tests"][:10]]
        if v["dependencies"]:
            out.append(f"\n### Depends on — callees ({len(v['dependencies'])})")
            out += [f"- {d}" for d in v["dependencies"][:12]]
        out.append("\n### Staleness (re-validated vs the live index)")
        if v["stale_paths"]:
            out.append(f"- ⚠ {len(v['stale_paths'])}/{v['stale_total']} relevant file(s) "
                       "changed since indexing — reindex before trusting refs: "
                       + ", ".join(v["stale_paths"][:6]))
        else:
            out.append(f"- all {v['stale_total']} relevant file(s) current.")
        if v["risks"]:
            out.append("\n### Risks")
            out += [f"- {r}" for r in v["risks"]]

    # --- unverified addendum (kept OUTSIDE the verified block on purpose) --
    if b.get("unverified_tests"):
        out.append("\n## ⚠ Unverified — name-matched tests (NOT graph-confirmed; "
                   "check relevance)")
        out += [f"- {t}" for t in b["unverified_tests"][:10]]

    # --- action + fetch order ---------------------------------------------
    out.append(f"\n## Suggested next action\n{b['suggested_action']}")
    if b["fetch_order"]:
        out.append("\n### Fetch order")
        out += [f"{i}. {r}" for i, r in enumerate(b["fetch_order"], 1)]
    return "\n".join(out)
