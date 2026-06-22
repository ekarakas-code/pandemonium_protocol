# Improvements3 — Alignment Assessment

**Date:** 2026-06-22 · **Method:** every one of the 22 areas in `docs/Improvements3.txt`
verified against *actual code* (file:line) in two passes — a grounded review, then an
adversarial re-check of every "already shipped" claim. Buckets and statuses below are the
post-verification verdict, not a read of the docs.

---

## Verdict (front-loaded)

**The thesis fits the mission perfectly. The plan does not.**

Improvements3 is a **second, *external* review written without knowledge of the project's
first, *empirical* one.** `IMPROVEMENTS.md` (v1, all done) and `ROADMAP.md` (v2) exist
because the system **measured itself losing to grep** on impact-mapping tasks and
reorganized around one governing principle:

> Build **trust primitives before synthesis.** Every synthesis output must carry its own
> confidence and visually separate **verified graph-facts** from **heuristic guesses**.
> *A confident-wrong brief is worse than no brief.*

Improvements3 has **none of that scar tissue.** Its energy is "build more packs / cards /
modes / metrics / a learning loop" — which is largely **already built**, and where it isn't,
it leans toward exactly the *synthesis-without-a-confidence-gate* that ROADMAP v2 was
rewritten to prevent. So the honest verdict is **not "redundant"** — it's:

> **Right thesis, snapshot-stale plan.** Followed naively it pushes toward the project's
> *known* failure mode. Mined selectively, it yields ~2–3 genuinely useful items the current
> roadmap doesn't already own.

**The numbers (22 areas, post-verification):**

| | count | |
|---|---|---|
| **Bucket: redundant** (already shipped or already in ROADMAP v2) | **14** | the bulk — *evidence*, not the payload |
| **Bucket: new + on-mission** | **2** | #16 deepen test-intel, #17 patch/diff pack |
| **Bucket: new + off-mission / in-tension** | **2** | #15 deep-Python, #22 quality-loop |
| **Bucket: meta** (positioning / architecture / priorities) | **4** | #18 #19 #20 #21 |
| Verified status: shipped **2** · partial **15** · absent **4** · meta **1** | | |

The "partial" count is high for an honest reason: Improvements3 enumerates *field-level*
detail (e.g. "each unit carries `git_commit`, `mtime`, `decorators`, `calls`, `called_by`")
that the code substitutes or stores elsewhere. The **headline** of almost every redundant
item is done; the **enumerated sub-fields** often aren't. That's calibration, not a gap in
the mission.

---

## Bucket 1 — Already shipped / redundant (14) — *the evidence*

These confirm the project is **well past** the MVP Improvements3 assumes it's reviewing.

