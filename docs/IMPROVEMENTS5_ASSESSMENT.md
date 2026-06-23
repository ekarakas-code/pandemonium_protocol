# Improvements5 — Alignment Assessment

**Date:** 2026-06-23 · **Method:** every recommendation in `docs/Improvements5.txt` verified
against *actual code* (file:line) via three parallel reads of the graph layer, the MCP/synthesis
tools, and the eval harness — then cross-checked against the canonical **`ROADMAP.md` (v2,
trust-first)** and the sibling precedent `docs/IMPROVEMENTS3_ASSESSMENT.md`. Buckets and
statuses below are the post-verification verdict, not a read of the doc.

---

## Verdict (front-loaded)

**The thesis is the mission, almost verbatim. The plan is a snapshot behind the code.**

Improvements5's closing principle —

> `repo_map` gives orientation · `repo_graph` gives verified structural facts ·
> `repo_logic_map` gives typed workflow flows · `repo_impact` gives change consequences ·
> `repo_context_pack` gives minimal complete evidence. The LLM should not infer relation
> truth from a pretty map; it should receive **typed relations with evidence and confidence**,
> then verify with exact code before editing.

— **is** PandemoniumProtocol's stated mission and its governing build principle (ROADMAP v2:
"build the trust primitives before the synthesis; every synthesis output must carry its own
confidence and visually separate verified graph-facts from heuristic guesses"). So this is a
**third external review written without knowledge of the project's own empirical, trust-first
roadmap** — the same situation as Improvements3. Followed naively it re-walks ground the
project already mapped; mined selectively it yields ~3 genuinely useful items.

> **Right thesis, snapshot-stale plan.** Most of the "what I would add" list is already shipped
> or already owned by ROADMAP v2; a couple of its *current-state* claims are simply
> out-of-date; and its single biggest new idea — data/config/event/framework edges — is the
> exact capability the project **architected as the `affects` layer and consciously left
> dormant**, and the most precision-threatening thing to build *ahead of* the eval instrument.

---

## Are the suggestions correct? (the question, answered directly)

Verified against the code, "correct" splits cleanly in two:

### A. Where Improvements5 describes the CURRENT state — several claims are stale/incorrect

The doc predates ROADMAP v2's trust-first reorganisation, so it recommends adding things that
already exist:

