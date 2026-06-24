# PandemoniumProtocol — Roadmap (v2, trust-first)

**Created:** 2026-06-20 · **Revised:** 2026-06-20 (trust-before-synthesis reorder)
**Predecessor:** `IMPROVEMENTS.md` (v1 — all 8 ranked fixes + foot-guns **DONE**; C++ #1/#5,
C# generics/enums/delegates, viz perf landed).

## Governing principle (above all else)

> **Build the trust primitives before the synthesis.** Every synthesis output —
> `repo_brief`, task modes, session-resume narrative, next-move hints — must (a) carry its
> **own confidence** and (b) **visually separate verified graph-facts from heuristic
> guesses**. Otherwise it makes Claude edit with *more confidence and the same ignorance* —
> the exact opposite of the thesis.

Why this governs the order: every "smart" feature is a **synthesis layer that amplifies
whatever is underneath**. The one measured failure (T2: `"cell size"` confidently returned
`.size()` accessors) was synthesis-of-garbage. A `repo_brief` on that same semantic layer
doesn't fix it — it dresses it in authoritative prose ("Likely targets: …") that the agent
anchors on. **A confident-wrong brief is worse than no brief.** So eval + confidence-gating
+ staleness discipline move *ahead* of brief/modes. This is just the repo's existing
verify-not-trust culture made into a build gate.

## Thesis & product shape

Claude Code = editor/executor. Pandemonium = local context-intelligence layer that **makes
Claude Code edit with less ignorance.** Shape: **A + C + D** (MCP companion + pre-flight
brief + task-specialized *tool-use policies*). **NOT** a standalone coding agent.

## Guardrails (non-goals — correct, keep)

- **No standalone agent loop** (plan→edit→shell→patch→test). Claude owns that.
- **Cards/signatures/impact first; full bodies only after narrowing.** The edge is token
  economy; `search+get` *lost* to grep on token count — the compact win came from `impact`
  (graph), not retrieval. Encode it.
- **grep stays the baseline** — but the rule is "grep wins when you know the exact
  **distinctive** token." Correction from T3: grep floods even on a *known* token if it's
  short / overloaded / a substring (`bleed`, `pulse`→`impulse`). Distinctive, not just exact.

---

## Build sequence (trust before synthesis)

| # | Step | Items | Why here |
|---|---|---|---|
| **0** | SKILL decision rule + impact-first guidance (free, today) | #6 #7 #17 #19 | converts an already-measured win into behavior at zero build cost |
| **1** | Tiny eval matrix + caller-graph regression assertion | #11 + **M1** | nothing below can be tuned without it; the failure we saw was a tuning/intent failure |
| **2** | Query-rewrite + confidence-gate + auto-fanout (ONE feature) | #4 #5 | fixes the measured worst failure; the prerequisite trust primitive for search |
| **3** | Edge provenance / confidence (tiered: flag inline, evidence on ask) | #9 | the trust primitive for the graph |
| **4** | Next-move hints (terse, confidence-conditional) | #8 | cheap; rides on #2/#3 confidence |
| **5** | `repo_brief` — verified-vs-guess **hard-separated**, on top of 2–4 | #1 | the capstone, not step 1 |
| **6** | 2–3 context modes (impact, discovery, bugfix), tuned vs the eval set | #2 #18 | modes change *ranking*, not the graph |
| **7** | Session resume with airtight staleness | #10 | highest value, highest risk |
| **8** | C++ header/cpp merge (this repo) · .NET DI (when targeting .NET) | #13 #12 | sequence by target-repo mix |

The change from the v1 order: **eval harness + trust-gating move ahead of brief/modes**, and
"task agents" (#3) disappears as a separate build (folded into SKILL recipes / `--mode`).

---

## Items

### Step 0 — guidance (free)  `[S]`
- **#7 SKILL decision rules:** impact/edit_plan when editing a known symbol; brief/
  context_pack to start; search for vague discovery; symbol for exact ids; get only after
  narrowing; **grep only for a known *distinctive* exact token**. Prefer impact/plan over
  search+get; signatures/cards before bodies.
- **#6 impact-first:** make `repo_impact`/`repo_edit_plan` the documented default before an
  edit (measured: T1 impact 184 tok / quality 95 vs grep 528 tok / quality 75, and it found
  a *guarding test that calls the caller* — grep can't). Builds on `.claude/skills/pandemonium/`.

### Step 1 — eval harness + regression  `[M]`  (PREREQUISITE, not validation)
- **#11 eval matrix:** {this C++ repo · one .NET · one TS} × {known-symbol impact · vague
  discovery · bug trace · feature · API refactor · test selection}. Repo-agnostic ≠
  repo-blind. Every N=1 claim in our test needs N≥3 before scoring work. Builds on `evals/`.
  - **STATUS 2026-06-24 — deterministic-retrieval half BUILT.** `evals/run_eval.py --matrix`
    over three vendored fixtures (`evals/fixtures/matrix/{cpp_app,dotnet_app,ts_app}`) scores
    all 6 task types per language and gates against `evals/matrix_baseline.json` (a fresh
    real-model index per fixture; deterministic). Still pending: the agent-level token/error
    A/B (the M3 half, extends `qa_ab_runner.py`) and large *external* repos via `--tasks`.
- **M1 caller-graph regression assertion:** standing check that `impact(computeStepFromVelocity)`
  on SomeStrategyGame contains its real SIMD steppers / `runMovementScalar` — the
  nested-namespace edge-drop broke this silently for months; lock it so a future protocol
  update can't regress it unnoticed.

### Step 2 — search trust primitive  `[M]`  (highest engineering value)
- **#4+#5 as ONE feature:** detect low-confidence results (top hits clustered on one symbol
  family + low query↔result domain-term overlap — exactly the `.size()` signature), then
  **auto-fan-out** rewritten sub-queries and re-rank (this is what the manual T2 retry did —
  it found the rescale contract). **NOT a hardcoded ban-list** (`size`/`get`/… is sometimes
  the real domain term → mis-fires). Surface a confidence signal + the rewrite. **Keep the
  bare-identifier fast path** — never rewrite a single known symbol (composes with the
  exact-short-circuit already shipped). Builds on `symbol_search._STOPWORDS` (seed only) +
  `hybrid_search.Retriever.search`.

### Step 3 — graph trust primitive  `[M]`
- **#9 edge provenance:** each edge carries `confidence` + an evidence list (receiver
  resolved as `this`, target in same class, language scope matched, call span line N; for
  possible: name match / receiver unknown / N overloads). **Tiered:** flag confidence
  inline, full evidence on request. So Claude distinguishes resolved vs possible vs
  text-match vs stale. Builds on `graph.py` edges (already store receiver+confidence + the
  confident/possible tiers).