| # | Area | Verified | Where it lives |
|---|---|---|---|
| #2 | chunks → code units | partial | `chunker.py:48-67` one chunk per symbol; `tree_sitter_parser.py:23-31`; `models.py:28-83`; `sqlite_store.py:58-95`. *Gap: no `git_commit`/`mtime` (uses content-hash), no `route/test/fixture` kinds, `calls/called_by` are graph edges not unit fields.* |
| #7 | symbol cards + file cards | partial | `models.py:86-109` "a search hit IS a card"; `mcp/tools.py:70-89` renders ref+summary+tags, **no raw code** → "fetch via repo_get". *Gap: card lacks `inputs/outputs/raises/external_deps/change_frequency`.* |
| #3 | relationship graph | partial | `graph.py` EDGE_SPECS (Py/C++/C#/JS/TS) + Dart; `resolve_call` carries **confidence + evidence**; callers split confident vs `callers_possible`; vector-similar labeled "suggestive, not verified". **Goes further than #3 asked on trust.** *Gap: only `calls/imports/inherits` stored; `raises/returns/decorates/references/configured_by` absent; `affects` edge dormant.* |
| #12 | context-expansion rules | partial | `repo_graph`/`repo_edit_plan` expand over confidence-tagged edges (on-demand tool, not auto-injected bodies — deliberate). |
| #4 | staged hybrid retrieval | partial | `hybrid_search.py` multi-channel + weighted merge + dedup; `confidence.assess` + `_fanout` is the trust-gated rerank slice. *Unbuilt (deliberately): cross-encoder rerank, broad auto-expansion — vector dominance is resisted.* |
| #11 | "why this result?" | partial | `SearchResult.reason` + `channel_scores`; `_reason()` label. *Gap: single label, not a list; no graph-derived reasons.* Respects trust (transparency). |
| #6 | context-pack **schema** + "why each item" | **shipped** | `context_packer.py` fixed sections + per-file reason; `repo_brief` separates heuristic vs verified blocks. One of two clean wins. |
| #13 | deliberate token-budget packing | partial | greedy budget fill in `context_packer.py`. *Gap: no explicit card→sig→body→file tiers/allocation %.* |
| #14 | "missing/weak context" detection | partial | `confidence.assess` (missing_terms + low/high verdict); `repo_brief` **withholds** the verified block at low confidence. **This IS the trust thesis.** |
| #8 | freshness / staleness | partial | `repo_changed`/`repo_reindex_changed`, content-hash staleness; brief demotes confidence + prepends stale warning. *Gap (deliberate): git-commit anchoring / `--diff HEAD~1` deferred in favor of git-free hashing.* |
| #10 | MCP = agent-intention tools | partial | 15 intention-level tools; single gated write tool (`repo_reindex_changed`). *Gap: MCP **resources** + **prompts** not exposed (tools only).* |
| #1 | context-pack quality as core metric | partial | `evals/` gold sets + `run_eval.py` gate. *Gap: ContextBench-style recall/precision/efficiency@budget not formalized as the headline metric.* |
| #9 | local eval harness vs baselines | partial | `gold.py` (15 labeled queries) + `run_eval.py --gate/--sweep` + `ab_runner.py` (grep vs hybrid). **Real gap: no vector-only / BM25-only channel-isolation arms** (0/4 retrieval baselines; only 2/4 at the agent layer); tasks live in Python modules, not `tasks.yaml`. |
| #5 | task-aware packs | **absent** | Only **3 ranking-weight modes** (`--mode` impact/discovery/bugfix) that change *ranking weights only, never sections* (`settings.py:94-109`). Improvements3's `--task` packs (6 types, different *packing strategy*) do **not** exist. Shipped slice is narrower and ships as "labelled hypotheses" pending #11 eval. |

---

## Bucket 2 — Genuinely new + on-mission (2) — *the payload*

These are the items worth actually extracting. **Both compose on existing trust-gated tools,
so a faithful build inherits the confidence/verified-vs-guess separation rather than
bypassing it.**

- **#17 — Patch / diff context pack** *(absent — verified by search → build candidate).* A
  `--diff` / `--changed` pack returning **changed symbols → affected callers → affected tests
  → risk**. Today `repo_changed` returns *staleness only* (`mcp/tools.py:343-351`,
  stale-vs-current), `repo_impact` takes a *single ref* not a diff set
  (`mcp/tools.py:307-313`), and `repo_context_pack` takes a *task string* with no diff input
  (`mcp/tools.py:243-246`); there is **no `--diff`/review/patch CLI command**, and
  `indexer/tracker.py:5` states "real `git diff` integration is a later phase." So nothing
  composes the pieces into a review/repair pack — confirmed absent, not inferred. (The
  built-in `/review` + `security-review` skills are generic Claude Code commands, not a
  pandemonium feature.) **On-mission:** it's diff-driven *analysis*, not a plan→edit→patch
  agent loop; it rides `repo_impact` (already tiers confident vs "possible — grep to confirm"
  callers) + `service.staleness`, so it **inherits the gate**. The single clearest "new, fits
  mission, not yet owned" item.