- **"The map just says *related* / ambiguity is silently chosen or omitted."** **Incorrect.**
  Every resolved edge carries a numeric confidence and an evidence string; callers are split
  into confident vs `callers_possible`; there is an explicit *"Possible callers (unverified —
  grep to confirm)"* section and an *"Ambiguous calls (low confidence, name-collision)"*
  section, plus a one-shot grep-to-confirm on every unverified edge
  (`graph.py:856-888, 1020-1046, 808-813`). (Tiny residual: names matching **zero** candidates
  are dropped silently — the one place the doc's concern half-lands.)
- **"Add evidence / confidence to every relation."** **Already present.** `resolve_call`
  returns `(symbol_id, confidence, ambiguous, evidence)`; receiver-aware tiers live in
  `_CALL_CONF` (`graph.py:32-35, 687-740`).
- **"Separate verified dependency from semantic similarity / LLM hypothesis."** **Already
  done.** Vector neighbours are labeled *"Similar implementations — suggestive, not verified"*;
  `affects` is `origin="llm_inferred"`; and `repo_brief` **hard-separates** a ✓Verified block
  (callers/tests/impact from the graph) from a ⚠Heuristic block (interpretation/likely
  targets), and **withholds** the verified block entirely at low confidence
  (`brief.py:1-34, 195-215, 328-424`). *A confident-wrong brief is worse than no brief* is the
  project's literal governing rule — this is the single closest match between the doc's ask and
  shipped code.

### B. Where Improvements5 proposes forward work — directionally correct, but unproven

By the project's own **M3 acceptance rule**, none of these may be called an "improvement" until
the eval matrix shows **fewer total task tokens + lower error rate** than the baseline.
*However* — unlike the Improvements3 grab-bag — relation work is **better-grounded**: the
relation graph is where the project measured its one concrete win over grep ("a guarding test
that calls the caller — grep can't," IMPROVEMENTS.md). So these are worth taking seriously,
**provided they are gated**, not dismissed with a blanket "quality is the wrong axis."

**Net:** most of the doc is correct-but-already-true; a couple of current-state claims are
factually off; ~3 items are genuinely net-new (below).

---

## Bucket table (verified against code)

| # | Improvements5 item | Status | Where it lives / why |
|---|---|---|---|
| 1 | Confidence **ladder** (verified_static / verified_framework / heuristic / semantic / llm_hypothesis / unknown_or_ambiguous) | **Substance EXISTS, naming differs** | Numeric confidence (0.35–0.9 keyed to receiver kind) + confident/possible/ambiguous split + `similar_to` (vector) + `affects` (llm_inferred). No *categorical* 6-class enum. The two missing labels map to features that are **absent** (`verified_framework`) or **already labeled differently** (`heuristic` = the name-matched test edges). `graph.py:32-35, 687-740, 856-888` |
| 2 | Evidence on every relation | **PARTIAL** | Evidence is a resolution *reason* string computed at query time ("receiver 'self' → caller's class X"), **not** a `file:line` call-span ref. A modest, genuine gap. `graph.py:50-60, 821-827, 989-997` |
| 3 | `repo_relation_pack(ref, task, budget)` | **REDUNDANT** | The capability is distributed across `repo_graph` + `repo_impact` + `repo_edit_plan` + `repo_brief` + `repo_context_pack`. Mostly repackaging, not new capability. `graph.py:929-1164`, `brief.py`, `retrieval/context_packer.py` |
| 4 | Typed **flows** in `repo_logic_map` (entrypoint→controller→service→domain→repo→side-effect→tests, edges labeled) | **NEW + on-mission** | Today returns relevant symbols + domain tags + confident-call *connections within the search-hit set* only — **no entrypoint concept, no multi-hop chaining, no edge-type labels.** Partly gated on framework edges (#5). `graph.py:1310-1367` |
| 5 | Data / config / event / **framework** edges (`reads_config`, `publishes_event`, `route_to_handler`, DI, `uses_template`, …) | **ABSENT — the dual-edged center** | Simultaneously the genuine net-new payload AND the recommendation most in tension with trust-first. This is precisely the territory the `affects` layer was **architected for and deliberately left dormant** pending a produce→ingest workflow + trust labeling. These edges are low-confidence by nature, so building them ahead of the eval is the exact failure ROADMAP v2 guards against. `graph.py:747-795` (no caller of `ingest_affects`); `ARCHITECTURE.md:76-80` (verbatim DORMANT note) |
| 6 | Make ambiguity explicit | **EXISTS** | `callers_possible`, "Ambiguous calls", high-collision suppression, grep-to-confirm. `graph.py:836-888, 808-813` |
| 7 | "What to inspect next" before editing | **PARTIAL** | `repo_edit_plan` gives a ranked fetch order + risks; per-card next-move hints exist. Missing the full 7-item checklist (side-effect callees, base class/interface, config/event contracts, similar implementations, recently-changed). `graph.py:1245-1307`, `mcp/tools.py:58-91` |
| — | Relation/**edge evals** (20–50 tasks, edge P@k / R@k, "agent found correct edit site") | **PARTIAL → the payload** | Only the M1 caller-graph regression lock + `impact_fp/fn` on **2** hand-authored symbols. No 20–50-task edge gold, no edge precision/recall. `gold.py:71-88`, `run_eval.py:41-56`, `tests/test_caller_graph_regression.py` |

**Counts:** already-true / redundant **4** (#1 substance, #3, #6, + the "add confidence/evidence/
separation" current-state asks) · new + on-mission **2** (#4 typed flows, edge-evals) · new +
in-tension **1** (#5 framework/data/event edges) · partial genuine gaps **2** (#2 span-evidence,
#7 inspect-checklist).

---

## The dual-edged center: #5 framework / data / event edges

This is Improvements5's "probably the biggest missing upgrade," and it is the crux of whether
the doc is "suitable." Two facts settle it:

1. **It is not a new idea here — it is a deferred one.** The `affects` layer exists: schema,
   storage, an `ingest_affects(settings, shard_paths)` writer, read paths in
   `repo_graph`/`repo_edit_plan`/`repo_brief`, evidence + staleness tracking. It is **dormant
   by deliberate decision** — `ingest_affects` has **no caller anywhere in the codebase** (verified
   by search), and `ARCHITECTURE.md:76-80` states verbatim: *"`affects` is a DORMANT capability …
   not wired to a CLI command or MCP tool, and no skill teaches an agent to produce shards …
   Treat `affects` as experimental/out-of-band until a first-class produce→ingest workflow is
   built; the read code is guarded and inert until then."* Improvements5's framework/data/event
   taxonomy is exactly what that layer was built to carry.
2. **These edges are low-confidence by nature**, so shipping them ahead of the trust instrument
   is the precise failure ROADMAP v2 was rewritten to prevent ("synthesis amplifies whatever is
   underneath; a confident-wrong brief is worse than no brief"). The correct sequence is
   **eval instrument → trust-labeled produce→ingest workflow → framework extractors**, not the
   reverse.

So #5 is **correct in spirit, wrong in sequencing**: confirmation that the deferred `affects`
hook is the right place for this, *not* a green light to build framework extractors now.

## Keep the edge-eval distinction sharp

Improvements5's "relation evals" ask is **not** satisfied by this branch's
(`evals/channel-baselines`) work. That branch added `--baselines` (channel-isolation:
hybrid vs vector-only vs BM25-only vs symbol-only) — a **retrieval-ranking** instrument.
Improvements5 asks for **edge/relation correctness**: precision@k / recall@k over 20–50 tasks
with hand-authored expected related refs. Today the only edge-correctness signal is the M1
regression test + `impact_fp/fn` on **2** symbols. The 20–50-task edge set is the real gap, it
maps cleanly onto **ROADMAP Step 1 (#11 eval matrix + M1)**, and it is the same conclusion the
Improvements3 assessment reached (close the measurement gap first) — which strengthens it.

---

## Bottom line / recommended action

- **Adopt now (highest confidence): the relation/edge eval set.** Expand the edge gold from 2
  → ~20–30 grep-derived cases with edge precision/recall + a regression gate, reusing
  `gold.IMPACT_GOLD`, `run_eval._impact_fp_fn`, the `--tasks` loader, and
  `test_caller_graph_regression.py`. Cheap, mission-critical, and the acceptance instrument for
  everything else in the doc. **[Build started 2026-06-23 — see ROADMAP Step 1 / `evals/`.]**
- **Queue as on-mission: #4 typed flows** in `repo_logic_map` — but it is partly gated on #5,
  so sequence it after the eval set and after the `affects` produce→ingest workflow.
- **Defer behind the gate: #5 framework/data/event edges** — build the trust-labeled
  produce→ingest workflow on the existing dormant `affects` layer first; only then add
  framework extractors, scoped to the repos actually targeted (C++/.NET per ROADMAP Step 8),
  not a framework buffet.
- **Take as small genuine gaps: #2** (promote edge evidence from reason-string to a `file:line`
  call-span) and **#7** (extend `repo_edit_plan`'s inspect-checklist) — low-risk, ride existing
  tools, inherit the gate.
- **Treat as confirmation, not new work: #1, #3, #6**, and the "add confidence/evidence/verified-
  vs-guess separation" asks — already shipped; the doc validates the direction.

> Improvements5 is **suitable as validation of the project's direction and as a source of ~3
> items** — chiefly the edge-eval instrument. It is **not suitable as a plan to follow
> step-by-step**: done naively it would rebuild shipped trust primitives and push the
> highest-risk synthesis (framework/data/event edges) *ahead of* the very instrument that would
> tell us whether it helps — the exact inversion ROADMAP v2 exists to prevent.