### Step 4 — next-move hints  `[S]`
- **#8:** 1–2 next actions per result, **confidence-conditional, never a 5-item menu** (it
  bloats the token budget that #17 says is the whole edge). Builds on `_format_results`
  (already emits `reason` + a fetch hint).

### Step 5 — `repo_brief`  `[M]`  (capstone)
- **#1:** one entrypoint returning task interpretation, likely targets, call flow, impact,
  tests, risks, staleness, fetch order, suggested Claude action. **Hard requirement:**
  visually separate the **verified** block (Impact / callers / tests — from the graph →
  trust) from the **heuristic** block (Interpretation / likely targets — label as guess).
  Builds on `repo_edit_plan` (~70%) + `repo_logic_map` (call flow). Ship before #2–#4 and
  it's a confident liar.

### Step 6 — task modes  `[M]`
- **#2+#18:** start with **2–3** (`impact`, `discovery`, `bugfix`) — not an 8-mode buffet.
  Each changes **ranking weights**, not the graph. `impact` mostly re-ranks toward tools
  that already exist (`repo_impact`/`edit_plan`). Per-mode weights **calibrated against
  #11's eval set**, never hand-waved. Builds on `context_packer.py` (today task-agnostic).
  - **As shipped (2026-06-20):** the mechanism + 3 presets landed, but #11 doesn't exist, so
    presets ship **principled-but-unvalidated (labelled hypotheses)**, NOT calibrated — only
    `discovery` has single-type evidence. Faithful calibration is deferred to the #11
    crossover matrix. See `evals/RESULTS.md` "Context modes".