- **#16 — Deepen test intelligence to first-class units** *(partial → enhance).* Today
  tested-by is a **name-match FTS heuristic**, correctly labeled "name-matched — confirm
  relevance" (`graph.py:1040`). First-class test units (fixtures used, target symbols,
  parametrize cases, mocks) would make bugfix/review packs materially better. **Caveat that
  keeps it on-mission:** it must preserve that *verified-vs-guess labeling* — a real
  `tested_by` edge can be promoted to verified; an inferred one stays labeled.

> Adjacent sharpening (lives in Bucket 1 but worth pulling forward): **#9's missing
> channel-isolation baselines** (vector-only, BM25-only). The harness exists; adding these
> two arms is cheap and is *the trust instrument itself* — it's how every future synthesis
> feature earns its place under M3.

---

## Bucket 3 — New but off-mission / in tension (2) — *handle with care*

- **#22 — "Context-pack quality loop" / experience memory** *(absent — the conflict item).*
  As written it **re-ranks from self-reported agent outcomes + `human_rating` + "patch
  passed" with no verified-vs-guess separation and no confidence gate.** That is *precisely*
  the failure ROADMAP's governing principle guards against ("synthesis amplifies whatever is
  underneath"; a ranking silently bent by unverified self-reports becomes confidently-wrong
  and **compounds across sessions**). ROADMAP v2 already **defers** this and draws the line:
  session ledger = scoped *investigation state*; Claude memory = durable *cross-project
  facts* — **neither is a ranking-learning loop.** *Only* safe if every stored outcome
  carries provenance + is re-validated against the live index (the same staleness discipline
  as session-resume). Aligned-but-deferred, not free.

- **#15 — Deep Python-framework intelligence** (FastAPI/Django/SQLAlchemy/pytest extractors)
  *(absent → decline as framed).* Crosses no stated non-goal, but pulls toward **deep
  single-language specialization exactly when the project deliberately went multi-language
  with C++ as the proven primary target** (IMPROVEMENTS.md benchmarks; ROADMAP v2 Step 8
  C++/.NET). Per-framework extractors are also high per-version maintenance. The on-mission
  slice is a *language-neutral* richer "entrypoints" classifier — already seeded in
  `tags.py:28-34` — not the FastAPI/Django/SQLAlchemy depth.

---

## Bucket 4 — Meta (4): positioning / architecture / priorities

- **#18 positioning ("context compiler", not "local RAG")** — already substantially the
  project's stance (`README` leads with "codebase intelligence" + "context pack"). Naming nit.
- **#20 target architecture** — **matches the shipped architecture** (verified). Only
  divergences are *deliberate*: graph is a post-retrieval relationship layer, not a retrieval
  channel; "rerank" = weighted merge + heuristic fanout, not a cross-encoder.
- **#21 ContextPack as the core object** — already the project's framing; the trust gates
  (confidence, verified-vs-guess) carry over.
- **#19 P1–P6 priority list** — **superseded** by the project's own empirically-derived,
  trust-first ROADMAP v2 ordering. Do not adopt over it.

---

## The two gates, summarized

1. **Crosses a stated non-goal?** — Essentially **none do** outright. The watch-items are the
   *un-built sub-pieces*: #4's cross-encoder/broad-expansion (→ vector dominance) and #12's
   "auto-inject neighbor bodies" (→ "too much loosely-related context", risk #21). The
   project's choice to keep expansion **tool-driven, not auto-injected** is the guard.
2. **Adds synthesis without a confidence gate?** — Be even-handed: Improvements3 *partially
   respects* trust (#11 why-this-result, #14 missing-context, #1/#9 metrics are all
   trust-positive). The **one clear violation is #22**, which learns from unverified
   self-reports. That's the line.

## Will these actually improve performance / quality? (the measured reality)

Applying the project's **own** M3 rule to *these* recommendations: **not provably — and the
existing measurements say "quality" is the wrong axis to expect movement on.**

- **`evals/AB_RESULTS.md` (9 seeded-bug tasks, 18 paired runs, vanilla Claude vs protocol):
  correctness is already a tie — 18/18 both arms.** The protocol's measured win is **token /
  cost economy**, not correctness: mean −14,424 tokens / −$0.012, and it **splits by bug
  locality** — *pricier* on grep-obvious one-liners (fixed MCP/orient overhead), ~2× *cheaper*
  on the cross-file 800-line `graph.py` bug ($0.44→$0.21). The repo admits this 118-file
  corpus is "close to the protocol's worst case … and it still broke even"; the edge is
  predicted to **scale with codebase size**, a regime barely tested.
- **`evals/RESULTS.md`:** the *retrieval-rank* gains are real and measured (descriptor embed
  P@1 +13; enrichment symbol-P@5 +27, 5 queries better / 0 worse) — but the gold is **100%
  Python, this-repo, n=15, discovery-shaped**, and the large-codebase / semantic-discovery
  win is stated as an explicit **hypothesis the static harness cannot verify**.

So for the three "adopt/enhance" items the honest grading is:
- **#9 (baselines + a large-repo matrix) is the only one defensible as "will improve" — and
  what it improves is your ability to *know*.** Right now the harness cannot see the regime
  where the tool actually wins. That is the binding constraint on answering this very question.
- **#17 (diff/patch pack): unproven, with a specific risk** — it composes on `repo_impact`,
  whose measured *weakness* was exact line precision (the judges' decisive complaint in
  IMPROVEMENTS.md Benchmark 2). Review/blast-radius may play to its *strength* instead, but
  that's a new, unmeasured regime. Build thin, gate on M3, measure — don't assume.
- **#16 (first-class test units): partial support, real cost** — graph/test relationships
  have measured value ("a guarding test that calls the caller — grep can't"), but the cheap
  measured win was the **graph**, not richer indexing; first-class test parsing adds synthesis
  surface for an unmeasured marginal gain over the existing labeled name-match.

**Mission-faithful conclusion:** by the project's own discipline, claiming any of these
"improves quality" before the eval matrix shows fewer tokens + lower error would be exactly
the "confident prose not measured to reduce ignorance" that ROADMAP v2 rejects. The
highest-leverage next move is **closing the measurement gap (#9 + a large-repo task set)** —
both to answer this question and because the *measured* failures already have owners in
ROADMAP v2 (Step 1 eval matrix, Step 2 confidence-gated search). Improvements3's net-new
items are speculative additions on top of that, not substitutes for it.

## Bottom line / recommended action

- **Adopt first (highest confidence): #9** — vector-only / BM25-only baseline arms **and a
  large-repo / cross-file task set**. It's cheap, it's the acceptance instrument, and it's the
  only item the evidence lets you call an "improvement" outright.
  - **[Implemented 2026-06-22]** `run_eval.py --baselines` (hybrid vs symbol/keyword/vector-only)
    + `--tasks <PATH>` external task loader + `evals/tasks.example.json`. Proven non-regressive
    (`--perquery` byte-identical, 162/162 pytest). **Retrieval half only** — first finding:
    vector-only beats hybrid on the discovery gold (P@5 .733 vs .600). The **token-at-scale
    half** (large-repo seeded-bug AB tasks in `ab_runner`) is named but **not built** — see
    `evals/RESULTS.md` "#9". Do NOT read the retrieval table as closing the scaling question.
- **Build thin + gate, don't assume: #17** (patch/diff pack) — strongest *net-new* capability,
  but unproven; accept only if it nets fewer task tokens + no quality regression under M3.
- **Enhance carefully:** #16 first-class test units, *keeping the name-matched-vs-verified
  label*.
- **Defer with a gate:** #22 — only behind provenance + live-index re-validation.
- **Decline as framed:** #15 — multi-language direction is deliberate; take only the
  language-neutral entrypoints slice.
- **Ignore as superseded:** #19 priorities; treat #18/#20/#21 as confirmation, not new work.

> Improvements3's closing line — *"compile the repo into compact, version-aware working
> memory before the agent arrives"* — **is** the mission, almost verbatim. It validates the
> direction. It just describes a place the project already reached, and would, if followed
> step-by-step, re-walk ground that the **measured** ROADMAP v2 already mapped more honestly.