### Step 7 — session resume  `[M]`  (highest value AND highest risk)
- **#10:** readable resume — last task, symbols inspected, files fetched, confirmed facts,
  rejected paths, risks, **files changed since last session**, recommended next. **Danger:**
  a narrative asserting "deaths are deferred until `World::processDeaths`" states a fact that
  may be stale. Its value *evaporates* without airtight staleness: it **must re-validate
  `confirmed_facts` against the current index and tag each verified-now vs believed-then.**
  Builds on `session.py` (persists to `.pandemonium/sessions/<id>.json`; gaps: `mcp-<pid>`
  id = no cross-restart resume, no narrative, notes not symbol-anchored).
  **Ownership:** session ledger = scoped *investigation state*; Claude memory = durable
  *cross-project facts*. Keep them distinct.

### Step 8 — language correctness (by target mix)  `[L]`
- **#13 C++ header↔cpp merge — SHIPPED (2026-06-20):** declared in `.hpp`, defined in
  `.cpp` — the classic `World::queueDeath` case the nested-namespace fix did **not** cover.
  Built as a doc-comment MERGE onto the `.cpp` definition keyed by canonical qualified_name
  (`tree_sitter_parser.cpp_decl_docs` + `index_runner._collect_header_docs`); declarations are
  NEVER emitted as symbols, so `resolve_call`/`by_name` are unchanged (the constraint).
  The spec's "alternate decl-site" landed too as `Symbol.decl_ref` (surfaced by `repo_get`),
  per the user's call to include it now. Gated by `indexing.cpp_header_merge`. Measured with
  the real model (`run_eval.py --cppmerge`: buried out-of-line def rank 2→0 ON); deterministic
  lock `tests/test_cpp_header_merge.py`; no-op on the gate (all hard metrics flat). Known
  residual: SILENT cross-file staleness when ONLY the header changes (see `evals/RESULTS.md`
  "Step 8") — a header→cpp dependency reindex is deliberately out of scope.
- **#12 .NET DI mapping:** `IUserService → UserService`, registrations in
  `Program.cs`/`Startup.cs`, controller/route usage. In .NET, DI *is* the call graph —
  without it the graph is fiction. Highest-value for the .NET world specifically.
- **#14 HTML/CSS coupling surface** (humble scope — id/class/selector/event/template
  references, "what breaks if I change this class") and **#15 focused viz overlays**
  (blast-radius/ego-network + stale + prod/test) — lowest priority; sequence last.

---

## Cross-cutting requirements (the parts that keep synthesis honest)

- **M1 — caller-graph regression** (in #11): see Step 1.
- **M2 — bake "grep-to-confirm" into the tool.** The skill currently outsources verifying
  unverified callers to the agent's discipline. Close the loop: a low-confidence edge offers
  a **one-shot exact-text confirmation** itself, rather than relying on Claude to remember.
- **M3 — A/B acceptance gate for every synthesis feature.** A brief/mode is accepted only if
  it lowers **total task tokens AND error rate** vs the no-synthesis baseline — not because
  it "looks comprehensive." Wire the context-bytes-vs-ground-truth-quality methodology from
  our test into #11 so every brief/mode change is judged on measured net token + quality.
- **M4 — operational robustness.** The MCP toolset **vanished this session** when the
  protocol was restarted/reinstalled and didn't re-register (observed). None of this matters
  if the tools drop from the agent's registry on every upgrade. Needs a **version handshake /
  graceful reconnect / health check** so an upgrade doesn't silently disarm the agent.

## Folded / downgraded

- **#3 task-agents → fold.** Right framing (policies, not autonomous agents) — but each is
  just a fixed tool-sequence playbook = a SKILL recipe or a `--mode` preset. The "Impact
  agent" flow is five lines in SKILL.md. Do **not** build an agent framework. Fold into
  #1/#2/#7.
- **#15 focused viz — lowest priority** (real, but last).

## Success metrics & acceptance

| Metric | Goal |
|---|---|
| Token / file-read reduction | 50–90% fewer irrelevant tokens; fewer full-file reads |
| Caller accuracy | real callers (incl. guarding tests) found before edit |
| First-attempt edit success | more tasks correct on first try |
| Session continuity | less repeated rediscovery (with staleness honesty) |
| Staleness / confidence honesty | every synthesis output tagged verified vs guess |

**Acceptance rule (M3):** a synthesis feature ships only when the eval matrix shows it nets
**fewer total task tokens + lower error rate** than the baseline. Confident prose that isn't
measured to *reduce ignorance* is rejected — that is the governing principle, enforced.
